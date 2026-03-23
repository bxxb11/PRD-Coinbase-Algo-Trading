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


# ── 4. SuperTrend ─────────────────────────────────────────────────────────────

def supertrend_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    SuperTrend indicator — ATR-based dynamic support/resistance band.
    Enters immediately on trend flip (no pullback wait) → aggressive.

    params: atr_period (10), atr_multiplier (3.0)

    BUY:  SuperTrend flips from bearish to bullish (close > upper band)
    SELL: SuperTrend flips from bullish to bearish (close < lower band)

    The band ratchets: upper only moves down, lower only moves up,
    locking in trend direction. Loop is O(n) single pass.
    """
    atr_period = params.get("atr_period", 10)
    multiplier  = params.get("atr_multiplier", 3.0)

    out = df.copy()

    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()
    hl2 = (out["high"] + out["low"]) / 2.0

    basic_upper = (hl2 + multiplier * atr).to_numpy()
    basic_lower = (hl2 - multiplier * atr).to_numpy()
    close_arr   = out["close"].to_numpy()

    n = len(out)
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    supertrend  = [0] * n   # 1 = bullish, -1 = bearish, 0 = warmup

    for i in range(1, n):
        # Upper band: ratchets downward unless prior close > prior upper
        if basic_upper[i] < final_upper[i - 1] or close_arr[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Lower band: ratchets upward unless prior close < prior lower
        if basic_lower[i] > final_lower[i - 1] or close_arr[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        prev_st = supertrend[i - 1] if supertrend[i - 1] != 0 else 1
        if prev_st == 1:
            supertrend[i] = -1 if close_arr[i] < final_lower[i] else 1
        else:
            supertrend[i] =  1 if close_arr[i] > final_upper[i] else -1

    import numpy as np
    st_series = pd.Series(supertrend, index=out.index)
    out["supertrend"]   = st_series
    out["st_upper"]     = final_upper
    out["st_lower"]     = final_lower
    out["atr"]          = atr

    prev_st   = st_series.shift(1)
    buy_mask  = (prev_st == -1) & (st_series == 1)
    sell_mask = (prev_st ==  1) & (st_series == -1)

    signals = pd.Series(Signal.HOLD, index=out.index)
    signals[buy_mask]  = Signal.BUY
    signals[sell_mask] = Signal.SELL

    # Warmup: no signal until ATR is meaningful
    nan_mask = atr.isna() | (st_series == 0)
    signals[nan_mask] = Signal.HOLD

    out["_signal"] = signals
    return out


# ── 5. Donchian Channel Breakout + ADX Filter ─────────────────────────────────

def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int) -> pd.Series:
    """
    Pure-pandas ADX (no TA-Lib).
    Uses Wilder smoothing (ewm alpha=1/period) throughout.
    """
    alpha = 1.0 / period
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    h_diff = high - prev_high
    l_diff = prev_low - low

    plus_dm  = h_diff.where((h_diff > l_diff) & (h_diff > 0), 0.0)
    minus_dm = l_diff.where((l_diff > h_diff) & (l_diff > 0), 0.0)

    smooth_tr    = tr.ewm(alpha=alpha, adjust=False).mean()
    smooth_plus  = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    smooth_tr_safe = smooth_tr.replace(0, float("nan"))
    plus_di  = 100.0 * smooth_plus  / smooth_tr_safe
    minus_di = 100.0 * smooth_minus / smooth_tr_safe

    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx     = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx    = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx


def donchian_adx_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Donchian Channel Breakout with ADX consolidation filter.
    Turtle Trading heritage — enters breakouts FROM consolidation zones.

    params: dc_enter_bars (20), dc_exit_bars (10),
            adx_period (14), adx_threshold (25.0)

    BUY:  close > highest close of prior dc_enter_bars bars
          AND ADX < adx_threshold  (low volatility = fresh breakout)
    SELL: close < lowest close of prior dc_exit_bars bars
          (asymmetric — exit faster than entry to cut losers quickly)

    Shift(1) on channels avoids lookahead bias.
    """
    enter_bars = params.get("dc_enter_bars",  20)
    exit_bars  = params.get("dc_exit_bars",   10)
    adx_period = params.get("adx_period",     14)
    adx_thresh = params.get("adx_threshold", 25.0)

    out = df.copy()

    # Donchian channels on prior bars (shift avoids lookahead)
    out["dc_enter_high"] = out["close"].shift(1).rolling(window=enter_bars).max()
    out["dc_exit_low"]   = out["close"].shift(1).rolling(window=exit_bars).min()

    out["adx"] = _compute_adx(out["high"], out["low"], out["close"], adx_period)

    buy_mask  = (out["close"] > out["dc_enter_high"]) & (out["adx"] < adx_thresh)
    sell_mask = (out["close"] < out["dc_exit_low"])

    signals = pd.Series(Signal.HOLD, index=out.index)
    signals[buy_mask]  = Signal.BUY
    signals[sell_mask] = Signal.SELL

    nan_mask = out["dc_enter_high"].isna() | out["dc_exit_low"].isna() | out["adx"].isna()
    signals[nan_mask] = Signal.HOLD

    out["_signal"] = signals
    return out


# ── Strategy registry ─────────────────────────────────────────────────────────

STRATEGIES = {
    "ma_crossover": ma_crossover_signals,
    "macd":         macd_signals,
    "ema_rsi":      ema_rsi_signals,
    "supertrend":   supertrend_signals,
    "donchian_adx": donchian_adx_signals,
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
    "supertrend": {
        "atr_period": 10,
        "atr_multiplier": 3.0,
    },
    "donchian_adx": {
        "dc_enter_bars":  48,   # 2-day breakout on 1h bars — sweep optimal
        "dc_exit_bars":  240,   # 10-day low exit — hold winners longer
        "adx_period":     14,
        "adx_threshold": 25.0,
    },
}
