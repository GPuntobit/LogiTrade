"""Test del portafoglio simulato (paper trading)."""

import pytest

from logitrade.paper import Portfolio, Position
from logitrade.report import format_portfolio

FEE = 0.1   # %
SLIP = 0.05  # %


class TestBuy:
    def test_buy_creates_position_with_costs(self):
        pf = Portfolio(cash=10_000.0)
        pos = pf.buy("X/USDT", price=100.0, usdt_amount=2_500.0,
                     commission_pct=FEE, slippage_pct=SLIP)
        fill_price = 100.0 * (1 + SLIP / 100)
        expected_qty = 2_500.0 * (1 - FEE / 100) / fill_price
        assert pos.qty == pytest.approx(expected_qty)
        assert pos.entry_price == pytest.approx(fill_price)
        assert pf.cash == pytest.approx(7_500.0)
        assert "X/USDT" in pf.positions

    def test_buy_clamps_to_available_cash(self):
        pf = Portfolio(cash=100.0)
        pf.buy("X/USDT", price=10.0, usdt_amount=1_000.0)
        assert pf.cash == pytest.approx(0.0)

    def test_buy_with_zero_cash_raises(self):
        pf = Portfolio(cash=0.0)
        with pytest.raises(ValueError, match="Cash insufficiente"):
            pf.buy("X/USDT", price=10.0, usdt_amount=100.0)

    def test_buy_invalid_amount_raises(self):
        pf = Portfolio(cash=1_000.0)
        with pytest.raises(ValueError, match="Importo"):
            pf.buy("X/USDT", price=10.0, usdt_amount=0.0)
        with pytest.raises(ValueError, match="Prezzo"):
            pf.buy("X/USDT", price=0.0, usdt_amount=100.0)

    def test_second_buy_averages_entry_price(self):
        pf = Portfolio(cash=10_000.0)
        p1 = pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        p2 = pf.buy("X/USDT", price=200.0, usdt_amount=1_000.0)
        assert p1 is p2
        assert p2.qty > p1.qty or p2.qty == p1.qty  # quantità accumulata
        assert 100.0 < p2.entry_price < 200.0       # media ponderata

    def test_history_records_buys(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        assert len(pf.history) == 1
        h = pf.history[0]
        assert h["side"] == "buy"
        assert h["symbol"] == "X/USDT"
        assert h["usdt"] == pytest.approx(1_000.0)


class TestSell:
    def test_sell_closes_position_and_realizes_pnl(self):
        pf = Portfolio(cash=10_000.0)
        pos = pf.buy("X/USDT", price=100.0, usdt_amount=2_500.0,
                     commission_pct=FEE, slippage_pct=SLIP)
        trade = pf.sell("X/USDT", price=110.0, commission_pct=FEE, slippage_pct=SLIP)
        fill_price = 110.0 * (1 - SLIP / 100)
        proceeds = pos.qty * fill_price * (1 - FEE / 100)
        cost = pos.qty * pos.entry_price
        assert trade["pnl"] == pytest.approx(proceeds - cost)
        assert trade["pnl"] > 0  # +10% di prezzo batte i costi
        assert "X/USDT" not in pf.positions
        assert pf.cash == pytest.approx(7_500.0 + proceeds)

    def test_sell_without_position_raises(self):
        pf = Portfolio(cash=10_000.0)
        with pytest.raises(ValueError, match="Nessuna posizione"):
            pf.sell("X/USDT", price=100.0)

    def test_sell_invalid_price_raises(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        with pytest.raises(ValueError, match="Prezzo"):
            pf.sell("X/USDT", price=0.0)

    def test_history_records_sells_with_pnl(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        pf.sell("X/USDT", price=90.0)
        h = pf.history[-1]
        assert h["side"] == "sell"
        assert h["pnl"] < 0  # perdita


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "portfolio.json"
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=2_500.0)
        pf.save(path)

        loaded = Portfolio.load(path)
        assert loaded.cash == pytest.approx(pf.cash)
        assert set(loaded.positions) == {"X/USDT"}
        assert loaded.positions["X/USDT"].qty == pytest.approx(pf.positions["X/USDT"].qty)
        assert len(loaded.history) == 1

    def test_load_missing_file_uses_initial_cash(self, tmp_path):
        pf = Portfolio.load(tmp_path / "nope.json", initial_cash=5_000.0)
        assert pf.cash == 5_000.0
        assert pf.positions == {}
        assert pf.history == []

    def test_multiple_save_load_cycles(self, tmp_path):
        path = tmp_path / "portfolio.json"
        pf = Portfolio(cash=10_000.0)
        for price in (100.0, 110.0, 90.0):
            pf.buy("X/USDT", price=price, usdt_amount=1_000.0)
            pf.save(path)
            pf = Portfolio.load(path)
        assert pf.cash == pytest.approx(7_000.0)
        assert len(pf.history) == 3


class TestValueAndFormat:
    def test_value_with_prices(self):
        pf = Portfolio(cash=2_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0, slippage_pct=0.0, commission_pct=0.0)
        assert pf.value({"X/USDT": 100.0}) == pytest.approx(2_000.0)  # 1000 cash + 10 unità
        assert pf.value({"X/USDT": 110.0}) == pytest.approx(2_100.0)

    def test_value_without_price_for_position(self):
        pf = Portfolio(cash=1_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=500.0)
        assert pf.value({}) == pytest.approx(pf.cash)

    def test_format_portfolio(self):
        pf = Portfolio(cash=7_500.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=2_500.0)
        out = format_portfolio(pf, {"X/USDT": 110.0})
        assert "Portafoglio simulato" in out
        assert "X/USDT" in out
        assert "Cash" in out
        assert "Totale" in out

    def test_format_portfolio_empty(self):
        out = format_portfolio(Portfolio(cash=10_000.0), {})
        assert "Nessuna posizione aperta" in out
        assert "10,000.00" in out

    def test_format_portfolio_realized_pnl(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        pf.sell("X/USDT", price=110.0)
        out = format_portfolio(pf, {})
        assert "Trade chiusi: 1" in out
        assert "PnL realized" in out
