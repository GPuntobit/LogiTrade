"""Strategia RSI + MACD con filtro di trend opzionale.

Regole (solo long, tipico per meme coin spot):
- INGRESSO: dipende da entry_mode:
  - "classic": RSI < soglia ipervenduto E MACD incrocia al rialzo la signal line
    (stessa barra: condizione rara su 4h/1d)
  - "dip_turn": RSI < soglia ipervenduto entro le ultime entry_lookback barre
    E MACD incrocia al rialzo la signal line (dip e rimbalzo disaccoppiati)
- USCITA:  RSI > soglia ipercomprato OPPURE MACD line incrocia al ribasso la signal line
           (stop loss / take profit gestiti dal motore di backtest)

Il filtro di trend usa la PENDENZA della SMA (SMA > SMA di trend_slope_bars fa)
perché il prezzo > SMA confligge con gli ingressi da ipervenduto: il rimbalzo
avviene tipicamente ancora sotto la media. Verificato empiricamente: con
prezzo > SMA(200) non viene generato alcun trade.
"""

from dataclasses import dataclass

import pandas as pd

from .indicators import macd, rsi


@dataclass
class StrategyParams:
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    trend_sma_period: int = 0  # 0 = filtro di trend disattivato
    trend_slope_bars: int = 50  # barre su cui la SMA deve essere crescente
    entry_mode: str = "classic"  # "classic" (stessa barra) | "dip_turn" (dip + rimbalzo)
    entry_lookback: int = 5      # barre di lookback per il dip in modalità dip_turn


def generate_signals(df: pd.DataFrame, params: StrategyParams | None = None) -> pd.DataFrame:
    """Aggiunge al DataFrame le colonne rsi, macd, macd_signal, macd_hist, entry, exit."""
    p = params or StrategyParams()
    out = df.copy()

    out["rsi"] = rsi(out["close"], p.rsi_period)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(
        out["close"], p.macd_fast, p.macd_slow, p.macd_signal
    )

    macd_cross_up = (out["macd_hist"] > 0) & (out["macd_hist"].shift(1) <= 0)
    macd_cross_down = (out["macd_hist"] < 0) & (out["macd_hist"].shift(1) >= 0)

    if p.entry_mode == "classic":
        out["entry"] = (out["rsi"] < p.rsi_oversold) & macd_cross_up
    elif p.entry_mode == "dip_turn":
        # RSI in ipervenduto entro le ultime entry_lookback barre, rimbalzo MACD ora
        dipped = out["rsi"].rolling(p.entry_lookback, min_periods=1).min() < p.rsi_oversold
        out["entry"] = dipped & macd_cross_up
    else:
        raise ValueError(f"entry_mode sconosciuto: {p.entry_mode!r}")
    out["exit"] = (out["rsi"] > p.rsi_overbought) | macd_cross_down

    if p.trend_sma_period > 0:
        # long solo se la SMA di trend e' in pendenza positiva (SMA > SMA di K bar fa)
        out["sma"] = out["close"].rolling(p.trend_sma_period).mean()
        out["trend_up"] = out["sma"] > out["sma"].shift(p.trend_slope_bars)
        out["entry"] = out["entry"] & out["trend_up"]

    return out.dropna(subset=["rsi", "macd"])
