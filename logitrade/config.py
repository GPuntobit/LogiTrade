"""Caricamento configurazione da config.yaml e .env."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    exchange_id: str
    symbols: list[str]
    timeframe: str
    since: str
    initial_cash: float
    commission_pct: float
    slippage_pct: float
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    macd_fast: int
    macd_slow: int
    macd_signal: int
    trend_sma_period: int
    trend_slope_bars: int
    entry_mode: str
    entry_lookback: int
    strategy_family: str
    breakout_entry_lookback: int
    breakout_exit_lookback: int
    position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    signal_check_minutes: int
    telegram_token: str
    telegram_allowed_chat_ids: list[int]


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    with open(config_path or PROJECT_ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    allowed = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids = [int(x) for x in allowed.split(",") if x.strip()]

    return Settings(
        exchange_id=os.getenv("EXCHANGE_ID", cfg["exchange"]["id"]),
        symbols=cfg["symbols"],
        timeframe=cfg["backtest"]["timeframe"],
        since=cfg["backtest"]["since"],
        initial_cash=float(cfg["backtest"]["initial_cash"]),
        commission_pct=float(cfg["backtest"]["commission_pct"]),
        slippage_pct=float(cfg["backtest"]["slippage_pct"]),
        rsi_period=int(cfg["strategy"]["rsi_period"]),
        rsi_oversold=float(cfg["strategy"]["rsi_oversold"]),
        rsi_overbought=float(cfg["strategy"]["rsi_overbought"]),
        macd_fast=int(cfg["strategy"]["macd_fast"]),
        macd_slow=int(cfg["strategy"]["macd_slow"]),
        macd_signal=int(cfg["strategy"]["macd_signal"]),
        trend_sma_period=int(cfg["strategy"].get("trend_sma_period", 0)),
        trend_slope_bars=int(cfg["strategy"].get("trend_slope_bars", 50)),
        entry_mode=str(cfg["strategy"].get("entry_mode", "classic")),
        entry_lookback=int(cfg["strategy"].get("entry_lookback", 5)),
        strategy_family=str(cfg["strategy"].get("family", "rsi_macd")),
        breakout_entry_lookback=int(cfg["strategy"].get("breakout_entry_lookback", 20)),
        breakout_exit_lookback=int(cfg["strategy"].get("breakout_exit_lookback", 10)),
        position_pct=float(cfg["risk"]["position_pct"]),
        stop_loss_pct=float(cfg["risk"]["stop_loss_pct"]),
        take_profit_pct=float(cfg["risk"]["take_profit_pct"]),
        trailing_stop_pct=float(cfg["risk"].get("trailing_stop_pct", 0)),
        signal_check_minutes=int(cfg.get("signal", {}).get("check_interval_minutes", 60)),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_chat_ids=allowed_ids,
    )
