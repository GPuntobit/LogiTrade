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


def fetch_last_price(symbol: str, exchange_id: str = "okx") -> float:
    """Ultimo prezzo noto per il simbolo (ticker)."""
    ex = _exchange(exchange_id)
    if symbol not in ex.markets:
        raise ValueError(f"{symbol} non disponibile su {exchange_id}")
    ticker = ex.fetch_ticker(symbol)
    price = ticker.get("last") or ticker.get("close")
    if not price:
        raise ValueError(f"Prezzo non disponibile per {symbol}")
    return float(price)


def _fetch_history_backwards(ex: ccxt.Exchange, symbol: str, timeframe: str,
                             tf_ms: int, max_batch: int = 300) -> list[list]:
    """Storico disponibile paginando ALL'INDIETRO dall'ultima candela.

    Serve quando `since` precede la quotazione del simbolo: OKX ritorna vuoto
    invece delle candele dalla quotazione in poi.
    """
    rows = ex.fetch_ohlcv(symbol, timeframe, limit=1000)  # ultime disponibili
    if not rows:
        return []
    all_rows = list(rows)
    oldest = rows[0][0]
    window = max_batch
    while window >= 1:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=oldest - window * tf_ms, limit=1000)
        if not batch:
            window //= 2  # vicini alla quotazione: stringi la finestra
            continue
        all_rows = batch + all_rows
        if batch[0][0] >= oldest:  # nessun avanzamento: evita loop infiniti
            break
        oldest = batch[0][0]
        window = max_batch
    return all_rows


def fetch_ohlcv(symbol: str, timeframe: str = "1h", since: str = "2024-01-01",
                exchange_id: str = "okx", use_cache: bool = True) -> pd.DataFrame:
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
        next_since = batch[-1][0] + tf_ms
        if next_since <= since_ms:  # nessun avanzamento: evita loop infiniti
            break
        since_ms = next_since

    if not rows:
        # simbolo quotato DOPO `since`: recupera lo storico disponibile
        rows = _fetch_history_backwards(ex, symbol, timeframe, tf_ms)

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        raise ValueError(f"Nessun dato per {symbol} ({timeframe})")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime").drop(columns=["timestamp"]).astype(float)
    df = df[~df.index.duplicated(keep="last")]

    df.to_parquet(cache_file)
    return df
