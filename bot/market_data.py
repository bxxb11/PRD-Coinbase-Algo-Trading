"""
market_data.py — fetch OHLCV candles and ticker price from Coinbase via CCXT.
Returns clean pandas DataFrames. Does not implement retry — the trading loop
owns that decision.
"""

import ccxt
import pandas as pd

from bot.logger import get_system_logger


class InsufficientDataError(Exception):
    pass


class PriceFetchError(Exception):
    pass


def create_exchange(config) -> ccxt.Exchange:
    """
    Instantiate ccxt.coinbaseadvanced.
    In PAPER_TRADING mode, credentials are optional (market data is public).
    """
    params: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if config.coinbase_api_key and config.coinbase_api_secret:
        params["apiKey"] = config.coinbase_api_key
        params["secret"] = config.coinbase_api_secret

    exchange = ccxt.coinbaseadvanced(params)
    return exchange


def fetch_ohlcv(
    exchange: ccxt.Exchange,
    pair: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles and return as a DataFrame with columns:
      [timestamp, open, high, low, close, volume]
    timestamp is a UTC-aware datetime.

    Raises InsufficientDataError if fewer than (limit - 5) candles are returned.
    """
    log = get_system_logger()
    raw = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)

    if not raw:
        raise InsufficientDataError(f"fetch_ohlcv returned empty data for {pair}/{timeframe}")

    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    min_required = limit - 5
    if len(df) < min_required:
        raise InsufficientDataError(
            f"Insufficient candles: got {len(df)}, need at least {min_required}"
        )

    log.debug(f"Fetched {len(df)} {timeframe} candles for {pair}")
    return df


def fetch_ticker_price(exchange: ccxt.Exchange, pair: str) -> float:
    """
    Fetch the latest ticker price.
    Raises PriceFetchError if the price is missing or zero.
    """
    ticker = exchange.fetch_ticker(pair)
    price = ticker.get("last")
    if price is None or price <= 0:
        raise PriceFetchError(f"Invalid ticker price for {pair}: {price!r}")
    return float(price)
