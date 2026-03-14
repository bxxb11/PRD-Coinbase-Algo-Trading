"""
Vectorised strategy functions for the backtesting engine.

Each function takes the FULL historical DataFrame + params and returns a
copy with all indicator columns added plus a `_signal` column (Series of
Signal enum values). pandas rolling/ewm are causal by default — no
lookahead bias.

Contract:
  Input:  df (full OHLCV DataFrame), params (dict)
  Output: DataFrame copy with indicator columns + '_signal' (Signal per row)
  Must never raise — return Signal.HOLD for bars with insufficient data
"""

import pandas as pd

from bot.strategy import Signal


# ── 1. MA Crossover (baseline — wraps existing bot/strategy.py logic) ─────────

def ma_crossover_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Golden/death cross on simple moving averages.
    params: short_period (int, default 20), long_period (int, default 50)
    """
    short = params.get("short_period", 20)
    long_ = params.get("long_period", 50)

    out = df.copy()
    out["ma_short"] = out["close"].rolling(window=short).mean()
    out["ma_long"] = out["close"].rolling(window=long_).mean()

    prev_short = out["ma_short"].shift(1)
    prev_long = out["ma_long"].shift(1)

    buy_mask  = (prev_short <= prev_long) & (out["ma_short"] > out["ma_long"])
    sell_mask = (prev_short >= prev_long) & (out["ma_short"] < out["ma_long"])

    signals = pd.Series(Signal.HOLD, index=out.index)
    signals[buy_mask]  = Signal.BUY
    signals[sell_mask] = Signal.SELL

    # NaN rows → HOLD
    nan_mask = out["ma_short"].isna() | out["ma_long"].isna()
    signals[nan_mask] = Signal.HOLD

    out["_signal"] = signals
    return out


# ── 2. MACD Crossover ─────────────────────────────────────────────────────────

def macd_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    MACD signal-line crossover via histogram sign change.
    params: fast (12), slow (26), signal_period (9), zero_filter (True)

    BUY:  histogram flips negative→positive (+ macd_line > 0 if zero_filter)
    SELL: histogram flips positive→negative (+ macd_line < 0 if zero_filter)
    """
    fast = params.get("fast", 12)
    slow = params.get("slow", 26)
    sig_p = params.get("signal_period", 9)
    zero_filter = params.get("zero_filter", True)

    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["macd_line"]   = ema_fast - ema_slow
    out["macd_signal"] = out["macd_line"].ewm(span=sig_p, adjust=False).mean()
    out["macd_hist"]   = out["macd_line"] - out["macd_signal"]

    prev_hist = out["macd_hist"].shift(1)
    curr_hist = out["macd_hist"]
    macd_line = out["macd_line"]

    bull_cross = (prev_hist <= 0) & (curr_hist > 0)
    bear_cross = (prev_hist >= 0) & (curr_hist < 0)

    if zero_filter:
        bull_cross = bull_cross & (macd_line > 0)
        bear_cross = bear_cross & (macd_line < 0)

    signals = pd.Series(Signal.HOLD, index=out.index)
    signals[bull_cross] = Signal.BUY
    signals[bear_cross] = Signal.SELL

    nan_mask = out["macd_hist"].isna() | prev_hist.isna()
    signals[nan_mask] = Signal.HOLD

    out["_signal"] = signals
    return out


# ── 3. EMA Trend + RSI Filter ─────────────────────────────────────────────────

def ema_rsi_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    EMA trend filter (EMA_short/EMA_long) with RSI pullback entry timing.
    params: ema_short (21), ema_long (55), rsi_period (14),
            rsi_buy_thresh (45), rsi_sell_thresh (55), trend_confirm_bars (3)

    BUY:  EMA_short > EMA_long for >= confirm bars AND RSI recovers above rsi_buy
    SELL: EMA_short < EMA_long for >= confirm bars AND RSI drops below rsi_sell
    """
    ema_s   = params.get("ema_short", 21)
    ema_l   = params.get("ema_long", 55)
    rsi_p   = params.get("rsi_period", 14)
    rsi_buy  = params.get("rsi_buy_thresh", 45.0)
    rsi_sell = params.get("rsi_sell_thresh", 55.0)
    confirm  = int(params.get("trend_confirm_bars", 3))

    out = df.copy()
    out["ema_short"] = out["close"].ewm(span=ema_s, adjust=False).mean()
    out["ema_long"]  = out["close"].ewm(span=ema_l, adjust=False).mean()

    # RSI — Wilder smoothing (alpha = 1/period)
    delta    = out["close"].diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1 / rsi_p, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / rsi_p, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    out["rsi"] = 100 - (100 / (1 + rs))

    # Trend: EMA alignment for >= confirm consecutive bars
    ema_above = (out["ema_short"] > out["ema_long"]).astype(int)
    ema_below = (out["ema_short"] < out["ema_long"]).astype(int)

    # Rolling sum of confirm bars — equals confirm only when all bars aligned
    uptrend   = ema_above.rolling(window=confirm).sum() == confirm
    downtrend = ema_below.rolling(window=confirm).sum() == confirm

    prev_rsi = out["rsi"].shift(1)
    curr_rsi = out["rsi"]

    rsi_buy_signal  = (prev_rsi < rsi_buy)  & (curr_rsi >= rsi_buy)
    rsi_sell_signal = (prev_rsi > rsi_sell) & (curr_rsi <= rsi_sell)

    signals = pd.Series(Signal.HOLD, index=out.index)
    signals[uptrend   & rsi_buy_signal]  = Signal.BUY
    signals[downtrend & rsi_sell_signal] = Signal.SELL

    nan_mask = out["rsi"].isna() | out["ema_long"].isna() | prev_rsi.isna()
    signals[nan_mask] = Signal.HOLD

    out["_signal"] = signals
    return out


# ── Strategy registry ─────────────────────────────────────────────────────────

STRATEGIES = {
    "ma_crossover": ma_crossover_signals,
    "macd":         macd_signals,
    "ema_rsi":      ema_rsi_signals,
}

DEFAULT_PARAMS = {
    "ma_crossover": {"short_period": 20, "long_period": 50},
    "macd": {"fast": 12, "slow": 26, "signal_period": 9, "zero_filter": True},
    "ema_rsi": {
        "ema_short": 21,
        "ema_long": 55,
        "rsi_period": 14,
        "rsi_buy_thresh": 45.0,
        "rsi_sell_thresh": 55.0,
        "trend_confirm_bars": 3,
    },
}
