"""
strategy.py — pure stateless signal computation and trade sizing.
No I/O, no exchange calls, no state reads.
All functions are independently testable with synthetic DataFrames.
"""

from enum import Enum

import pandas as pd


class Signal(Enum):
    BUY = "BUY"    # MA20 crossed ABOVE MA50 (golden cross)
    SELL = "SELL"  # MA20 crossed BELOW MA50 (death cross)
    HOLD = "HOLD"  # No new crossover this tick


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
