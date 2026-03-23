"""
config.py — single source of truth for all runtime parameters.
Loads .env on import, validates types, exposes a frozen Config dataclass.
No other module reads os.environ directly.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigValidationError(Exception):
    pass


def _get_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, str(default)).strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    raise ConfigValidationError(f"{key} must be true/false, got: {val!r}")


def _get_int(key: str, default: int) -> int:
    val = os.environ.get(key, str(default))
    try:
        return int(val)
    except ValueError:
        raise ConfigValidationError(f"{key} must be an integer, got: {val!r}")


def _get_float(key: str, default: float) -> float:
    val = os.environ.get(key, str(default))
    try:
        return float(val)
    except ValueError:
        raise ConfigValidationError(f"{key} must be a float, got: {val!r}")


def _get_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass(frozen=True)
class Config:
    # Exchange
    coinbase_api_key: str
    coinbase_api_secret: str
    exchange_id: str
    trading_pair: str

    # Mode
    paper_trading: bool

    # Strategy selector
    strategy_name: str          # "ma_crossover" | "macd" | "ema_rsi"
    timeframe: str
    candles_required: int
    loop_interval_seconds: int
    scheduled_hours: tuple      # e.g. (2, 14) → tick at 02:00 + 14:00 UTC; () = interval mode

    # MA Crossover params
    ma_short_period: int
    ma_long_period: int

    # MACD params
    macd_fast: int
    macd_slow: int
    macd_signal_period: int
    macd_zero_filter: bool

    # EMA+RSI params (recommended strategy)
    ema_short: int
    ema_long: int
    rsi_period: int
    rsi_buy_thresh: float
    rsi_sell_thresh: float
    trend_confirm_bars: int

    # SuperTrend params
    atr_period: int
    atr_multiplier: float

    # Donchian + ADX params
    dc_enter_bars: int
    dc_exit_bars: int
    adx_period: int
    adx_threshold: float

    # Sizing ($1–$20 per trade, spread-driven)
    min_trade_size_usd: float
    max_trade_size_usd: float
    size_spread_min_pct: float
    size_spread_max_pct: float
    max_position_usd: float

    # Risk
    max_drawdown_percent: float
    initial_equity_usd: float

    # Persistence
    db_path: str

    # Logging
    log_dir: str
    log_level: str


def load_config() -> Config:
    """
    Load .env, cast and validate all fields.
    API credentials are only required when PAPER_TRADING=False.
    Raises ConfigValidationError on bad values.
    """
    paper_trading = _get_bool("PAPER_TRADING", True)

    api_key = _get_str("COINBASE_API_KEY")
    api_secret = _get_str("COINBASE_API_SECRET")
    if not paper_trading and (not api_key or not api_secret):
        raise ConfigValidationError(
            "COINBASE_API_KEY and COINBASE_API_SECRET are required when PAPER_TRADING=False"
        )

    strategy_name = _get_str("STRATEGY", "ema_rsi").lower()
    _valid_strategies = {"ma_crossover", "macd", "ema_rsi", "supertrend", "donchian_adx"}
    if strategy_name not in _valid_strategies:
        raise ConfigValidationError(
            f"STRATEGY must be one of {sorted(_valid_strategies)}, got: {strategy_name!r}"
        )

    ma_short = _get_int("MA_SHORT_PERIOD", 20)
    ma_long = _get_int("MA_LONG_PERIOD", 50)
    if ma_short >= ma_long:
        raise ConfigValidationError(
            f"MA_SHORT_PERIOD ({ma_short}) must be less than MA_LONG_PERIOD ({ma_long})"
        )

    ema_short = _get_int("EMA_SHORT", 13)
    ema_long  = _get_int("EMA_LONG", 55)
    if ema_short >= ema_long:
        raise ConfigValidationError(
            f"EMA_SHORT ({ema_short}) must be less than EMA_LONG ({ema_long})"
        )

    # candles_required must cover the warmup of the active strategy
    macd_slow   = _get_int("MACD_SLOW", 26)
    macd_signal = _get_int("MACD_SIGNAL_PERIOD", 9)
    rsi_p       = _get_int("RSI_PERIOD", 21)
    atr_p       = _get_int("ATR_PERIOD", 10)
    dc_enter    = _get_int("DC_ENTER_BARS", 48)
    adx_p       = _get_int("ADX_PERIOD", 14)

    min_candles = {
        "ma_crossover": ma_long + 10,
        "macd":         macd_slow + macd_signal + 10,
        "ema_rsi":      ema_long + rsi_p + 10,
        "supertrend":   atr_p + 10,
        "donchian_adx": dc_enter + adx_p + 10,
    }[strategy_name]

    candles_required = _get_int("CANDLES_REQUIRED", max(100, min_candles))
    if candles_required < min_candles:
        raise ConfigValidationError(
            f"CANDLES_REQUIRED ({candles_required}) must be at least {min_candles} "
            f"for strategy '{strategy_name}'"
        )

    min_trade = _get_float("MIN_TRADE_SIZE_USD", 1.0)
    max_trade = _get_float("MAX_TRADE_SIZE_USD", 20.0)
    max_position = _get_float("MAX_POSITION_USD", 200.0)
    if min_trade <= 0:
        raise ConfigValidationError("MIN_TRADE_SIZE_USD must be > 0")
    if max_trade > max_position:
        raise ConfigValidationError(
            f"MAX_TRADE_SIZE_USD ({max_trade}) must be <= MAX_POSITION_USD ({max_position})"
        )

    spread_min = _get_float("SIZE_SPREAD_MIN_PCT", 0.1)
    spread_max = _get_float("SIZE_SPREAD_MAX_PCT", 0.5)
    if spread_min >= spread_max:
        raise ConfigValidationError(
            f"SIZE_SPREAD_MIN_PCT ({spread_min}) must be less than SIZE_SPREAD_MAX_PCT ({spread_max})"
        )

    # Parse SCHEDULED_HOURS (e.g. "2,14" → (2, 14); blank → interval mode)
    _scheduled_raw = _get_str("SCHEDULED_HOURS", "")
    scheduled_hours: tuple = ()
    if _scheduled_raw:
        try:
            _parsed = tuple(sorted(int(h.strip()) for h in _scheduled_raw.split(",")))
        except ValueError:
            raise ConfigValidationError(
                f"SCHEDULED_HOURS must be comma-separated integers (0–23), got: {_scheduled_raw!r}"
            )
        for h in _parsed:
            if not (0 <= h <= 23):
                raise ConfigValidationError(
                    f"SCHEDULED_HOURS values must be 0–23, got: {h}"
                )
        scheduled_hours = _parsed

    db_path = _get_str("DB_PATH", "state/trading.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    log_dir = _get_str("LOG_DIR", "logs/")
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    return Config(
        coinbase_api_key=api_key,
        coinbase_api_secret=api_secret,
        exchange_id="coinbaseadvanced",
        trading_pair=_get_str("TRADING_PAIR", "BTC/USD"),
        paper_trading=paper_trading,
        # Strategy
        strategy_name=strategy_name,
        timeframe=_get_str("TIMEFRAME", "1h"),
        candles_required=candles_required,
        loop_interval_seconds=_get_int("LOOP_INTERVAL_SECONDS", 60),
        scheduled_hours=scheduled_hours,
        # MA Crossover
        ma_short_period=ma_short,
        ma_long_period=ma_long,
        # MACD
        macd_fast=_get_int("MACD_FAST", 12),
        macd_slow=macd_slow,
        macd_signal_period=macd_signal,
        macd_zero_filter=_get_bool("MACD_ZERO_FILTER", True),
        # EMA+RSI (recommended)
        ema_short=ema_short,
        ema_long=ema_long,
        rsi_period=rsi_p,
        rsi_buy_thresh=_get_float("RSI_BUY_THRESH", 45.0),
        rsi_sell_thresh=_get_float("RSI_SELL_THRESH", 55.0),
        trend_confirm_bars=_get_int("TREND_CONFIRM_BARS", 3),
        # SuperTrend
        atr_period=atr_p,
        atr_multiplier=_get_float("ATR_MULTIPLIER", 3.0),
        # Donchian + ADX
        dc_enter_bars=dc_enter,
        dc_exit_bars=_get_int("DC_EXIT_BARS", 240),
        adx_period=adx_p,
        adx_threshold=_get_float("ADX_THRESHOLD", 25.0),
        # Sizing
        min_trade_size_usd=min_trade,
        max_trade_size_usd=max_trade,
        size_spread_min_pct=spread_min,
        size_spread_max_pct=spread_max,
        max_position_usd=max_position,
        # Risk
        max_drawdown_percent=_get_float("MAX_DRAWDOWN_PERCENT", 25.0),
        initial_equity_usd=_get_float("INITIAL_EQUITY_USD", 1000.0),
        # Persistence & logging
        db_path=db_path,
        log_dir=log_dir,
        log_level=_get_str("LOG_LEVEL", "INFO").upper(),
    )
