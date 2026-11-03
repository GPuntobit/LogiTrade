"""Test del layer dati: paginazione della cache OHLCV (regressione bug batch OKX)."""

import pandas as pd
import pytest

import logitrade.data as data_mod

TF_MS = 86_400_000  # 1d
START_MS = 1_704_067_200_000  # 2024-01-01


class FakeExchange:
    """Exchange fittizio: ritorna batch piccoli come OKX (max 300 per richiesta)."""

    def __init__(self, total_candles: int, max_batch: int):
        self.total = total_candles
        self.max_batch = max_batch
        self.markets = {"X/USDT": {}}
        self.calls = 0

    def parse8601(self, s: str) -> int:
        return START_MS

    def milliseconds(self) -> int:
        return START_MS + self.total * TF_MS

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        offset = (since - START_MS) // TF_MS
        n = min(limit, self.max_batch, self.total - offset)
        if n <= 0:
            return []
        return [[START_MS + (offset + i) * TF_MS, 100.0, 101.0, 99.0, 100.5, 1000.0]
                for i in range(n)]


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", tmp_path)
    return tmp_path


def test_pagination_with_small_batches(cache_dir, monkeypatch):
    """Batch da 300 (come OKX): la paginazione deve scaricare TUTTE le candele."""
    fake = FakeExchange(total_candles=750, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)

    df = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=False)
    assert len(df) == 750
    # 3 batch con dati (300+300+150) + 1 batch vuoto che termina la paginazione
    assert fake.calls == 4
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()


def test_pagination_with_full_batches(cache_dir, monkeypatch):
    """Batch da 1000 (come Binance): regressione del comportamento originale."""
    fake = FakeExchange(total_candles=2500, max_batch=1000)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)

    df = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=False)
    assert len(df) == 2500


def test_no_infinite_loop_on_stalled_exchange(cache_dir, monkeypatch):
    """Exchange che non avanza: il loop deve terminare comunque."""

    class StalledExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
            self.calls += 1
            if self.calls > 5:
                pytest.fail("loop infinito nella paginazione")
            return [[START_MS, 100.0, 101.0, 99.0, 100.5, 1000.0]]

    fake = StalledExchange(total_candles=10, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)
    df = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=False)
    assert len(df) == 1


def test_cache_written_and_reused(cache_dir, monkeypatch):
    fake = FakeExchange(total_candles=50, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)

    df1 = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=True)
    assert len(df1) == 50
    assert (cache_dir / "okx_X_USDT_1d_2024-01-01.parquet").exists()
    calls_after_first = fake.calls

    df2 = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=True)
    assert len(df2) == 50
    assert fake.calls == calls_after_first  # secondo accesso servito dalla cache


def test_unknown_symbol_raises(cache_dir, monkeypatch):
    fake = FakeExchange(total_candles=10, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)
    with pytest.raises(ValueError, match="non disponibile"):
        data_mod.fetch_ohlcv("NOPE/USDT", "1d", "2024-01-01", "okx")


def test_unsupported_timeframe_raises(cache_dir, monkeypatch):
    fake = FakeExchange(total_candles=10, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)
    with pytest.raises(ValueError, match="Timeframe non supportato"):
        data_mod.fetch_ohlcv("X/USDT", "2w", "2024-01-01", "okx")


class LateListedExchange:
    """Simbolo quotato tardi: since prima della quotazione ritorna vuoto (come OKX)."""

    def __init__(self, listing_offset: int, total: int, max_batch: int = 300):
        self.listing = START_MS + listing_offset * TF_MS
        self.end = START_MS + (listing_offset + total) * TF_MS
        self.total = total
        self.max_batch = max_batch
        self.markets = {"X/USDT": {}}
        self.calls = 0

    def parse8601(self, s: str) -> int:
        return START_MS

    def milliseconds(self) -> int:
        return self.end

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        if since is None:
            start = max(self.listing, self.end - self.max_batch * TF_MS)
        elif since < self.listing:
            return []  # comportamento OKX: richiesta che parte prima della quotazione
        else:
            start = since
        n = min(limit, self.max_batch, (self.end - start) // TF_MS)
        if n <= 0:
            return []
        return [[start + i * TF_MS, 100.0, 101.0, 99.0, 100.5, 1000.0] for i in range(n)]


def test_late_listed_symbol_fallback(cache_dir, monkeypatch):
    """Regressione: simbolo quotato dopo `since` deve ottenere comunque lo storico."""
    fake = LateListedExchange(listing_offset=400, total=250, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)

    df = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=False)
    assert len(df) == 250  # tutte le candele dalla quotazione
    expected_first = pd.Timestamp(START_MS + 400 * TF_MS, unit="ms", tz="UTC")
    assert df.index[0] == expected_first
    assert not df.index.duplicated().any()


def test_late_listed_symbol_backward_pagination(cache_dir, monkeypatch):
    """Storico lungo del simbolo quotato tardi: piu' passi indietro, senza vuoti."""
    fake = LateListedExchange(listing_offset=100, total=750, max_batch=300)
    monkeypatch.setattr(data_mod, "_exchange", lambda ex_id: fake)

    df = data_mod.fetch_ohlcv("X/USDT", "1d", "2024-01-01", "okx", use_cache=False)
    assert len(df) == 750
    assert df.index.is_monotonic_increasing
