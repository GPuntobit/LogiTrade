"""Test del trailing stop nel motore di backtest."""

import pytest

from logitrade.backtest import BacktestConfig, run_backtest

from test_backtest import make_df

CFG_TRAILING = BacktestConfig(
    initial_cash=10_000.0,
    commission_pct=0.1,
    slippage_pct=0.05,
    position_pct=25.0,
    stop_loss_pct=5.0,
    take_profit_pct=15.0,
    trailing_stop_pct=5.0,
)
FEE = CFG_TRAILING.commission_pct / 100
SLIP = CFG_TRAILING.slippage_pct / 100


class TestTrailingStop:
    def test_triggers_after_pullback_from_peak(self):
        # 100 -> 110 (peak) -> 104: -5.5% dal massimo => trailing attivo
        df = make_df([100, 110, 104], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.n_trades == 1
        t = res.trades[0]
        assert t.reason == "trailing_stop"
        assert t.exit_price == pytest.approx(104 * (1 - SLIP))
        assert t.exit_time == df.index[2]

    def test_not_triggered_when_price_holds(self):
        # 100 -> 110 -> 105.5: solo -4.1% dal massimo => nessun trailing
        df = make_df([100, 110, 105.5], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "end_of_data"

    def test_disabled_by_default(self):
        df = make_df([100, 110, 104], [True, False, False], [False, False, False])
        res = run_backtest(df, BacktestConfig())  # trailing 0
        assert res.trades[0].reason == "end_of_data"

    def test_locks_profit_above_entry(self):
        # 100 -> 114 (sotto il TP) -> 108.2: trailing attivo, trade in profitto
        df = make_df([100, 114, 108.2], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        t = res.trades[0]
        assert t.reason == "trailing_stop"
        assert t.return_pct > 0

    def test_stop_loss_has_priority_over_trailing(self):
        # stessa barra: -7% dall'ingresso (SL) e -5% dal massimo (trailing)
        df = make_df([100, 110, 93], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "stop_loss"

    def test_take_profit_has_priority_over_trailing(self):
        # stessa barra: +16% dall'ingresso (TP) e prezzo sotto il trail? impossibile,
        # ma il TP deve comunque vincere sul segnale/trailing generico
        df = make_df([100, 105, 116.1], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "take_profit"

    def test_protective_exit_beats_signal_exit(self):
        # stessa barra: trailing attivo E segnale di uscita => vince il trailing
        df = make_df([100, 110, 104], [True, False, False], [False, False, True])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "trailing_stop"

    def test_peak_updated_after_exit_checks(self):
        # il massimo e' aggiornato DOPO i controlli: la barra corrente non conta
        # 100 -> 110 -> 111 -> 104.5: trail 5% su peak=111 => soglia 105.45 => attivo
        df = make_df([100, 110, 111, 104.5], [True, False, False, False], [False, False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "trailing_stop"
        assert res.trades[0].exit_time == df.index[3]

    def test_trailing_accounting(self):
        df = make_df([100, 110, 104], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        entry_price = 100 * (1 + SLIP)
        size = 2_500.0 * (1 - FEE) / entry_price
        expected = 7_500.0 + size * 104 * (1 - SLIP) * (1 - FEE)
        assert res.final_equity == pytest.approx(expected)

    def test_trailing_tighter_than_static_sl(self):
        # trailing 3% piu' stretto dello SL 5%: deve scattare per primo
        cfg = BacktestConfig(trailing_stop_pct=3.0, stop_loss_pct=5.0)
        df = make_df([100, 110, 106.5], [True, False, False], [False, False, False])
        res = run_backtest(df, cfg)
        assert res.trades[0].reason == "trailing_stop"

    def test_trailing_with_falling_market_hits_static_sl(self):
        # mercato in calo senza mai nuovi massimi: resta lo SL statico
        df = make_df([100, 98, 93], [True, False, False], [False, False, False])
        res = run_backtest(df, CFG_TRAILING)
        assert res.trades[0].reason == "stop_loss"
