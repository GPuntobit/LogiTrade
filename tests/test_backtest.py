"""Test del motore di backtest: contabilità, commissioni, SL/TP e metriche."""

import pandas as pd
import pytest

from logitrade.backtest import BacktestConfig, run_backtest

CFG = BacktestConfig(
    initial_cash=10_000.0,
    commission_pct=0.1,
    slippage_pct=0.05,
    position_pct=25.0,
    stop_loss_pct=5.0,
    take_profit_pct=15.0,
)
FEE = CFG.commission_pct / 100       # 0.001
SLIP = CFG.slippage_pct / 100        # 0.0005


def make_df(closes, entries, exits):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame({"close": [float(c) for c in closes], "entry": entries, "exit": exits}, index=idx)


def test_single_trade_signal_exit_accounting():
    # bar 2: 112/100.05-1 = +11.9% < 15% => niente take profit, esce per segnale
    df = make_df([100, 105, 112, 118], [True, False, False, False], [False, False, True, False])
    res = run_backtest(df, CFG)

    assert res.n_trades == 1
    t = res.trades[0]
    entry_price = 100 * (1 + SLIP)
    exit_price = 112 * (1 - SLIP)
    assert t.entry_price == pytest.approx(entry_price)
    assert t.exit_price == pytest.approx(exit_price)
    assert t.reason == "signal"
    assert t.return_pct == pytest.approx((exit_price / entry_price - 1) * 100)

    size = 2500.0 * (1 - FEE) / entry_price  # 25% del capitale, commissione sull'acquisto
    expected_cash = 7_500.0 + size * exit_price * (1 - FEE)
    assert res.final_equity == pytest.approx(expected_cash)
    assert res.total_return_pct == pytest.approx((expected_cash / 10_000.0 - 1) * 100)
    assert res.buy_hold_return_pct == pytest.approx((118 / 100 - 1) * 100)


def test_stop_loss_triggers():
    df = make_df([100, 93, 96], [True, False, False], [False, False, False])
    res = run_backtest(df, CFG)
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.reason == "stop_loss"
    assert t.exit_price == pytest.approx(93 * (1 - SLIP))
    assert t.return_pct < 0


def test_take_profit_triggers():
    df = make_df([100, 116, 118], [True, False, False], [False, False, False])
    res = run_backtest(df, CFG)
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.reason == "take_profit"
    assert t.exit_price == pytest.approx(116 * (1 - SLIP))
    assert t.return_pct > 0


def test_sl_tp_have_priority_over_signal_exit():
    """A parità di barra, SL/TP vengono valutati prima dell'uscita a segnale."""
    df = make_df([100, 116], [True, False], [False, True])
    res = run_backtest(df, CFG)
    assert res.trades[0].reason == "take_profit"


def test_position_closed_at_end_of_data():
    df = make_df([100, 105], [True, False], [False, False])
    res = run_backtest(df, CFG)
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.reason == "end_of_data"
    assert t.exit_price == pytest.approx(105 * (1 - SLIP))


def test_no_trades_flat_equity():
    df = make_df([100, 101, 102], [False, False, False], [False, False, False])
    res = run_backtest(df, CFG)
    assert res.n_trades == 0
    assert res.final_equity == pytest.approx(10_000.0)
    assert res.total_return_pct == pytest.approx(0.0)
    assert res.max_drawdown_pct == pytest.approx(0.0)
    assert res.win_rate_pct == pytest.approx(0.0)
    assert res.profit_factor == pytest.approx(0.0)
    assert res.buy_hold_return_pct == pytest.approx(2.0)
    assert (res.equity == 10_000.0).all()


def test_win_rate_and_profit_factor():
    # trade 1: +11.9% lordo (vincita) | trade 2: chiusura end_of_data in perdita
    df = make_df(
        [100, 105, 112, 100, 97, 96],
        [True, False, False, True, False, False],
        [False, False, True, False, False, False],
    )
    res = run_backtest(df, CFG)
    assert res.n_trades == 2
    win, loss = res.trades
    assert win.return_pct > 0 > loss.return_pct
    assert res.win_rate_pct == pytest.approx(50.0)
    assert res.profit_factor == pytest.approx(win.return_pct / abs(loss.return_pct))
    assert res.avg_trade_pct == pytest.approx((win.return_pct + loss.return_pct) / 2)


def test_max_drawdown_negative_on_losing_trade():
    df = make_df([100, 93, 92], [True, False, False], [False, False, False])
    res = run_backtest(df, CFG)
    assert res.max_drawdown_pct < 0


def test_no_pyramiding():
    """Due ingressi consecutivi con posizione già aperta: resta una sola posizione."""
    df = make_df([100, 101, 112], [True, True, False], [False, False, True])
    res = run_backtest(df, CFG)
    assert res.n_trades == 1
    assert res.trades[0].entry_time == df.index[0]


def test_equity_curve_length_matches_index():
    df = make_df([100, 105, 112, 118], [True, False, False, False], [False, False, True, False])
    res = run_backtest(df, CFG)
    assert len(res.equity) == len(df)
    assert res.equity.index.equals(df.index)


def test_missing_columns_raise():
    import pandas as pd

    df = pd.DataFrame({"close": [100.0, 101.0]})
    with pytest.raises(ValueError, match="Mancano colonne"):
        run_backtest(df, CFG)
