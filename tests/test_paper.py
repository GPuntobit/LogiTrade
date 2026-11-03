"""Test del portafoglio simulato (paper trading)."""

import pytest

from logitrade.paper import Portfolio, Position
from logitrade.report import format_auto_trade, format_portfolio

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
        # prezzo di carico inclusi costi: investito / quantità
        assert pos.entry_price == pytest.approx(2_500.0 / expected_qty)
        assert pos.entry_price == pytest.approx(fill_price / (1 - FEE / 100))
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


class TestPartialSell:
    def test_partial_sell_keeps_position_and_entry_price(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=2_000.0,
               commission_pct=FEE, slippage_pct=SLIP)
        before = pf.positions["X/USDT"]
        qty_before, entry_before = before.qty, before.entry_price  # valori (no alias)
        trade = pf.sell("X/USDT", price=110.0, commission_pct=FEE, slippage_pct=SLIP,
                        usdt_amount=500.0)
        assert trade["usdt"] == pytest.approx(500.0)       # ricavi netti = importo chiesto
        resid = pf.positions["X/USDT"]
        assert resid.qty == pytest.approx(qty_before - trade["qty"])
        assert resid.entry_price == pytest.approx(entry_before)      # carico invariato
        assert pf.cash == pytest.approx(8_000.0 + 500.0)
        assert trade["pnl"] == pytest.approx(500.0 - trade["qty"] * entry_before)

    def test_partial_sell_history_records_qty_sold(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=2_000.0)
        trade = pf.sell("X/USDT", price=100.0, usdt_amount=1_000.0)
        h = pf.history[-1]
        assert h["qty"] == pytest.approx(trade["qty"])
        assert h["usdt"] == pytest.approx(trade["usdt"])
        assert len(pf.positions) == 1  # posizione ancora aperta

    def test_amount_above_position_closes_it(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        pf.sell("X/USDT", price=100.0, usdt_amount=99_999.0)
        assert pf.positions == {}

    def test_repeated_partial_sells_sum_to_full(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=3_000.0,
               commission_pct=FEE, slippage_pct=SLIP)
        qty0 = pf.positions["X/USDT"].qty
        t1 = pf.sell("X/USDT", price=100.0, commission_pct=FEE, slippage_pct=SLIP,
                     usdt_amount=1_000.0)
        t2 = pf.sell("X/USDT", price=100.0, commission_pct=FEE, slippage_pct=SLIP,
                     usdt_amount=2_000.0)
        assert t1["qty"] + t2["qty"] == pytest.approx(qty0)
        assert pf.positions == {}

    def test_zero_amount_raises(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        with pytest.raises(ValueError, match="Importo"):
            pf.sell("X/USDT", price=100.0, usdt_amount=0.0)

    def test_negative_amount_raises(self):
        pf = Portfolio(cash=10_000.0)
        pf.buy("X/USDT", price=100.0, usdt_amount=1_000.0)
        with pytest.raises(ValueError, match="Importo"):
            pf.sell("X/USDT", price=100.0, usdt_amount=-10.0)


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


class TestAutoTrade:
    def test_entry_signal_buys_when_flat(self):
        pf = Portfolio(cash=10_000.0)
        result = pf.auto_trade("X/USDT", "ENTRY", price=100.0, position_pct=25.0)
        assert result["action"] == "buy"
        assert result["usdt"] == pytest.approx(2_500.0)
        assert "X/USDT" in pf.positions
        assert pf.cash == pytest.approx(7_500.0)

    def test_entry_signal_no_pyramiding(self):
        pf = Portfolio(cash=10_000.0)
        pf.auto_trade("X/USDT", "ENTRY", price=100.0)
        assert pf.auto_trade("X/USDT", "ENTRY", price=110.0) is None
        assert len(pf.history) == 1  # nessun secondo acquisto

    def test_exit_signal_sells_open_position(self):
        pf = Portfolio(cash=10_000.0)
        pf.auto_trade("X/USDT", "ENTRY", price=100.0)
        result = pf.auto_trade("X/USDT", "EXIT", price=110.0)
        assert result["action"] == "sell"
        assert result["pnl"] > 0
        assert "X/USDT" not in pf.positions

    def test_exit_signal_without_position_does_nothing(self):
        pf = Portfolio(cash=10_000.0)
        assert pf.auto_trade("X/USDT", "EXIT", price=100.0) is None
        assert pf.cash == pytest.approx(10_000.0)

    def test_neutral_does_nothing(self):
        pf = Portfolio(cash=10_000.0)
        assert pf.auto_trade("X/USDT", "NEUTRAL", price=100.0) is None
        assert pf.history == []

    def test_buy_without_cash_raises(self):
        pf = Portfolio(cash=0.0)
        with pytest.raises(ValueError, match="Cash insufficiente"):
            pf.auto_trade("X/USDT", "ENTRY", price=100.0)

    def test_full_cycle_pnl(self):
        """Ciclo completo ENTRY→EXIT: il PnL torna col conto economico."""
        pf = Portfolio(cash=10_000.0)
        pf.auto_trade("X/USDT", "ENTRY", price=100.0, position_pct=25.0,
                      commission_pct=0.1, slippage_pct=0.05)
        result = pf.auto_trade("X/USDT", "EXIT", price=100.0, commission_pct=0.1, slippage_pct=0.05)
        # prezzo invariato: la perdita e' esattamente i costi di andata e ritorno
        assert result["pnl"] < 0
        assert pf.cash == pytest.approx(10_000.0 + result["pnl"])  # costo = investito


class TestFormatAutoTrade:
    def test_buy_summary(self):
        out = format_auto_trade({"action": "buy", "symbol": "X/USDT", "price": 100.0,
                                 "qty": 25.0, "usdt": 2_500.0})
        assert "ACQUISTO AUTOMATICO" in out
        assert "SIMULATO" in out
        assert "X/USDT" in out

    def test_sell_summary(self):
        out = format_auto_trade({"action": "sell", "symbol": "X/USDT", "price": 110.0,
                                 "qty": 25.0, "usdt": 2_750.0, "pnl": 250.0})
        assert "VENDEITA AUTOMATICA" in out or "VENDEITA" in out
        assert "PnL" in out


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
