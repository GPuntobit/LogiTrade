"""Strategia breakout (Donchian) per trend-following.

Famiglia alternativa a RSI/MACD, pensata per timeframe superiori dove la
mean-reversion non ha edge (vedi TASKS.md, task 4).

Regole (solo long):
- INGRESSO: close > massimo high delle ultime entry_lookback barre PRECEDENTI
- USCITA:  close < minimo low delle ultime exit_lookback barre PRECEDENTI
- Filtro trend opzionale (pendenza della SMA), condiviso con la strategia RSI/MACD

Il breakout compra la forza e taglia la debolezza: molte piccole perdite
(lo stop e' il minimo recente) e pochi grandi vincitori (il trend si cavalca
fino alla rottura del minimo).
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class BreakoutParams:
    entry_lookback: int = 20    # rottura del massimo delle N barre precedenti
    exit_lookback: int = 10     # rottura del minimo delle M barre precedenti
    trend_sma_period: int = 0   # 0 = filtro di trend disattivato
    trend_slope_bars: int = 50  # barre su cui la SMA deve essere crescente


def generate_signals(df: pd.DataFrame, params: BreakoutParams | None = None) -> pd.DataFrame:
    """Aggiunge al DataFrame le colonne upper, lower, entry, exit (e trend_up se filtro attivo)."""
    p = params or BreakoutParams()
    out = df.copy()

    # bande sulle barre PRECEDENTI (shift(1)): la barra corrente non entra nel calcolo
    out["upper"] = out["high"].rolling(p.entry_lookback).max().shift(1)
    out["lower"] = out["low"].rolling(p.exit_lookback).min().shift(1)

    out["entry"] = out["close"] > out["upper"]
    out["exit"] = out["close"] < out["lower"]

    if p.trend_sma_period > 0:
        out["sma"] = out["close"].rolling(p.trend_sma_period).mean()
        out["trend_up"] = out["sma"] > out["sma"].shift(p.trend_slope_bars)
        out["entry"] = out["entry"] & out["trend_up"]

    return out.dropna(subset=["upper", "lower"])
