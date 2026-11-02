"""Test montecarlo del motore di backtest.

Due livelli:
- invarianti del motore su N simulazioni con seed fissi (random walk + segnali casuali);
- scenari analitici deterministici dove il risultato e' calcolabile a mano.
"""

import numpy as np
import pandas as pd
import pytest

from logitrade.backtest import BacktestConfig, run_backtest

from helpers import build_df, random_walk_closes

FEE = 0.001
SLIP = 0.0005
CFG = BacktestConfig(
    initial_cash=10_000.0,
    commission_pct=0.1,
    slippage_pct=0.05,
    position_pct=25.0,
    stop_loss_pct=5.0,
    take_profit_pct=15.0,
)
ALLOWED_REASONS = {"signal", "stop_loss", "take_profit", "trailing_stop", "end_of_data"}


def random_signals_df(n: int, seed: int, entry_p: float = 0.03, exit_p: float = 0.08) -> pd.DataFrame:
    """OHLCV + segnali entry/exit casuali deterministici."""
    df = build_df(random_walk_closes(n, seed))
    rng = np.random.default_rng(seed + 10_000)
    df["entry"] = rng.random(n) < entry_p
    df["exit"] = rng.random(n) < exit_p
    return df


def assert_engine_invariants(res, df, cfg):
    """Invarianti che devono valere per QUALSIASI simulazione."""
    # struttura
    assert len(res.equity) == len(df)
    assert res.final_equity == pytest.approx(res.equity.iloc[-1])

    # tutti i trade sono chiusi e con motivo valido
    for t in res.trades:
        assert t.exit_price is not None
        assert t.exit_time is not None
        assert t.reason in ALLOWED_REASONS
        assert t.size > 0
        assert t.entry_price > 0

    # nessuna sovrapposizione di posizioni (l'uscita precede l'ingresso successivo)
    ordered = sorted(res.trades, key=lambda t: t.entry_time)
    for a, b in zip(ordered, ordered[1:]):
        assert a.exit_time <= b.entry_time

    # contabilità: initial + somma dei cash flow netti dei trade
    expected = cfg.initial_cash + sum(
        t.size * t.exit_price * (1 - cfg.commission_pct / 100)
        - t.size * t.entry_price / (1 - cfg.commission_pct / 100)
        for t in res.trades
    )
    assert res.final_equity == pytest.approx(expected, rel=1e-9)

    # metriche coerenti
    assert res.n_trades == len(res.trades)
    assert 0.0 <= res.win_rate_pct <= 100.0
    assert res.max_drawdown_pct <= 0.0
    assert (res.equity > 0).all()


class TestMonteCarloInvariants:
    SEEDS = range(40)

    @pytest.mark.parametrize("trailing", [0.0, 5.0])
    def test_invariants_hold_across_random_paths(self, trailing):
        cfg = BacktestConfig(
            initial_cash=10_000.0, commission_pct=0.1, slippage_pct=0.05,
            position_pct=25.0, stop_loss_pct=5.0, take_profit_pct=15.0,
            trailing_stop_pct=trailing,
        )
        for seed in self.SEEDS:
            df = random_signals_df(300, seed)
            res = run_backtest(df, cfg)
            assert_engine_invariants(res, df, cfg)

    def test_simulations_produce_trades(self):
        """Le simulazioni non devono essere vacue."""
        total = sum(
            run_backtest(random_signals_df(300, seed), CFG).n_trades
            for seed in self.SEEDS
        )
        assert total > 50

    @pytest.mark.parametrize("seed", [3, 11, 27])
    def test_deterministic_given_seed(self, seed):
        a = run_backtest(random_signals_df(300, seed), CFG)
        b = run_backtest(random_signals_df(300, seed), CFG)
        assert a.final_equity == b.final_equity
        assert a.n_trades == b.n_trades

    @pytest.mark.parametrize("drift", [0.001, -0.001])
    def test_invariants_with_drift(self, drift):
        for seed in self.SEEDS:
            df = build_df(random_walk_closes(300, seed, drift=drift, vol=0.015))
            rng = np.random.default_rng(seed)
            df["entry"] = rng.random(300) < 0.04
            df["exit"] = rng.random(300) < 0.10
            assert_engine_invariants(run_backtest(df, CFG), df, CFG)


class TestAnalyticScenarios:
    def test_flat_market_round_trips_pure_cost_drag(self):
        """Mercato piatto: ogni trade perde esattamente commissioni + slippage.

        Per giri di round-trip: final = initial * f^k con
        f = 0.75 + 0.25 * (1-fee)^2 * (1-slip) / (1+slip)   (position_pct = 25%).
        """
        n = 60
        df = build_df([100.0] * n)
        df["entry"] = [i % 6 == 0 for i in range(n)]   # barre 0, 6, 12, ...
        df["exit"] = [i % 6 == 3 for i in range(n)]    # barre 3, 9, 15, ...
        res = run_backtest(df, CFG)

        assert res.n_trades == 10
        expected_return_pct = ((1 - SLIP) / (1 + SLIP) - 1) * 100
        for t in res.trades:
            assert t.return_pct == pytest.approx(expected_return_pct)
            assert t.reason == "signal"

        f = 0.75 + 0.25 * (1 - FEE) ** 2 * (1 - SLIP) / (1 + SLIP)
        assert res.final_equity == pytest.approx(10_000.0 * f**10, rel=1e-9)
        assert res.total_return_pct < 0  # solo costi, nessun edge
        assert res.win_rate_pct == 0.0

    def test_stop_loss_loss_bounded(self):
        # ingresso poi discesa costante: SL scatta al primo bar sotto -5%
        closes = [100.0] + [100.0 * 0.99**i for i in range(1, 9)]
        df = build_df(closes)
        df["entry"] = [True] + [False] * 8
        df["exit"] = [False] * 9
        res = run_backtest(df, CFG)
        t = res.trades[0]
        assert t.reason == "stop_loss"
        assert -12.0 < t.return_pct <= -4.0  # -5% + costi, senza gap enormi su vol 1%

    def test_take_profit_gain_bounded(self):
        df = build_df([100.0, 116.0, 118.0])
        df["entry"] = [True, False, False]
        df["exit"] = [False, False, False]
        res = run_backtest(df, CFG)
        t = res.trades[0]
        assert t.reason == "take_profit"
        assert t.return_pct >= 12.0  # TP 15% meno slippage/costi

    def test_no_signals_flat_equity_any_path(self):
        for seed in range(10):
            df = build_df(random_walk_closes(200, seed))
            df["entry"] = False
            df["exit"] = False
            res = run_backtest(df, CFG)
            assert res.n_trades == 0
            assert res.final_equity == pytest.approx(10_000.0)
            assert (res.equity == 10_000.0).all()
            assert res.max_drawdown_pct == pytest.approx(0.0)

    def test_repeated_stop_losses_compound_losses(self):
        """Mercato discendente: tutti gli SL, equity in calo ma mai negativa."""
        n = 120
        df = build_df(random_walk_closes(n, seed=99, drift=-0.002, vol=0.01))
        rng = np.random.default_rng(7)
        df["entry"] = rng.random(n) < 0.06
        df["exit"] = False
        res = run_backtest(df, CFG)
        assert_engine_invariants(res, df, CFG)
        assert res.total_return_pct < 0
