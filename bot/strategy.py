"""
strategy.py — pure stateless signal computation and trade sizing.
No I/O, no exchange calls, no state reads.
All functions are independently testable with synthetic DataFrames.

Supported strategies (set via STRATEGY in .env):
  ma_crossover  — Simple MA golden/death cross (original)
  macd          — MACD histogram sign-change crossover
  ema_rsi       — EMA trend filter + RSI pullback entry (recommended)
"""

from enum import Enum
from typing import Optional

import pandas as pd


class Signal(Enum):
    BUY = "BUY"    # Entry signal
    SELL = "SELL"  # Exit signal
    HOLD = "HOLD"  # No action this tick


def compute_moving_averages(
    df: pd.DataFrame,
    short_period: int,
    long_period: int,
) -> pd.DataFrame:
    """
    Add 'ma_short' and 'ma_long' columns to a copy of df.
    Raises ValueError if df has fewer rows than long_period.
    """
    if len(df) < long_period:
        raise ValueError(
            f"Not enough candles: need {long_period}, got {len(df)}"
        )
    result = df.copy()
    result["ma_short"] = result["close"].rolling(window=short_period).mean()
    result["ma_long"] = result["close"].rolling(window=long_period).mean()
    return result


def detect_crossover(df: pd.DataFrame) -> Signal:
    """
    Examine the last two rows to detect a crossover event.
    Requires 'ma_short' and 'ma_long' columns (call compute_moving_averages first).

    Golden cross (BUY):  prev ma_short <= ma_long  AND  curr ma_short > ma_long
    Death cross (SELL):  prev ma_short >= ma_long  AND  curr ma_short < ma_long
    Otherwise: HOLD
    """
    if "ma_short" not in df.columns or "ma_long" not in df.columns:
        raise ValueError("DataFrame missing 'ma_short' or 'ma_long' columns")
    if len(df) < 2:
        raise ValueError("Need at least 2 rows to detect a crossover")

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    # Skip if any MA value is NaN (insufficient history)
    if any(pd.isna([prev["ma_short"], prev["ma_long"], curr["ma_short"], curr["ma_long"]])):
        return Signal.HOLD

    if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
        return Signal.BUY

    if prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
        return Signal.SELL

    return Signal.HOLD


def compute_trade_size(
    df: pd.DataFrame,
    min_size: float,
    max_size: float,
    spread_min_pct: float,
    spread_max_pct: float,
) -> float:
    """
    Compute a trade size between min_size and max_size based on the
    current MA spread strength (abs(MA20 - MA50) / MA50 * 100).

    Mapping:
      spread <= spread_min_pct  →  min_size  (weak signal)
      spread >= spread_max_pct  →  max_size  (strong signal)
      between                   →  linear interpolation

    Requires 'ma_short' and 'ma_long' columns in df.
    Returns a float rounded to 2 decimal places.
    """
    if "ma_short" not in df.columns or "ma_long" not in df.columns:
        raise ValueError("DataFrame missing 'ma_short' or 'ma_long' columns")

    latest = df.iloc[-1]
    ma_short = latest["ma_short"]
    ma_long = latest["ma_long"]

    if pd.isna(ma_short) or pd.isna(ma_long) or ma_long == 0:
        return min_size

    spread_pct = abs(ma_short - ma_long) / ma_long * 100

    # Normalize spread to [0, 1] within configured bounds
    spread_range = spread_max_pct - spread_min_pct
    if spread_range <= 0:
        return min_size

    normalized = (spread_pct - spread_min_pct) / spread_range
    normalized = max(0.0, min(1.0, normalized))

    trade_size = min_size + normalized * (max_size - min_size)
    return round(trade_size, 2)


def generate_signal(
    df: pd.DataFrame,
    short_period: int,
    long_period: int,
) -> Signal:
    """
    Convenience entry point: compute MAs then detect crossover.
    Returns a Signal enum value.
    """
    df_with_ma = compute_moving_averages(df, short_period, long_period)
    return detect_crossover(df_with_ma)


# ── MACD Crossover ────────────────────────────────────────────────────────────

def generate_macd_signal(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
    zero_filter: bool = True,
) -> Signal:
    """
    MACD histogram sign-change crossover.

    BUY:  histogram flips negative → positive (+ macd_line > 0 if zero_filter)
    SELL: histogram flips positive → negative (+ macd_line < 0 if zero_filter)
    HOLD: no flip, or insufficient data

    Args:
        df: OHLCV DataFrame with 'close' column, minimum slow+signal_period+1 rows.
        fast: Fast EMA period (default 12).
        slow: Slow EMA period (default 26).
        signal_period: Signal EMA period (default 9).
        zero_filter: Require MACD line above/below zero to reduce false signals.
    """
    min_bars = slow + signal_period + 1
    if len(df) < min_bars:
        return Signal.HOLD

    close = df["close"]
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram  = macd_line - signal_line

    if len(histogram) < 2:
        return Signal.HOLD

    prev_hist  = histogram.iloc[-2]
    curr_hist  = histogram.iloc[-1]
    curr_macd  = macd_line.iloc[-1]

    if pd.isna(prev_hist) or pd.isna(curr_hist) or pd.isna(curr_macd):
        return Signal.HOLD

    if prev_hist <= 0 and curr_hist > 0:
        if zero_filter and curr_macd <= 0:
            return Signal.HOLD
        return Signal.BUY

    if prev_hist >= 0 and curr_hist < 0:
        if zero_filter and curr_macd >= 0:
            return Signal.HOLD
        return Signal.SELL

    return Signal.HOLD


# ── EMA Trend + RSI Filter ────────────────────────────────────────────────────

def generate_ema_rsi_signal(
    df: pd.DataFrame,
    ema_short: int = 13,
    ema_long: int = 55,
    rsi_period: int = 21,
    rsi_buy_thresh: float = 45.0,
    rsi_sell_thresh: float = 55.0,
    trend_confirm_bars: int = 3,
) -> Signal:
    """
    EMA trend filter with RSI pullback entry (recommended strategy).

    BUY:  EMA_short > EMA_long for >= trend_confirm_bars AND
          RSI crossed up through rsi_buy_thresh (recovery from oversold)
    SELL: EMA_short < EMA_long for >= trend_confirm_bars AND
          RSI crossed down through rsi_sell_thresh (rejection from overbought)
    HOLD: trend not confirmed, or RSI condition not met

    Rationale: Waiting for a pullback within the trend improves fill price
    by 0.5–1.5%, partially offsetting the 1.2% round-trip fee.

    Args:
        df: OHLCV DataFrame with 'close' column.
        ema_short: Fast EMA period (default 13 — Fibonacci).
        ema_long: Slow EMA period (default 55 — Fibonacci).
        rsi_period: RSI look-back with Wilder smoothing (default 21).
        rsi_buy_thresh: RSI recovery level for BUY (default 45).
        rsi_sell_thresh: RSI rejection level for SELL (default 55).
        trend_confirm_bars: Consecutive bars of EMA alignment required (default 3).
    """
    min_bars = ema_long + rsi_period + trend_confirm_bars + 2
    if len(df) < min_bars:
        return Signal.HOLD

    close = df["close"]

    # EMAs
    ema_s = close.ewm(span=ema_short, adjust=False).mean()
    ema_l = close.ewm(span=ema_long,  adjust=False).mean()

    # RSI — Wilder smoothing (alpha = 1/period)
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1 / rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / rsi_period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    rsi      = 100 - (100 / (1 + rs))

    # Trend confirmation: EMA alignment for last confirm bars
    confirm = int(trend_confirm_bars)
    uptrend = all(
        ema_s.iloc[-(k + 1)] > ema_l.iloc[-(k + 1)]
        for k in range(confirm)
    )
    downtrend = all(
        ema_s.iloc[-(k + 1)] < ema_l.iloc[-(k + 1)]
        for k in range(confirm)
    )

    prev_rsi = rsi.iloc[-2]
    curr_rsi = rsi.iloc[-1]

    if pd.isna(prev_rsi) or pd.isna(curr_rsi):
        return Signal.HOLD

    rsi_buy_signal  = prev_rsi < rsi_buy_thresh  and curr_rsi >= rsi_buy_thresh
    rsi_sell_signal = prev_rsi > rsi_sell_thresh and curr_rsi <= rsi_sell_thresh

    if uptrend and rsi_buy_signal:
        return Signal.BUY
    if downtrend and rsi_sell_signal:
        return Signal.SELL

    return Signal.HOLD


# ── Strategy dispatcher ───────────────────────────────────────────────────────

def dispatch_strategy(df: pd.DataFrame, config) -> Signal:
    """
    Route to the correct strategy function based on config.strategy_name.
    Called once per tick in trading_loop.run_tick().

    Supported values for config.strategy_name:
        "ma_crossover"  — original MA golden/death cross
        "macd"          — MACD histogram crossover
        "ema_rsi"       — EMA trend + RSI pullback (recommended)
    """
    name = getattr(config, "strategy_name", "ma_crossover")

    if name == "macd":
        return generate_macd_signal(
            df,
            fast=getattr(config, "macd_fast", 12),
            slow=getattr(config, "macd_slow", 26),
            signal_period=getattr(config, "macd_signal_period", 9),
            zero_filter=getattr(config, "macd_zero_filter", True),
        )

    if name == "ema_rsi":
        return generate_ema_rsi_signal(
            df,
            ema_short=getattr(config, "ema_short", 13),
            ema_long=getattr(config, "ema_long", 55),
            rsi_period=getattr(config, "rsi_period", 21),
            rsi_buy_thresh=getattr(config, "rsi_buy_thresh", 45.0),
            rsi_sell_thresh=getattr(config, "rsi_sell_thresh", 55.0),
            trend_confirm_bars=getattr(config, "trend_confirm_bars", 3),
        )

    # Default: ma_crossover
    return generate_signal(df, config.ma_short_period, config.ma_long_period)
