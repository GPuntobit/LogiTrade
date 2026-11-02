"""Test dell'orchestrazione runner: run_symbol, dispatcher strategie, multi-timeframe."""

import pandas as pd
import pytest

from logitrade import runner
from logitrade.backtest import BacktestResult
from logitrade.breakout import BreakoutParams
from logitrade.runner import RunOutput, build_signals, build_strategy_params, compare_timeframes
from logitrade.strategy import StrategyParams

from helpers import build_df, make_closes, make_settings


def make_output(symbol: str, tf: str, total_return: float = 1.0) -> RunOutput:
    return RunOutput(symbol=symbol, timeframe=tf, since="2024-01-01",
                     result=BacktestResult(total_return_pct=total_return, n_trades=3))


class TestCompareTimeframes:
    def test_returns_all_symbols_per_timeframe(self, monkeypatch):
        monkeypatch.setattr(runner, "run_symbol", lambda symbol, settings, tf, since=None: make_output(symbol, tf))
        results = compare_timeframes(["AAA/USDT", "BBB/USDT"], make_settings(), ["1h", "4h"])
        assert set(results) == {"1h", "4h"}
        for tf in ("1h", "4h"):
            assert set(results[tf]) == {"AAA/USDT", "BBB/USDT"}
            assert results[tf]["AAA/USDT"] is not None

    def test_results_are_per_timeframe(self, monkeypatch):
        def fake_run(symbol, settings, tf, since=None):
            return make_output(symbol, tf, total_return={"1h": 1.0, "4h": 2.0, "1d": 3.0}[tf])

        monkeypatch.setattr(runner, "run_symbol", fake_run)
        results = compare_timeframes(["AAA/USDT"], make_settings(), ["1h", "4h", "1d"])
        assert results["1h"]["AAA/USDT"].total_return_pct == 1.0
        assert results["4h"]["AAA/USDT"].total_return_pct == 2.0
        assert results["1d"]["AAA/USDT"].total_return_pct == 3.0

    def test_failed_pair_becomes_none(self, monkeypatch):
        def fake_run(symbol, settings, tf, since=None):
            if symbol == "BAD/USDT":
                raise ValueError("simbolo inesistente")
            return make_output(symbol, tf)

        monkeypatch.setattr(runner, "run_symbol", fake_run)
        results = compare_timeframes(["GOOD/USDT", "BAD/USDT"], make_settings(), ["1h"])
        assert results["1h"]["GOOD/USDT"] is not None
        assert results["1h"]["BAD/USDT"] is None

    def test_empty_timeframes(self, monkeypatch):
        monkeypatch.setattr(runner, "run_symbol", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
        assert compare_timeframes(["AAA/USDT"], make_settings(), []) == {}


class TestStrategyDispatch:
    def test_rsi_macd_family(self):
        s = make_settings(strategy_family="rsi_macd")
        sig = build_signals(build_df(make_closes()), s)
        assert "macd_hist" in sig.columns
        assert "rsi" in sig.columns

    def test_breakout_family(self):
        s = make_settings(strategy_family="breakout")
        sig = build_signals(build_df(make_closes()), s)
        assert "upper" in sig.columns
        assert "lower" in sig.columns

    def test_unknown_family_raises(self):
        s = make_settings(strategy_family="boh")
        with pytest.raises(ValueError, match="sconosciuto"):
            build_signals(build_df(make_closes()), s)

    def test_params_breakout(self):
        s = make_settings(strategy_family="breakout", breakout_entry_lookback=40, breakout_exit_lookback=7)
        p = build_strategy_params(s)
        assert isinstance(p, BreakoutParams)
        assert p.entry_lookback == 40
        assert p.exit_lookback == 7

    def test_params_rsi_macd(self):
        s = make_settings(strategy_family="rsi_macd", rsi_oversold=25.0)
        p = build_strategy_params(s)
        assert isinstance(p, StrategyParams)
        assert p.rsi_oversold == 25.0
