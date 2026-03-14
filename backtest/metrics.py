"""
Performance metric calculations for backtest results.
All functions are pure — no I/O, no side effects.
"""

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class BacktestMetrics:
    total_return_pct: float
    sharpe_ratio: Optional[float]   # None if insufficient data
    max_drawdown_pct: float
    win_rate_pct: float
    avg_win_usd: float
    avg_loss_usd: float             # positive number
    profit_factor: float            # sum_wins / sum_losses; inf if no losses
    total_trades: int
    trades_per_month: float
    buy_count: int
    sell_count: int
    net_pnl_usd: float
    total_fees_usd: float


def compute_metrics(
    trades: list,
    equity_curve: list,
    equity_timestamps: list,
    initial_equity: float,
    final_equity: float,
    risk_free_rate_annual: float = 0.05,
) -> BacktestMetrics:
    """
    Master metrics function. Called by BacktestEngine after run().

    Args:
        trades: list of TradeRecord (executed trades only, not rejected)
        equity_curve: per-bar equity snapshots (float list, same length as timestamps)
        equity_timestamps: per-bar pd.Timestamp list
        initial_equity: starting equity USD
        final_equity: ending equity USD
        risk_free_rate_annual: annualized risk-free rate for Sharpe (default 5%)
    """
    total_return_pct = _compute_total_return(initial_equity, final_equity)
    max_dd = _compute_max_drawdown(equity_curve)
    sharpe = _compute_sharpe(equity_curve, equity_timestamps, risk_free_rate_annual)

    buy_count = sum(1 for t in trades if t.side == "BUY")
    sell_count = sum(1 for t in trades if t.side == "SELL")
    total_fees = sum(t.fee_usd for t in trades)

    round_trips = _pair_round_trips(trades)
    win_loss = _compute_win_loss_stats(round_trips)

    trades_per_month = _compute_trades_per_month(trades, equity_timestamps)

    net_pnl = round(final_equity - initial_equity, 2)

    return BacktestMetrics(
        total_return_pct=round(total_return_pct, 4),
        sharpe_ratio=round(sharpe, 4) if sharpe is not None else None,
        max_drawdown_pct=round(max_dd, 4),
        win_rate_pct=round(win_loss["win_rate_pct"], 2),
        avg_win_usd=round(win_loss["avg_win_usd"], 2),
        avg_loss_usd=round(win_loss["avg_loss_usd"], 2),
        profit_factor=round(win_loss["profit_factor"], 4),
        total_trades=len(trades),
        trades_per_month=round(trades_per_month, 2),
        buy_count=buy_count,
        sell_count=sell_count,
        net_pnl_usd=net_pnl,
        total_fees_usd=round(total_fees, 2),
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _compute_total_return(initial: float, final: float) -> float:
    if initial <= 0:
        return 0.0
    return (final - initial) / initial * 100


def _compute_max_drawdown(equity_curve: list) -> float:
    """Walk the equity curve tracking running peak; return worst trough (%)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
    return max_dd


def _compute_sharpe(
    equity_curve: list,
    equity_timestamps: list,
    risk_free_rate_annual: float,
) -> Optional[float]:
    """
    Annualized Sharpe ratio from per-bar log returns.
    Returns None if fewer than 30 bars (unreliable).
    """
    if len(equity_curve) < 30:
        return None

    # Infer bars per year from timestamp spacing
    bars_per_year = _infer_bars_per_year(equity_timestamps)

    prices = pd.Series(equity_curve, dtype=float)
    log_returns = prices.pct_change().dropna()

    if log_returns.std() == 0:
        return None

    rf_per_bar = (1 + risk_free_rate_annual) ** (1 / bars_per_year) - 1
    excess = log_returns - rf_per_bar

    sharpe = excess.mean() / excess.std() * math.sqrt(bars_per_year)
    return sharpe


def _infer_bars_per_year(timestamps: list) -> float:
    """Estimate bars per year from the median gap between timestamps."""
    if len(timestamps) < 2:
        return 8760.0  # default: 1h
    try:
        gaps = []
        sample = timestamps[1:min(50, len(timestamps))]
        for i, ts in enumerate(sample):
            prev = timestamps[i]
            gap_hours = (ts - prev).total_seconds() / 3600
            if gap_hours > 0:
                gaps.append(gap_hours)
        if not gaps:
            return 8760.0
        median_gap_hours = sorted(gaps)[len(gaps) // 2]
        return 8760.0 / median_gap_hours
    except Exception:
        return 8760.0


def _pair_round_trips(trades: list) -> list:
    """
    FIFO pairing of BUY → SELL trades to form round trips.
    Returns list of (buy_trade, sell_trade) tuples.
    Open positions (unmatched BUYs) at end are excluded.
    """
    pairs = []
    buy_queue = []
    for trade in trades:
        if trade.side == "BUY":
            buy_queue.append(trade)
        elif trade.side == "SELL" and buy_queue:
            buy = buy_queue.pop(0)
            pairs.append((buy, trade))
    return pairs


def _compute_win_loss_stats(round_trips: list) -> dict:
    """
    For each (buy, sell) pair compute net PnL:
        pnl = (sell.fill_price - buy.fill_price) * sell.size_qty
              - buy.fee_usd - sell.fee_usd
    Returns dict with win_rate_pct, avg_win_usd, avg_loss_usd, profit_factor.
    """
    if not round_trips:
        return {
            "win_rate_pct": 0.0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "profit_factor": 0.0,
        }

    wins = []
    losses = []
    for buy, sell in round_trips:
        pnl = (sell.fill_price - buy.fill_price) * sell.size_qty - buy.fee_usd - sell.fee_usd
        if pnl >= 0:
            wins.append(pnl)
        else:
            losses.append(abs(pnl))

    total = len(round_trips)
    win_rate = len(wins) / total * 100 if total > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    sum_wins = sum(wins)
    sum_losses = sum(losses)
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else float("inf")

    return {
        "win_rate_pct": win_rate,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "profit_factor": profit_factor,
    }


def _compute_trades_per_month(trades: list, equity_timestamps: list) -> float:
    if not trades or len(equity_timestamps) < 2:
        return 0.0
    try:
        start = equity_timestamps[0]
        end = equity_timestamps[-1]
        days = (end - start).total_seconds() / 86400
        months = days / 30.44
        if months <= 0:
            return 0.0
        return len(trades) / months
    except Exception:
        return 0.0
