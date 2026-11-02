"""Test degli indicatori: RSI (Wilder) e MACD."""

import numpy as np
import pandas as pd
import pytest

from logitrade.indicators import macd, rsi


def _oscillating(n: int = 50) -> pd.Series:
    return pd.Series([100 + 10 * np.sin(i / 3) + i * 0.1 for i in range(n)])


class TestRSI:
    def test_warmup_rows_are_na(self):
        out = rsi(_oscillating(), 14)
        assert int(out.isna().sum()) == 14  # primi 14 valori non validi (delta.NaN + min_periods)
        assert out.first_valid_index() == 14

    def test_all_gains_is_100(self):
        rising = pd.Series(np.arange(1.0, 41.0))
        out = rsi(rising, 14)
        assert (out.dropna() == 100.0).all()

    def test_all_losses_is_0(self):
        falling = pd.Series(np.arange(40.0, 0.0, -1.0))
        out = rsi(falling, 14)
        assert (out.dropna() == 0.0).all()

    def test_bounded_between_0_and_100(self):
        rng = np.random.default_rng(42)
        prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300))))
        out = rsi(prices, 14).dropna()
        assert ((out >= 0) & (out <= 100)).all()

    def test_mirror_invariant(self):
        """RSI(x) + RSI(mirror di x) == 100: gain e loss si scambiano, RS -> 1/RS."""
        c = _oscillating()
        out = rsi(c, 14)
        mirror = rsi(200.0 - c, 14)
        valid = out.notna() & mirror.notna() & (out != 100.0) & (out != 0.0)
        assert valid.sum() > 30
        assert np.allclose(out[valid] + mirror[valid], 100.0)

    def test_constant_series_documents_quirk(self):
        """Comportamento documentato: loss media zero => RSI = 100 (artefatto, non NaN/50)."""
        flat = pd.Series([100.0] * 30)
        out = rsi(flat, 14)
        assert (out.dropna() == 100.0).all()

    def test_period_is_respected(self):
        c = _oscillating()
        assert int(rsi(c, 7).isna().sum()) == 7
        assert int(rsi(c, 21).isna().sum()) == 21


class TestMACD:
    def test_constant_series_is_zero(self):
        flat = pd.Series([100.0] * 60)
        line, signal, hist = macd(flat)
        assert np.allclose(line, 0.0)
        assert np.allclose(signal, 0.0)
        assert np.allclose(hist, 0.0)

    def test_rising_series_positive(self):
        rising = pd.Series(np.arange(1.0, 61.0))
        line, _, _ = macd(rising)
        assert (line.iloc[30:] > 0).all()

    def test_falling_series_negative(self):
        falling = pd.Series(np.arange(60.0, 0.0, -1.0))
        line, _, _ = macd(falling)
        assert (line.iloc[30:] < 0).all()

    def test_hist_is_line_minus_signal(self):
        line, signal, hist = macd(_oscillating())
        assert np.allclose(hist, line - signal)

    def test_no_nan(self):
        line, signal, hist = macd(_oscillating())
        for s in (line, signal, hist):
            assert s.notna().all()

    def test_custom_periods(self):
        line, signal, hist = macd(_oscillating(), fast=5, slow=10, signal=3)
        assert np.allclose(hist, line - signal)
