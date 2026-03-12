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

    # Strategy
    timeframe: str
    ma_short_period: int
    ma_long_period: int
    candles_required: int
    loop_interval_seconds: int

    # Sizing
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

    ma_short = _get_int("MA_SHORT_PERIOD", 20)
    ma_long = _get_int("MA_LONG_PERIOD", 50)
    if ma_short >= ma_long:
        raise ConfigValidationError(
            f"MA_SHORT_PERIOD ({ma_short}) must be less than MA_LONG_PERIOD ({ma_long})"
        )

    candles_required = _get_int("CANDLES_REQUIRED", 100)
    if candles_required < ma_long + 10:
        raise ConfigValidationError(
            f"CANDLES_REQUIRED ({candles_required}) must be at least MA_LONG_PERIOD + 10 ({ma_long + 10})"
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
        timeframe=_get_str("TIMEFRAME", "1h"),
        ma_short_period=ma_short,
        ma_long_period=ma_long,
        candles_required=candles_required,
        loop_interval_seconds=_get_int("LOOP_INTERVAL_SECONDS", 60),
        min_trade_size_usd=min_trade,
        max_trade_size_usd=max_trade,
        size_spread_min_pct=spread_min,
        size_spread_max_pct=spread_max,
        max_position_usd=max_position,
        max_drawdown_percent=_get_float("MAX_DRAWDOWN_PERCENT", 25.0),
        initial_equity_usd=_get_float("INITIAL_EQUITY_USD", 1000.0),
        db_path=db_path,
        log_dir=log_dir,
        log_level=_get_str("LOG_LEVEL", "INFO").upper(),
    )
