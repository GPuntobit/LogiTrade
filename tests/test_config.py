"""Test del caricamento della configurazione."""

from logitrade.config import load_settings

MINIMAL_YAML = """
exchange:
  id: binance
  quote: USDT

symbols:
  - TEST/USDT

backtest:
  timeframe: 4h
  since: "2023-01-01"
  initial_cash: 5000
  commission_pct: 0.2
  slippage_pct: 0.1

strategy:
  rsi_period: 21
  rsi_oversold: 25
  rsi_overbought: 75
  macd_fast: 8
  macd_slow: 21
  macd_signal: 5

risk:
  position_pct: 10
  stop_loss_pct: 3
  take_profit_pct: 9
"""


def test_loads_project_config():
    s = load_settings()
    assert "PEPE/USDT" in s.symbols
    assert s.exchange_id == "okx"
    assert s.timeframe == "1d"
    assert s.rsi_period == 14
    assert s.stop_loss_pct == 5.0
    assert s.take_profit_pct == 15.0


def test_signal_check_minutes_from_project_config():
    assert load_settings().signal_check_minutes == 60


def test_trend_sma_from_project_config():
    assert load_settings().trend_sma_period == 0  # filtro off con breakout
    assert load_settings().trend_slope_bars == 50


def test_entry_mode_from_project_config():
    s = load_settings()
    assert s.entry_mode == "classic"
    assert s.entry_lookback == 5


def test_trailing_stop_from_project_config():
    assert load_settings().trailing_stop_pct == 0.0


def test_strategy_family_from_project_config():
    s = load_settings()
    assert s.strategy_family == "breakout"
    assert s.breakout_entry_lookback == 20
    assert s.breakout_exit_lookback == 10


def test_custom_config_values(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINIMAL_YAML, encoding="utf-8")
    s = load_settings(cfg)
    assert s.symbols == ["TEST/USDT"]
    assert s.timeframe == "4h"
    assert s.initial_cash == 5000.0
    assert s.commission_pct == 0.2
    assert s.rsi_period == 21
    assert s.macd_slow == 21
    assert s.position_pct == 10.0


def test_missing_signal_section_defaults_to_60(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINIMAL_YAML, encoding="utf-8")  # senza sezione signal
    assert load_settings(cfg).signal_check_minutes == 60


def test_missing_trend_sma_defaults_to_off(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINIMAL_YAML, encoding="utf-8")  # senza trend_sma_period
    s = load_settings(cfg)
    assert s.trend_sma_period == 0
    assert s.trend_slope_bars == 50
    assert s.entry_mode == "classic"
    assert s.entry_lookback == 5
    assert s.strategy_family == "rsi_macd"
    assert s.breakout_entry_lookback == 20
    assert s.breakout_exit_lookback == 10


def test_signal_section_honored(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINIMAL_YAML + "\nsignal:\n  check_interval_minutes: 15\n", encoding="utf-8")
    assert load_settings(cfg).signal_check_minutes == 15
