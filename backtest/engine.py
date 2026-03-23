"""
BacktestEngine — vectorised simulation loop.

Instead of recomputing indicators on a growing sliding window each bar
(O(n²)), we precompute the full indicator series once (O(n)) and walk
bar-by-bar reading pre-calculated values. pandas rolling/ewm are causal
by default so there is zero lookahead bias.

Key design decisions:
- Indicators precomputed once on full DataFrame → fast even for 8 760+ bars
- Fills at close price of signal bar (standard backtest assumption)
- Reuses bot/risk_manager sub-functions directly (bypasses evaluate_trade()
  to avoid its SQLite write on PAUSED)
- Signal deduplication mirrors trading_loop.py exactly
- Trade sizing uses MA spread formula; falls back to min_size for non-MA strats
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from bot.strategy import Signal, compute_trade_size
from bot.risk_manager import (
    check_bot_status,
    check_drawdown,
    check_position_limit,
    RiskDecision,
)
from backtest.state_sim import (
    make_initial_state,
    apply_buy,
    apply_sell,
    update_peak_equity,
    apply_paused,
)
from backtest.metrics import compute_metrics, BacktestMetrics

_TAKER_FEE_RATE = 0.006


# ── Vectorised strategy callable type ─────────────────────────────────────────
# Takes full DataFrame + params → returns Series of Signal values (index-aligned).
# The engine precomputes this once, then reads row by row.
VectorStrategyFn = Callable[[pd.DataFrame, dict], pd.Series]


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    bar_index: int
    timestamp: pd.Timestamp
    side: str                       # "BUY" | "SELL"
    size_usd: float
    size_qty: float
    fill_price: float               # close price of that bar
    fee_usd: float
    equity_after: float
    position_usd_after: float
    risk_rejection_reason: Optional[str] = None


@dataclass
class BacktestResult:
    trades: list
    equity_curve: list
    equity_timestamps: list
    rejected_trades: list
    params: dict
    initial_equity: float
    final_equity: float
    metrics: BacktestMetrics
    stopped_early: bool = False
    stop_reason: Optional[str] = None


# ── Engine ─────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Replay historical OHLCV data through a vectorised strategy and the
    existing risk controls.

    Usage:
        from backtest.strategies import macd_signals, DEFAULT_PARAMS
        engine = BacktestEngine(
            df=historical_df,
            strategy_fn=macd_signals,
            params=DEFAULT_PARAMS["macd"],
        )
        result = engine.run()
    """

    def __init__(
        self,
        df: pd.DataFrame,
        strategy_fn: VectorStrategyFn,
        params: dict,
        initial_equity: float = 1000.0,
        max_position_usd: float = 200.0,
        max_drawdown_percent: float = 25.0,
        min_trade_size_usd: float = 1.0,
        max_trade_size_usd: float = 20.0,
        size_spread_min_pct: float = 0.1,
        size_spread_max_pct: float = 0.5,
        fee_rate: float = _TAKER_FEE_RATE,
        warmup_bars: Optional[int] = None,
    ) -> None:
        self._df = df.reset_index(drop=True)
        self._strategy_fn = strategy_fn
        self._params = params
        self._initial_equity = initial_equity
        self._max_position_usd = max_position_usd
        self._max_drawdown_percent = max_drawdown_percent
        self._min_trade_size_usd = min_trade_size_usd
        self._max_trade_size_usd = max_trade_size_usd
        self._size_spread_min_pct = size_spread_min_pct
        self._size_spread_max_pct = size_spread_max_pct
        self._fee_rate = fee_rate

        long_period = max(
            params.get("long_period", 0),
            params.get("ema_long", 0),
            params.get("slow", 0) + params.get("signal_period", 0),
            params.get("rsi_period", 0),
        )
        self._warmup_bars = warmup_bars if warmup_bars is not None else max(long_period, 10) + 5

    # ── Public ─────────────────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        """
        Precompute indicator series for the full DataFrame, then walk bar-by-bar
        applying dedup, risk checks, and in-memory fills.
        """
        df = self._df
        n = len(df)

        if n < self._warmup_bars + 2:
            raise ValueError(
                f"DataFrame has only {n} rows; need at least {self._warmup_bars + 2}."
            )

        # ── Step 1: Precompute ALL indicators once (O(n)) ──────────────────────
        enriched_df = self._strategy_fn(df, self._params)   # adds indicator columns
        signal_series: pd.Series = enriched_df["_signal"]   # Signal enum per row

        # Precompute MA columns for trade sizing (always MA20/50, fallback safe)
        size_df = self._precompute_ma_for_sizing(df)

        # ── Step 2: Initialise simulation state ────────────────────────────────
        state = make_initial_state(self._initial_equity)
        equity_curve: list[float] = [self._initial_equity] * self._warmup_bars
        equity_timestamps: list = list(df["timestamp"].iloc[: self._warmup_bars])
        trades: list[TradeRecord] = []
        rejected_trades: list[TradeRecord] = []

        # ── Step 3: Bar-by-bar walk ────────────────────────────────────────────
        for i in range(self._warmup_bars, n):
            row = df.iloc[i]
            current_price = float(row["close"])
            current_ts = row["timestamp"]
            signal: Signal = signal_series.iloc[i]

            # ── Mark-to-market: re-price held BTC at this bar's close ──────
            # Total portfolio value = cash + BTC_qty × close_price
            # This means the equity curve reflects real P&L as BTC moves,
            # not just fee deductions.
            mtm = round(state["cash_usd"] + state["position_qty"] * current_price, 2)
            state = {**state, "current_equity_usd": mtm}
            state = update_peak_equity(state)

            # HOLD path
            if signal == Signal.HOLD:
                equity_curve.append(state["current_equity_usd"])
                equity_timestamps.append(current_ts)
                continue

            # Dedup — mirrors trading_loop.py exactly
            if signal.value == state["last_signal"]:
                equity_curve.append(state["current_equity_usd"])
                equity_timestamps.append(current_ts)
                continue

            # Trade sizing
            size_usd = self._read_trade_size(size_df, i)

            # Risk evaluation (pure — no DB writes)
            decision, reason = self._evaluate_risk(signal, state, size_usd)

            if decision == RiskDecision.PAUSED:
                state = apply_paused(state, reason)
                equity_curve.append(state["current_equity_usd"])
                equity_timestamps.append(current_ts)
                metrics = compute_metrics(
                    trades, equity_curve, equity_timestamps,
                    self._initial_equity, state["current_equity_usd"],
                )
                return BacktestResult(
                    trades=trades,
                    equity_curve=equity_curve,
                    equity_timestamps=equity_timestamps,
                    rejected_trades=rejected_trades,
                    params=self._params,
                    initial_equity=self._initial_equity,
                    final_equity=state["current_equity_usd"],
                    metrics=metrics,
                    stopped_early=True,
                    stop_reason=reason,
                )

            if decision == RiskDecision.REJECTED:
                rejected_trades.append(TradeRecord(
                    bar_index=i, timestamp=current_ts, side=signal.value,
                    size_usd=size_usd, size_qty=0.0, fill_price=current_price,
                    fee_usd=0.0, equity_after=state["current_equity_usd"],
                    position_usd_after=state["position_usd"],
                    risk_rejection_reason=reason,
                ))
                equity_curve.append(state["current_equity_usd"])
                equity_timestamps.append(current_ts)
                continue

            # Execute (in-memory)
            size_qty = round(size_usd / current_price, 8) if current_price > 0 else 0.0
            fee_usd = round(size_usd * self._fee_rate, 2)

            if signal == Signal.BUY:
                state = apply_buy(state, size_usd, size_qty, self._fee_rate)
            else:
                state = apply_sell(state, size_usd, size_qty, self._fee_rate)

            # Post-trade MTM (cash changed but price unchanged within same bar)
            mtm = round(state["cash_usd"] + state["position_qty"] * current_price, 2)
            state = {**state, "current_equity_usd": mtm}
            state = update_peak_equity(state)

            trades.append(TradeRecord(
                bar_index=i, timestamp=current_ts, side=signal.value,
                size_usd=size_usd, size_qty=size_qty, fill_price=current_price,
                fee_usd=fee_usd, equity_after=state["current_equity_usd"],
                position_usd_after=state["position_usd"],
            ))
            equity_curve.append(state["current_equity_usd"])
            equity_timestamps.append(current_ts)

        # ── End of loop ────────────────────────────────────────────────────────
        final_equity = state["current_equity_usd"]
        metrics = compute_metrics(
            trades, equity_curve, equity_timestamps,
            self._initial_equity, final_equity,
        )
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            equity_timestamps=equity_timestamps,
            rejected_trades=rejected_trades,
            params=self._params,
            initial_equity=self._initial_equity,
            final_equity=final_equity,
            metrics=metrics,
            stopped_early=False,
        )

    # ── Private ────────────────────────────────────────────────────────────────

    def _evaluate_risk(self, signal: Signal, state: dict, trade_size_usd: float) -> tuple:
        status_result = check_bot_status(state["status"])
        if status_result.decision != RiskDecision.APPROVED:
            return status_result.decision, status_result.reason

        dd_result = check_drawdown(
            state["current_equity_usd"], state["peak_equity_usd"], self._max_drawdown_percent,
        )
        if dd_result.decision in (RiskDecision.PAUSED, RiskDecision.REJECTED):
            return dd_result.decision, dd_result.reason

        if signal == Signal.BUY:
            pos_result = check_position_limit(
                state["position_usd"], trade_size_usd, self._max_position_usd,
            )
            if pos_result.decision != RiskDecision.APPROVED:
                return pos_result.decision, pos_result.reason

        return RiskDecision.APPROVED, "All risk checks passed"

    def _precompute_ma_for_sizing(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add ma_short / ma_long columns using params (or defaults 20/50).
        Used for compute_trade_size() regardless of active strategy.
        """
        short = self._params.get("short_period", 20)
        long_ = self._params.get("long_period", 50)
        out = df.copy()
        out["ma_short"] = out["close"].rolling(window=short).mean()
        out["ma_long"] = out["close"].rolling(window=long_).mean()
        return out

    def _read_trade_size(self, size_df: pd.DataFrame, i: int) -> float:
        """Read pre-computed MA values at row i to size the trade."""
        try:
            return compute_trade_size(
                size_df.iloc[: i + 1],
                self._min_trade_size_usd,
                self._max_trade_size_usd,
                self._size_spread_min_pct,
                self._size_spread_max_pct,
            )
        except Exception:
            return self._min_trade_size_usd
