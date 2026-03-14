"""
Historical OHLCV data fetcher with CSV caching.

Fetches paginated OHLCV data from Coinbase via CCXT and caches to CSV
so subsequent runs load instantly without hitting the exchange.

Pagination required because Coinbase Advanced Trade caps responses at
300 candles per request. 12 months of 1h data = ~8,760 candles (~30 requests).
"""

import time
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd


_COINBASE_MAX_CANDLES = 300
_RATE_LIMIT_SLEEP = 1.2   # seconds between requests (conservative)

_TIMEFRAME_MS = {
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}


def fetch_historical_ohlcv(
    exchange: ccxt.Exchange,
    pair: str,
    timeframe: str = "1h",
    months: int = 12,
    cache_dir: str = "data/",
    no_cache: bool = False,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV data, with local CSV caching.

    First call:  fetches from exchange (paginated), writes CSV.
    Subsequent:  loads from CSV instantly (unless no_cache=True).

    Args:
        exchange: CCXT exchange instance (coinbaseadvanced).
        pair: Trading pair e.g. "BTC/USD".
        timeframe: CCXT timeframe string e.g. "1h".
        months: How many months of history to fetch (1–24).
        cache_dir: Directory for CSV cache files.
        no_cache: If True, bypass cache and re-fetch from exchange.

    Returns:
        DataFrame with columns [timestamp (UTC datetime), open, high, low, close, volume]
        Sorted ascending by timestamp, no duplicates.
    """
    cache_path = _cache_path(cache_dir, pair, timeframe, months)

    if not no_cache and cache_path.exists():
        print(f"[data_fetcher] Loading from cache: {cache_path}")
        return load_ohlcv_from_csv(str(cache_path))

    print(f"[data_fetcher] Fetching {months}mo of {pair} {timeframe} from exchange...")
    df = _fetch_paginated(exchange, pair, timeframe, months)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    print(f"[data_fetcher] Cached {len(df)} candles → {cache_path}")

    return df


def load_ohlcv_from_csv(csv_path: str) -> pd.DataFrame:
    """
    Load OHLCV data from a local CSV file.
    Parses timestamp column to UTC-aware datetime.
    """
    df = pd.read_csv(csv_path)
    df = _normalise_df(df)
    return df


# ── Private helpers ───────────────────────────────────────────────────────────

def _fetch_paginated(
    exchange: ccxt.Exchange,
    pair: str,
    timeframe: str,
    months: int,
) -> pd.DataFrame:
    """Paginate through CCXT fetch_ohlcv until all months are covered."""
    tf_ms = _TIMEFRAME_MS.get(timeframe)
    if tf_ms is None:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Supported: {list(_TIMEFRAME_MS.keys())}"
        )

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(months * 30.44 * 24 * 3600 * 1000)
    cursor = start_ms

    # Upper bound on iterations to guard against infinite loops
    max_iters = int((months * 30.44 * 24 * 3600 * 1000) / tf_ms // _COINBASE_MAX_CANDLES) + 10
    all_rows: list = []

    for _ in range(max_iters):
        try:
            batch = exchange.fetch_ohlcv(
                pair,
                timeframe=timeframe,
                since=cursor,
                limit=_COINBASE_MAX_CANDLES,
            )
        except (ccxt.NetworkError, ccxt.ExchangeError) as exc:
            raise RuntimeError(f"CCXT error fetching OHLCV: {exc}") from exc

        if not batch:
            break

        all_rows.extend(batch)
        last_ts = batch[-1][0]

        if last_ts >= now_ms:
            break

        cursor = last_ts + tf_ms
        time.sleep(_RATE_LIMIT_SLEEP)

    if not all_rows:
        raise RuntimeError(
            f"No OHLCV data returned for {pair} {timeframe} ({months}mo). "
            "Check exchange connectivity and pair symbol."
        )

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = _normalise_df(df)
    return df


def _normalise_df(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate, sort, and parse timestamps to UTC datetime."""
    df = df.copy()

    # Parse timestamp: may be epoch ms (int/float) or ISO string
    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    df = df.drop_duplicates(subset="timestamp")
    df = df.sort_values("timestamp").reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _cache_path(cache_dir: str, pair: str, timeframe: str, months: int) -> Path:
    """Build cache file path. Sanitises pair symbol (BTC/USD → BTC_USD)."""
    safe_pair = pair.replace("/", "_").replace(" ", "_")
    filename = f"{safe_pair}_{timeframe}_{months}m.csv"
    return Path(cache_dir) / filename
