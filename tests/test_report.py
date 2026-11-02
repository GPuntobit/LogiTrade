"""Test della formattazione dei report (backtest e segnali live)."""

import pandas as pd

from logitrade.backtest import BacktestResult, Trade
from logitrade.report import format_report, format_signal, format_signal_line
from logitrade.signals import SignalSnapshot

from helpers import make_settings


def make_snapshot(**overrides) -> SignalSnapshot:
    base = dict(
        symbol="PEPE/USDT", timeframe="1h",
        candle_time=pd.Timestamp("2024-06-01 12:00", tz="UTC"),
        price=0.001, rsi=25.0, macd_hist=1e-6, prev_macd_hist=-1e-6,
        status="NEUTRAL", reason="condizioni non allineate",
    )
    base.update(overrides)
    return SignalSnapshot(**base)


class TestFormatSignal:
    def test_entry_includes_sl_tp_and_size(self):
        settings = make_settings()
        out = format_signal(make_snapshot(status="ENTRY", reason="RSI ipervenduto"), settings)
        assert "SEGNALE DI INGRESSO" in out
        assert "SL 0.00095" in out      # 0.001 * (1 - 5%)
        assert "TP 0.00115" in out      # 0.001 * (1 + 15%)
        assert "25% del capitale" in out
        assert "RSI ipervenduto" in out

    def test_exit_block(self):
        out = format_signal(make_snapshot(status="EXIT", reason="incrocio ribassista"), make_settings())
        assert "SEGNALE DI USCITA" in out
        assert "incrocio ribassista" in out

    def test_neutral_block(self):
        out = format_signal(make_snapshot(), make_settings())
        assert "NESSUN SEGNALE" in out
        assert "condizioni non allineate" in out

    def test_always_contains_header_and_indicators(self):
        out = format_signal(make_snapshot(), make_settings())
        assert "PEPE/USDT" in out
        assert "2024-06-01 12:00" in out
        assert "RSI(14): 25.0" in out
        assert "MACD hist" in out

    def test_momentum_expansion_vs_attenuation(self):
        expanding = format_signal(make_snapshot(macd_hist=2e-6, prev_macd_hist=1e-6), make_settings())
        fading = format_signal(make_snapshot(macd_hist=1e-6, prev_macd_hist=2e-6), make_settings())
        assert "in espansione" in expanding
        assert "in attenuazione" in fading

    def test_momentum_direction(self):
        up = format_signal(make_snapshot(macd_hist=1e-6), make_settings())
        down = format_signal(make_snapshot(macd_hist=-1e-6), make_settings())
        assert "rialzista" in up
        assert "ribassista" in down

    def test_html_escaped_thresholds(self):
        """Le soglie < e > devono essere entity HTML (ParseMode.HTML)."""
        out = format_signal(make_snapshot(), make_settings())
        assert "&lt; 30" in out
        assert "&gt; 70" in out


class TestFormatSignalLine:
    def test_icons_per_status(self):
        assert format_signal_line(make_snapshot(status="ENTRY")).startswith("🚀")
        assert format_signal_line(make_snapshot(status="EXIT")).startswith("🔔")
        assert format_signal_line(make_snapshot(status="NEUTRAL")).startswith("⚪")

    def test_contains_key_data(self):
        line = format_signal_line(make_snapshot())
        assert "PEPE/USDT" in line
        assert "NEUTRAL" in line
        assert "25.0" in line


class TestFormatReport:
    def test_backtest_report_with_open_trade(self):
        res = BacktestResult(
            final_equity=10_500.0, total_return_pct=5.0, max_drawdown_pct=-2.0,
            win_rate_pct=60.0, profit_factor=2.0, n_trades=5, avg_trade_pct=1.0,
            buy_hold_return_pct=10.0,
            trades=[Trade(entry_time=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0)],
        )
        out = format_report("PEPE/USDT", "1h", "2024-01-01", res)
        assert "Backtest PEPE/USDT" in out
        assert "APERTA" in out
        assert "+5.00%" in out

    def test_infinite_profit_factor(self):
        res = BacktestResult(profit_factor=float("inf"), n_trades=1, win_rate_pct=100.0)
        out = format_report("X/USDT", "1h", "2024-01-01", res)
        assert "∞" in out
