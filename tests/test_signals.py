"""Test dei segnali live: classificazione, candela chiusa, anti-repainting."""

import pandas as pd
import pytest

import logitrade.signals as signals_mod
from logitrade.signals import SignalSnapshot, check_signal
from logitrade.strategy import generate_signals

from helpers import build_df, make_closes, make_settings


def stub_generate_signals(df, params=None):
    """Sostituisce generate_signals con segnali pilotati dal test."""
    out = df.copy()
    out["rsi"] = 50.0
    out["macd"] = 0.0
    out["macd_signal"] = 0.0
    out["macd_hist"] = 1.0
    out["prev_macd_hist"] = -1.0
    out["entry"] = False
    out["exit"] = False
    return out


@pytest.fixture
def settings():
    return make_settings()


class TestStatusMapping:
    def test_entry_on_last_closed_candle(self, monkeypatch, settings):
        df = build_df([100.0] * 6)
        sig = stub_generate_signals(df)
        sig.loc[sig.index[-2], "entry"] = True  # ultima candela chiusa
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", lambda *a, **k: sig)

        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "ENTRY"
        assert "ipervenduto" in snap.reason

    def test_exit_on_last_closed_candle(self, monkeypatch, settings):
        df = build_df([100.0] * 6)
        sig = stub_generate_signals(df)
        sig.loc[sig.index[-2], "exit"] = True
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", lambda *a, **k: sig)

        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "EXIT"
        assert "ipercomprato" in snap.reason

    def test_neutral_when_no_signal(self, monkeypatch, settings):
        df = build_df([100.0] * 6)
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", stub_generate_signals)

        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "NEUTRAL"


class TestFormingCandleIgnored:
    def test_entry_on_forming_candle_is_ignored(self, monkeypatch, settings):
        """La candela in formazione può ancora cambiare: non deve generare segnali."""
        df = build_df([100.0] * 6)
        sig = stub_generate_signals(df)
        sig.loc[sig.index[-1], "entry"] = True  # solo candela in formazione
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", lambda *a, **k: sig)

        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "NEUTRAL"

    def test_snapshot_uses_last_closed_candle_data(self, monkeypatch, settings):
        df = build_df([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        sig = stub_generate_signals(df)
        sig["rsi"] = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", lambda *a, **k: sig)

        snap = check_signal("TEST/USDT", settings)
        assert snap.candle_time == df.index[-2]
        assert snap.price == pytest.approx(104.0)  # close della penultima candela
        assert snap.rsi == pytest.approx(50.0)     # rsi della penultima candela


class TestNeutralReasons:
    def _snap_with_rsi(self, monkeypatch, settings, rsi_value):
        df = build_df([100.0] * 6)
        sig = stub_generate_signals(df)
        sig["rsi"] = rsi_value
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", lambda *a, **k: sig)
        return check_signal("TEST/USDT", settings)

    def test_near_entry_zone(self, monkeypatch, settings):
        snap = self._snap_with_rsi(monkeypatch, settings, 35.0)  # < 30 + 10
        assert snap.status == "NEUTRAL"
        assert "ingresso" in snap.reason

    def test_near_exit_zone(self, monkeypatch, settings):
        snap = self._snap_with_rsi(monkeypatch, settings, 65.0)  # > 70 - 10
        assert snap.status == "NEUTRAL"
        assert "uscita" in snap.reason

    def test_mid_range(self, monkeypatch, settings):
        snap = self._snap_with_rsi(monkeypatch, settings, 50.0)
        assert snap.status == "NEUTRAL"
        assert snap.reason == "condizioni non allineate"


class TestValidation:
    def test_insufficient_data_raises(self, monkeypatch, settings):
        df = build_df([100.0] * 6)

        def one_row(a, b=None):
            return df.iloc[:1]

        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        monkeypatch.setattr(signals_mod, "generate_signals", one_row)
        with pytest.raises(ValueError, match="Dati insufficienti"):
            check_signal("TEST/USDT", settings)

    def test_unsupported_timeframe_raises(self, settings):
        with pytest.raises(ValueError, match="Timeframe non supportato"):
            signals_mod.fetch_recent("TEST/USDT", "2w")


class TestBreakoutSnapshot:
    """check_signal con famiglia breakout: stato dalle bande di Donchian."""

    def _check(self, monkeypatch, closes, **settings_overrides):
        df = build_df(closes)
        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: df)
        s = make_settings(
            strategy_family="breakout",
            breakout_entry_lookback=3,
            breakout_exit_lookback=3,
            **settings_overrides,
        )
        return check_signal("TEST/USDT", s)

    def test_entry_on_breakout(self, monkeypatch):
        # 5 barre piatte poi un salto: rottura del massimo sull'ultima chiusa
        snap = self._check(monkeypatch, [100.0] * 6 + [110.0, 111.0])
        assert snap.status == "ENTRY"
        assert "rottura del massimo" in snap.reason
        assert "upper" in snap.extra

    def test_exit_on_breakdown(self, monkeypatch):
        # rally poi crollo: rottura del minimo sull'ultima chiusa
        closes = [100.0, 110.0, 120.0, 130.0, 125.0, 115.0, 100.0, 95.0]
        snap = self._check(monkeypatch, closes)
        assert snap.status == "EXIT"
        assert "rottura del minimo" in snap.reason

    def test_neutral_between_bands(self, monkeypatch):
        snap = self._check(monkeypatch, [100.0, 100.5, 101.0, 100.8, 101.1, 100.9, 101.0, 100.95])
        assert snap.status == "NEUTRAL"
        assert "bande" in snap.reason or "rottura" in snap.reason

    def test_forming_candle_ignored(self, monkeypatch):
        # il salto e' sulla candela in formazione: nessun segnale ancora
        snap = self._check(monkeypatch, [100.0] * 7 + [110.0])
        assert snap.status == "NEUTRAL"

    def test_rsi_macd_still_computed_for_display(self, monkeypatch):
        # serie lunga abbastanza per il warmup RSI (14 barre)
        snap = self._check(monkeypatch, [100.0] * 24 + [110.0, 111.0])
        assert snap.rsi > 0
        assert snap.status == "ENTRY"


class TestIntegration:
    """Catena reale: dati OHLCV fintizi -> generate_signals reale -> check_signal."""

    def test_entry_detected_on_last_closed(self, monkeypatch, settings):
        closes = make_closes()
        raw = build_df(closes)
        full = generate_signals(raw)
        entry_ts = full.index[full["entry"]][0]
        pos = raw.index.get_loc(entry_ts)
        truncated = raw.iloc[: pos + 2]  # entry come ultima candela CHIUSA + 1 in formazione

        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: truncated)
        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "ENTRY"
        assert snap.candle_time == entry_ts
        assert snap.rsi < settings.rsi_oversold

    def test_entry_on_forming_candle_not_reported_yet(self, monkeypatch, settings):
        closes = make_closes()
        raw = build_df(closes)
        full = generate_signals(raw)
        entry_ts = full.index[full["entry"]][0]
        pos = raw.index.get_loc(entry_ts)
        truncated = raw.iloc[: pos + 1]  # entry come candela IN FORMAZIONE

        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: truncated)
        snap = check_signal("TEST/USDT", settings)
        assert snap.status == "NEUTRAL"
        assert snap.candle_time == raw.index[pos - 1]

    def test_trend_filter_wired_from_settings(self, monkeypatch):
        """settings.trend_sma_period arriva alla strategia: filtro attivo sopprime l'entry."""
        closes = make_closes()
        raw = build_df(closes)
        full = generate_signals(raw)
        entry_ts = full.index[full["entry"]][0]
        pos = raw.index.get_loc(entry_ts)
        truncated = raw.iloc[: pos + 2]

        monkeypatch.setattr(signals_mod, "fetch_recent", lambda *a, **k: truncated)
        # senza filtro: ENTRY (come da test precedente)
        snap_off = check_signal("TEST/USDT", make_settings(trend_sma_period=0))
        assert snap_off.status == "ENTRY"
        # con filtro SMA(200): serie troppo corta per la SMA => nessun ingresso
        snap_on = check_signal("TEST/USDT", make_settings(trend_sma_period=200))
        assert snap_on.status == "NEUTRAL"

    def test_snapshot_dataclass_fields(self):
        snap = SignalSnapshot(
            symbol="X/USDT", timeframe="1h", candle_time=pd.Timestamp("2024-01-01", tz="UTC"),
            price=1.0, rsi=50.0, macd_hist=0.1, prev_macd_hist=-0.1, status="NEUTRAL", reason="ok",
        )
        assert snap.status == "NEUTRAL"
