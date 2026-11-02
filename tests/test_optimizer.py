"""Test dell'ottimizzazione parametri e della validazione walk-forward."""

import numpy as np
import pandas as pd
import pytest

from logitrade.backtest import BacktestConfig, run_backtest
from logitrade.breakout import BreakoutParams
from logitrade.breakout import generate_signals as breakout_signals
from logitrade.optimizer import DEFAULT_GRID, grid_search, walk_forward
from logitrade.strategy import StrategyParams, generate_signals

from helpers import build_df, make_closes

BASE_STRATEGY = StrategyParams()
BASE_RISK = BacktestConfig()

SMALL_GRID = {
    "rsi_oversold": [25.0, 30.0],
    "stop_loss_pct": [5.0, 8.0],
}


def make_daily_df(n: int = 400) -> pd.DataFrame:
    """Serie giornaliera oscillante abbastanza lunga per il walk-forward."""
    rng = np.random.default_rng(7)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.normal(0.0002, 0.03)))
    return build_df(closes, start="2024-01-01", freq="D")


class TestGridSearch:
    def test_result_count_and_sorting(self):
        df = build_df(make_closes())
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)
        assert len(rows) == 4  # 2 strategie x 2 rischi
        scores = [r.score for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_grid_values_applied(self):
        df = build_df(make_closes())
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)
        oversold = {r.strategy.rsi_oversold for r in rows}
        sl = {r.risk.stop_loss_pct for r in rows}
        assert oversold == {25.0, 30.0}
        assert sl == {5.0, 8.0}
        # i campi fuori grid mantengono i valori base
        assert all(r.strategy.rsi_period == BASE_STRATEGY.rsi_period for r in rows)
        assert all(r.risk.position_pct == BASE_RISK.position_pct for r in rows)

    def test_min_trades_guard(self):
        """Combo senza trade: score -inf, in fondo all'ordinamento."""
        df = make_daily_df(60)
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, {"stop_loss_pct": [5.0]}, min_trades=100)
        assert all(r.score == float("-inf") for r in rows)

    def test_empty_grid_uses_base_params(self):
        df = build_df(make_closes())
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, {})
        assert len(rows) == 1
        assert rows[0].strategy == BASE_STRATEGY
        assert rows[0].risk == BASE_RISK

    def test_unknown_grid_keys_ignored(self):
        df = build_df(make_closes())
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, {"nonexistent": [1, 2]})
        assert len(rows) == 1

    def test_score_is_total_return(self):
        df = build_df(make_closes())
        rows = grid_search(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)
        for r in rows:
            assert r.score == pytest.approx(r.result.total_return_pct) or r.score == float("-inf")

    def test_deterministic(self):
        df = make_daily_df(200)
        a = grid_search(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)
        b = grid_search(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)
        assert [r.score for r in a] == [r.score for r in b]


class TestWalkForward:
    def test_window_layout(self):
        df = make_daily_df(400)
        wf = walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=180, test_days=90)
        assert len(wf.windows) == 3  # 180 + 90*2 + 70 residui
        prev_end = df.index[0] + pd.Timedelta(days=180)
        for w in wf.windows:
            assert w.train_end == w.test_start  # il train tocca il test
            assert w.train_start == w.test_start - pd.Timedelta(days=180)
            assert w.test_start == prev_end     # finestre contigue
            assert (w.test_end - w.test_start) <= pd.Timedelta(days=90)
            prev_end = w.test_end
        assert wf.windows[-1].test_end == df.index[-1]

    def test_no_windows_on_short_data(self):
        df = build_df(make_closes())  # ~3 giorni
        wf = walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=180, test_days=90)
        assert wf.windows == []
        assert wf.oos_return_pct == pytest.approx(0.0)

    def test_best_chosen_on_train_only(self):
        df = make_daily_df(400)
        wf = walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=180, test_days=90)
        first = wf.windows[0]
        train_df = df[(df.index >= first.train_start) & (df.index < first.train_end)]
        expected = grid_search(train_df, BASE_STRATEGY, BASE_RISK, SMALL_GRID)[0]
        assert first.best.score == pytest.approx(expected.score)
        assert first.best.strategy == expected.strategy

    def test_test_result_computed_on_test_window(self):
        df = make_daily_df(400)
        wf = walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=180, test_days=90)
        w = wf.windows[0]
        test_df = df[(df.index >= w.test_start) & (df.index < w.test_end)]
        recomputed = run_backtest(generate_signals(test_df, w.best.strategy), w.best.risk)
        assert w.test_result.total_return_pct == pytest.approx(recomputed.total_return_pct)

    def test_oos_is_compounded(self):
        df = make_daily_df(400)
        wf = walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=180, test_days=90)
        factor = 1.0
        for w in wf.windows:
            factor *= 1 + w.test_result.total_return_pct / 100
        assert wf.oos_return_pct == pytest.approx((factor - 1) * 100)

    def test_grid_size_scales_calls(self):
        """Il numero di combo valutate per finestra = |strategie| x |rischi|."""
        df = make_daily_df(200)
        calls = {"n": 0}
        original = generate_signals

        def counting(df_, p=None):
            calls["n"] += 1
            return original(df_, p)

        import logitrade.optimizer as opt
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(opt, "generate_signals", counting)
            walk_forward(df, BASE_STRATEGY, BASE_RISK, SMALL_GRID, train_days=90, test_days=90)
        finally:
            monkey.undo()
        # 2 finestre x (2 combo di strategia sul train + 1 valutazione sul test)
        assert calls["n"] == 6


class TestBreakoutGrid:
    def test_grid_applies_to_breakout_params(self):
        """grid_search funziona con BreakoutParams e la rispettiva signals_fn."""
        df = build_df(make_closes())
        rows = grid_search(
            df, BreakoutParams(), BASE_RISK,
            grid={"entry_lookback": [5, 10], "stop_loss_pct": [5.0, 8.0]},
            signals_fn=breakout_signals,
        )
        assert len(rows) == 4
        assert {r.strategy.entry_lookback for r in rows} == {5, 10}
        assert all(isinstance(r.strategy, BreakoutParams) for r in rows)

    def test_walk_forward_with_breakout(self):
        df = make_daily_df(400)
        wf = walk_forward(
            df, BreakoutParams(), BASE_RISK,
            grid={"entry_lookback": [5, 10], "stop_loss_pct": [5.0]},
            train_days=180, test_days=90,
            signals_fn=breakout_signals,
        )
        assert len(wf.windows) == 3
        for w in wf.windows:
            assert isinstance(w.best.strategy, BreakoutParams)
