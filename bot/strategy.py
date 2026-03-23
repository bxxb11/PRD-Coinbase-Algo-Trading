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


# ── SuperTrend ────────────────────────────────────────────────────────────────

def generate_supertrend_signal(
    df: pd.DataFrame,
    atr_period: int = 10,
    atr_multiplier: float = 3.0,
) -> Signal:
    """
    SuperTrend trend-flip signal (aggressive — no pullback wait).

    BUY:  SuperTrend flips bearish → bullish on current bar
    SELL: SuperTrend flips bullish → bearish on current bar
    HOLD: trend unchanged or insufficient data

    The ratcheting band logic is O(n) single pass over the input df.
    Minimum bars: atr_period + 2
    """
    min_bars = atr_period + 2
    if len(df) < min_bars:
        return Signal.HOLD

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    hl2 = (df["high"] + df["low"]) / 2.0

    basic_upper = (hl2 + atr_multiplier * atr).to_numpy()
    basic_lower = (hl2 - atr_multiplier * atr).to_numpy()
    close_arr   = df["close"].to_numpy()

    n = len(df)
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    supertrend  = [0] * n

    for i in range(1, n):
        if basic_upper[i] < final_upper[i - 1] or close_arr[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if basic_lower[i] > final_lower[i - 1] or close_arr[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        prev_st = supertrend[i - 1] if supertrend[i - 1] != 0 else 1
        if prev_st == 1:
            supertrend[i] = -1 if close_arr[i] < final_lower[i] else 1
        else:
            supertrend[i] =  1 if close_arr[i] > final_upper[i] else -1

    if supertrend[-1] == 0 or supertrend[-2] == 0:
        return Signal.HOLD
    if supertrend[-2] == -1 and supertrend[-1] == 1:
        return Signal.BUY
    if supertrend[-2] == 1 and supertrend[-1] == -1:
        return Signal.SELL
    return Signal.HOLD


# ── Donchian Channel + ADX ────────────────────────────────────────────────────

def _compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder-smoothed ADX. Pure pandas, no TA-Lib."""
    alpha      = 1.0 / period
    prev_high  = df["high"].shift(1)
    prev_low   = df["low"].shift(1)
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    h_diff   = df["high"] - prev_high
    l_diff   = prev_low   - df["low"]
    plus_dm  = h_diff.where((h_diff > l_diff) & (h_diff > 0), 0.0)
    minus_dm = l_diff.where((l_diff > h_diff) & (l_diff > 0), 0.0)

    smooth_tr    = tr.ewm(alpha=alpha, adjust=False).mean()
    smooth_plus  = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    tr_safe  = smooth_tr.replace(0, float("nan"))
    plus_di  = 100.0 * smooth_plus  / tr_safe
    minus_di = 100.0 * smooth_minus / tr_safe
    di_sum   = (plus_di + minus_di).replace(0, float("nan"))
    dx       = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, adjust=False).mean()


def generate_donchian_adx_signal(
    df: pd.DataFrame,
    dc_enter_bars: int = 20,
    dc_exit_bars: int = 10,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
) -> Signal:
    """
    Donchian Channel Breakout with ADX consolidation filter.

    BUY:  close > highest close of prior dc_enter_bars bars
          AND ADX < adx_threshold  (breakout from consolidation)
    SELL: close < lowest close of prior dc_exit_bars bars
    HOLD: no breakout, or ADX too high (already trending = chasing)

    Shift(1) on channels prevents lookahead bias.
    Minimum bars: dc_enter_bars + adx_period + 2
    """
    min_bars = dc_enter_bars + adx_period + 2
    if len(df) < min_bars:
        return Signal.HOLD

    close = df["close"]
    dc_high = close.shift(1).rolling(window=dc_enter_bars).max()
    dc_low  = close.shift(1).rolling(window=dc_exit_bars).min()
    adx     = _compute_adx(df, adx_period)

    curr_close = close.iloc[-1]
    curr_high  = dc_high.iloc[-1]
    curr_low   = dc_low.iloc[-1]
    curr_adx   = adx.iloc[-1]

    if pd.isna(curr_high) or pd.isna(curr_low) or pd.isna(curr_adx):
        return Signal.HOLD

    if curr_close > curr_high and curr_adx < adx_threshold:
        return Signal.BUY
    if curr_close < curr_low:
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

    if name == "supertrend":
        return generate_supertrend_signal(
            df,
            atr_period=getattr(config, "atr_period", 10),
            atr_multiplier=getattr(config, "atr_multiplier", 3.0),
        )

    if name == "donchian_adx":
        return generate_donchian_adx_signal(
            df,
            dc_enter_bars=getattr(config, "dc_enter_bars", 20),
            dc_exit_bars=getattr(config, "dc_exit_bars", 10),
            adx_period=getattr(config, "adx_period", 14),
            adx_threshold=getattr(config, "adx_threshold", 25.0),
        )

    # Default: ma_crossover
    return generate_signal(df, config.ma_short_period, config.ma_long_period)
