"""Test della strategia breakout (Donchian)."""

import pandas as pd
import pytest

from logitrade.breakout import BreakoutParams, generate_signals

from helpers import build_df

PARAMS = BreakoutParams()  # entry 20 / exit 10 = config.yaml


def make_rising(n: int = 40, rate: float = 1.03) -> list[float]:
    return [100.0 * rate**i for i in range(n)]


def make_falling(n: int = 40, rate: float = 0.97) -> list[float]:
    return [100.0 * rate**i for i in range(n)]


def make_up_then_down(n_up: int = 30, n_down: int = 15) -> list[float]:
    closes = make_rising(n_up, 1.03)
    p = closes[-1]
    for _ in range(n_down):
        p *= 0.95
        closes.append(p)
    return closes


def _rule_oracle(df: pd.DataFrame, p: BreakoutParams) -> pd.DataFrame:
    """Regola documentata applicata manualmente: verifica il cablaggio (bande, shift)."""
    upper = df["high"].rolling(p.entry_lookback).max().shift(1)
    lower = df["low"].rolling(p.exit_lookback).min().shift(1)
    entry = df["close"] > upper
    exit_ = df["close"] < lower
    if p.trend_sma_period > 0:
        sma = df["close"].rolling(p.trend_sma_period).mean()
        entry = entry & (sma > sma.shift(p.trend_slope_bars))
    return pd.DataFrame({"entry": entry, "exit": exit_}, index=df.index)


class TestBreakoutRules:
    def test_columns_added(self):
        sig = generate_signals(build_df(make_rising()), PARAMS)
        for col in ("upper", "lower", "entry", "exit"):
            assert col in sig.columns

    def test_entry_rule_matches_documentation(self):
        df = build_df(make_up_then_down())
        sig = generate_signals(df, PARAMS)
        expected = _rule_oracle(df, PARAMS)
        pd.testing.assert_series_equal(sig["entry"], expected.loc[sig.index, "entry"], check_names=False)

    def test_exit_rule_matches_documentation(self):
        df = build_df(make_up_then_down())
        sig = generate_signals(df, PARAMS)
        expected = _rule_oracle(df, PARAMS)
        pd.testing.assert_series_equal(sig["exit"], expected.loc[sig.index, "exit"], check_names=False)

    def test_rising_series_produces_entries(self):
        sig = generate_signals(build_df(make_rising()), PARAMS)
        assert sig["entry"].any()

    def test_falling_series_produces_exits(self):
        sig = generate_signals(build_df(make_falling()), PARAMS)
        assert sig["exit"].any()
        assert not sig["entry"].any()

    def test_up_then_down_produces_both(self):
        sig = generate_signals(build_df(make_up_then_down()), PARAMS)
        assert sig["entry"].any()
        assert sig["exit"].any()

    def test_bands_use_previous_bars_only(self):
        """La barra corrente non entra nelle bande: solo close > massimo PRECEDENTE."""
        # 5 barre piatte poi un salto: l'ingresso scatta solo sul salto
        closes = [100.0] * 10 + [110.0]
        sig = generate_signals(build_df(closes), BreakoutParams(entry_lookback=5, exit_lookback=5))
        assert sig["entry"].sum() == 1
        assert bool(sig["entry"].iloc[-1])

    def test_no_entry_without_close_above_band(self):
        """High che fa nuovi massimi ma close sotto la banda: nessun ingresso."""
        # high = close*1.01 (helpers): con salto < 1% la banda non e' rotta dal close
        closes = [100.0] * 10 + [100.5]
        sig = generate_signals(build_df(closes), BreakoutParams(entry_lookback=5, exit_lookback=5))
        assert not sig["entry"].any()

    def test_entry_exit_never_overlap(self):
        # upper >= lower per costruzione (massimo su finestra piu' ampia)
        sig = generate_signals(build_df(make_up_then_down()), PARAMS)
        assert not (sig["entry"] & sig["exit"]).any()

    def test_warmup_rows_dropped(self):
        closes = make_rising()
        sig = generate_signals(build_df(closes), PARAMS)
        assert len(sig) == len(closes) - PARAMS.entry_lookback  # shift(1) + rolling(N)
        assert sig["upper"].notna().all()
        assert sig["lower"].notna().all()

    def test_lookback_params_respected(self):
        df = build_df(make_up_then_down())
        default = generate_signals(df, PARAMS)
        tight = generate_signals(df, BreakoutParams(entry_lookback=5, exit_lookback=5))
        assert len(tight) > len(default)  # warmup piu' corto

    def test_trend_filter_suppresses_entries(self):
        df = build_df(make_falling())
        without = generate_signals(df, BreakoutParams())
        with_filter = generate_signals(df, BreakoutParams(trend_sma_period=10, trend_slope_bars=5))
        assert not (with_filter["entry"] & ~without["entry"]).any()

    def test_trend_filter_columns(self):
        sig = generate_signals(build_df(make_rising()), BreakoutParams(trend_sma_period=10, trend_slope_bars=5))
        assert "sma" in sig.columns
        assert "trend_up" in sig.columns
