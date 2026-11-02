"""Download e cache dei dati storici OHLCV tramite ccxt."""

import os
from pathlib import Path

import ccxt
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

_TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def _exchange(exchange_id: str) -> ccxt.Exchange:
    cls = getattr(ccxt, exchange_id)
    ex = cls({"enableRateLimit": True})
    ex.load_markets()
    return ex


def fetch_ohlcv(symbol: str, timeframe: str = "1h", since: str = "2024-01-01",
                exchange_id: str = "binance", use_cache: bool = True) -> pd.DataFrame:
    """Scarica le candele OHLCV e le restituisce come DataFrame (indice datetime UTC)."""
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"Timeframe non supportato: {timeframe}")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{exchange_id}_{symbol.replace('/', '_')}_{timeframe}_{since}.parquet"
    if use_cache and cache_file.exists():
        return pd.read_parquet(cache_file)

    ex = _exchange(exchange_id)
    if symbol not in ex.markets:
        raise ValueError(f"{symbol} non disponibile su {exchange_id}")

    since_ms = ex.parse8601(f"{since}T00:00:00Z")
    tf_ms = _TIMEFRAME_MS[timeframe]
    rows: list[list] = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since_ms = batch[-1][0] + tf_ms
        if len(batch) < 1000 or since_ms > ex.milliseconds():
            break

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime").drop(columns=["timestamp"]).astype(float)
    df = df[~df.index.duplicated(keep="last")]

    df.to_parquet(cache_file)
    return df
