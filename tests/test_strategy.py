"""Test della strategia RSI + MACD: regole di ingresso/uscita documentate nel README."""

import numpy as np
import pandas as pd
import pytest

from logitrade.indicators import macd, rsi
from logitrade.strategy import StrategyParams, generate_signals

from helpers import build_df, make_closes, make_dip_turn_closes

PARAMS = StrategyParams()  # default = config.yaml


def _rule_oracle(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Regola del README applicata manualmente sugli indicatori calcolati dal modulo indicators.

    Non duplica la formula degli indicatori: verifica solo il cablaggio delle regole
    (colonne usate, soglie, incroci con shift(1), filtro di trend).
    """
    r = rsi(df["close"], p.rsi_period)
    _, _, hist = macd(df["close"], p.macd_fast, p.macd_slow, p.macd_signal)
    cross_up = (hist > 0) & (hist.shift(1) <= 0)
    cross_down = (hist < 0) & (hist.shift(1) >= 0)
    if p.entry_mode == "classic":
        entry = (r < p.rsi_oversold) & cross_up
    else:  # dip_turn
        dipped = r.rolling(p.entry_lookback, min_periods=1).min() < p.rsi_oversold
        entry = dipped & cross_up
    if p.trend_sma_period > 0:
        sma = df["close"].rolling(p.trend_sma_period).mean()
        entry = entry & (sma > sma.shift(p.trend_slope_bars))
    return pd.DataFrame(
        {"entry": entry, "exit": (r > p.rsi_overbought) | cross_down},
        index=df.index,
    )


def test_columns_added():
    sig = generate_signals(build_df(make_closes()), PARAMS)
    for col in ("rsi", "macd", "macd_signal", "macd_hist", "entry", "exit"):
        assert col in sig.columns


def test_entry_rule_matches_documentation():
    df = build_df(make_closes())
    sig = generate_signals(df, PARAMS)
    expected = _rule_oracle(df, PARAMS)
    pd.testing.assert_series_equal(sig["entry"], expected.loc[sig.index, "entry"], check_names=False)


def test_exit_rule_matches_documentation():
    df = build_df(make_closes())
    sig = generate_signals(df, PARAMS)
    expected = _rule_oracle(df, PARAMS)
    pd.testing.assert_series_equal(sig["exit"], expected.loc[sig.index, "exit"], check_names=False)


def test_crafted_series_produces_one_entry():
    sig = generate_signals(build_df(make_closes()), PARAMS)
    assert int(sig["entry"].sum()) == 1
    entry_row = sig[sig["entry"]].iloc[0]
    assert entry_row["rsi"] < PARAMS.rsi_oversold


def test_crafted_series_produces_exit():
    sig = generate_signals(build_df(make_closes()), PARAMS)
    assert sig["exit"].any()
    first_exit = sig[sig["exit"]].iloc[0]
    assert first_exit["rsi"] > PARAMS.rsi_overbought


def test_entry_and_exit_never_overlap():
    sig = generate_signals(build_df(make_closes()), PARAMS)
    assert not (sig["entry"] & sig["exit"]).any()


def test_warmup_rows_dropped():
    closes = make_closes()
    sig = generate_signals(build_df(closes), PARAMS)
    assert len(sig) < len(closes)
    assert sig["rsi"].notna().all()
    assert sig["macd"].notna().all()


def test_no_signals_on_tight_thresholds():
    """Con soglie impossibili (oversold 5 / overbought 95) non deve scattare nulla."""
    p = StrategyParams(rsi_oversold=5.0, rsi_overbought=95.0)
    sig = generate_signals(build_df(make_closes()), p)
    assert not sig["entry"].any()
    assert not sig["exit"].any()


def test_custom_params_change_indicators():
    """Parametri diversi producono indicatori diversi: vengono applicati davvero."""
    sig_default = generate_signals(build_df(make_closes()), StrategyParams())
    sig_custom = generate_signals(build_df(make_closes()), StrategyParams(rsi_period=5))
    a, b = sig_default["rsi"].align(sig_custom["rsi"], join="inner")
    assert not np.allclose(a, b)


class TestTrendFilter:
    def test_filter_disabled_by_default(self):
        sig = generate_signals(build_df(make_closes()), StrategyParams())
        assert "sma" not in sig.columns
        assert "trend_up" not in sig.columns
        assert sig["entry"].any()  # comportamento invariato senza filtro

    def test_filter_suppresses_downtrend_entries(self):
        """Serie in calo: con filtro attivo nessun ingresso (SMA decrescente)."""
        df = build_df(make_closes())
        without = generate_signals(df, StrategyParams())
        with_filter = generate_signals(df, StrategyParams(trend_sma_period=20, trend_slope_bars=5))
        assert without["entry"].any()
        assert not with_filter["entry"].any()

    def test_filter_only_removes_entries(self):
        """Il filtro non crea ingressi nuovi: toglie solo quelli con SMA decrescente."""
        df = build_df(make_closes())
        without = generate_signals(df, StrategyParams())
        with_filter = generate_signals(df, StrategyParams(trend_sma_period=20, trend_slope_bars=5))
        assert not (with_filter["entry"] & ~without["entry"]).any()
        # gli ingressi tolti sono esattamente quelli con trend_up falso
        removed = without["entry"] & ~with_filter["entry"]
        assert (removed & with_filter["trend_up"]).sum() == 0

    def test_filter_rule_matches_documentation(self):
        df = build_df(make_closes())
        p = StrategyParams(trend_sma_period=20, trend_slope_bars=5)
        sig = generate_signals(df, p)
        expected = _rule_oracle(df, p)
        pd.testing.assert_series_equal(sig["entry"], expected.loc[sig.index, "entry"], check_names=False)

    def test_exit_unaffected_by_filter(self):
        df = build_df(make_closes())
        without = generate_signals(df, StrategyParams())
        with_filter = generate_signals(df, StrategyParams(trend_sma_period=20, trend_slope_bars=5))
        pd.testing.assert_series_equal(
            without["exit"], with_filter["exit"], check_names=False,
        )

    def test_sma_columns_present_when_active(self):
        sig = generate_signals(build_df(make_closes()), StrategyParams(trend_sma_period=20, trend_slope_bars=5))
        assert "sma" in sig.columns
        assert "trend_up" in sig.columns


class TestEntryModes:
    def test_dip_turn_entry_where_classic_has_none(self):
        """Dip e incrocio in barre diverse: classic tace, dip_turn entra."""
        df = build_df(make_dip_turn_closes())
        classic = generate_signals(df, StrategyParams())
        dip = generate_signals(df, StrategyParams(entry_mode="dip_turn", entry_lookback=5))
        assert not classic["entry"].any()
        assert dip["entry"].any()
        entry_row = dip[dip["entry"]].iloc[0]
        assert entry_row["rsi"] >= 30  # RSI gia' risalito al momento dell'incrocio

    def test_dip_turn_rule_matches_documentation(self):
        df = build_df(make_dip_turn_closes())
        p = StrategyParams(entry_mode="dip_turn", entry_lookback=5)
        sig = generate_signals(df, p)
        expected = _rule_oracle(df, p)
        pd.testing.assert_series_equal(sig["entry"], expected.loc[sig.index, "entry"], check_names=False)

    def test_dip_turn_lookback_1_equals_classic(self):
        """Con lookback=1 il dip coincide con la barra corrente: come classic."""
        df = build_df(make_dip_turn_closes())
        classic = generate_signals(df, StrategyParams())
        dip1 = generate_signals(df, StrategyParams(entry_mode="dip_turn", entry_lookback=1))
        pd.testing.assert_series_equal(classic["entry"], dip1["entry"], check_names=False)

    def test_lookback_respected(self):
        """Lookback troppo corto per coprire il dip: nessun ingresso."""
        df = build_df(make_dip_turn_closes())
        dip = generate_signals(df, StrategyParams(entry_mode="dip_turn", entry_lookback=5))
        assert dip["entry"].any()
        entry_ts = dip.index[dip["entry"]][0]
        # il minimo RSI entro 1 barra dall'incrocio e' >= 30
        dip1 = generate_signals(df, StrategyParams(entry_mode="dip_turn", entry_lookback=1))
        assert not dip1.loc[[entry_ts], "entry"].iloc[0]

    def test_exit_unaffected_by_entry_mode(self):
        df = build_df(make_closes())
        classic = generate_signals(df, StrategyParams())
        dip = generate_signals(df, StrategyParams(entry_mode="dip_turn", entry_lookback=5))
        pd.testing.assert_series_equal(classic["exit"], dip["exit"], check_names=False)

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="entry_mode sconosciuto"):
            generate_signals(build_df(make_closes()), StrategyParams(entry_mode="boh"))
