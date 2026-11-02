"""Orchestrazione: dati -> segnali -> backtest -> risultato."""

from dataclasses import dataclass

import pandas as pd

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .breakout import BreakoutParams
from .breakout import generate_signals as breakout_signals
from .config import Settings
from .data import fetch_ohlcv
from .strategy import StrategyParams
from .strategy import generate_signals as rsi_macd_signals


@dataclass
class RunOutput:
    symbol: str
    timeframe: str
    since: str
    result: BacktestResult


def build_strategy_params(settings: Settings) -> StrategyParams | BreakoutParams:
    """Costruisce i parametri della strategia configurata (strategy.family)."""
    if settings.strategy_family == "rsi_macd":
        return StrategyParams(
            rsi_period=settings.rsi_period,
            rsi_oversold=settings.rsi_oversold,
            rsi_overbought=settings.rsi_overbought,
            macd_fast=settings.macd_fast,
            macd_slow=settings.macd_slow,
            macd_signal=settings.macd_signal,
            trend_sma_period=settings.trend_sma_period,
            trend_slope_bars=settings.trend_slope_bars,
            entry_mode=settings.entry_mode,
            entry_lookback=settings.entry_lookback,
        )
    if settings.strategy_family == "breakout":
        return BreakoutParams(
            entry_lookback=settings.breakout_entry_lookback,
            exit_lookback=settings.breakout_exit_lookback,
            trend_sma_period=settings.trend_sma_period,
            trend_slope_bars=settings.trend_slope_bars,
        )
    raise ValueError(f"strategy.family sconosciuto: {settings.strategy_family!r}")


def build_signals(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Genera i segnali con la famiglia di strategia configurata."""
    params = build_strategy_params(settings)
    if settings.strategy_family == "rsi_macd":
        return rsi_macd_signals(df, params)
    return breakout_signals(df, params)


def run_symbol(symbol: str, settings: Settings, timeframe: str | None = None,
               since: str | None = None) -> RunOutput:
    tf = timeframe or settings.timeframe
    start = since or settings.since

    df = fetch_ohlcv(symbol, tf, start, settings.exchange_id)
    signals = build_signals(df, settings)
    result = run_backtest(signals, BacktestConfig(
        initial_cash=settings.initial_cash,
        commission_pct=settings.commission_pct,
        slippage_pct=settings.slippage_pct,
        position_pct=settings.position_pct,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        trailing_stop_pct=settings.trailing_stop_pct,
    ))
    return RunOutput(symbol=symbol, timeframe=tf, since=start, result=result)


def compare_timeframes(symbols: list[str], settings: Settings,
                       timeframes: list[str]) -> dict[str, dict[str, BacktestResult | None]]:
    """Esegue il backtest di ogni simbolo su piu' timeframe.

    Ritorna {timeframe: {simbolo: BacktestResult}}; in caso di errore su una
    coppia (simbolo, timeframe) il valore e' None.
    """
    results: dict[str, dict[str, BacktestResult | None]] = {}
    for tf in timeframes:
        per_symbol: dict[str, BacktestResult | None] = {}
        for symbol in symbols:
            try:
                per_symbol[symbol] = run_symbol(symbol, settings, tf).result
            except Exception:  # noqa: BLE001 - una coppia fallita non invalida il confronto
                per_symbol[symbol] = None
        results[tf] = per_symbol
    return results
