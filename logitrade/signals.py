"""Controllo in tempo reale dei segnali RSI + MACD.

Legge le candele piu' recenti dall'exchange (senza cache) e valuta le condizioni
della strategia sull'ultima candela CHIUSA. Non esegue alcun ordine.
"""

from dataclasses import dataclass

import pandas as pd

from .breakout import BreakoutParams, generate_signals as breakout_signals
from .config import Settings
from .data import _TIMEFRAME_MS, _exchange
from .indicators import macd as macd_fn
from .indicators import rsi as rsi_fn
from .strategy import StrategyParams, generate_signals

# Candele sufficienti a coprire RSI + MACD con margine (evita il warmup degli indicatori)
WARMUP_CANDLES = 300


def fetch_recent(symbol: str, timeframe: str, exchange_id: str = "okx",
                 limit: int = WARMUP_CANDLES) -> pd.DataFrame:
    """Scarica le ultime `limit` candele (inclusa quella in formazione), senza cache."""
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"Timeframe non supportato: {timeframe}")
    ex = _exchange(exchange_id)
    if symbol not in ex.markets:
        raise ValueError(f"{symbol} non disponibile su {exchange_id}")
    rows = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    if not rows:
        raise ValueError(f"Nessun dato per {symbol} ({timeframe})")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime").drop(columns=["timestamp"]).astype(float)
    return df[~df.index.duplicated(keep="last")]


@dataclass
class SignalSnapshot:
    symbol: str
    timeframe: str
    candle_time: pd.Timestamp  # apertura ultima candela chiusa
    price: float               # close ultima candela chiusa
    rsi: float
    macd_hist: float
    prev_macd_hist: float
    status: str                # "ENTRY" | "EXIT" | "NEUTRAL"
    reason: str
    extra: str = ""             # riga informativa aggiuntiva (es. bande di breakout)


def _strategy_params(settings: Settings) -> StrategyParams:
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


def _breakout_params(settings: Settings) -> BreakoutParams:
    return BreakoutParams(
        entry_lookback=settings.breakout_entry_lookback,
        exit_lookback=settings.breakout_exit_lookback,
        trend_sma_period=settings.trend_sma_period,
        trend_slope_bars=settings.trend_slope_bars,
    )


def check_signal(symbol: str, settings: Settings, timeframe: str | None = None) -> SignalSnapshot:
    """Valuta la strategia sull'ultima candela chiusa (quella in formazione viene ignorata)."""
    tf = timeframe or settings.timeframe
    df = fetch_recent(symbol, tf, settings.exchange_id)
    if settings.strategy_family == "breakout":
        signals = breakout_signals(df, _breakout_params(settings))
    else:
        signals = generate_signals(df, _strategy_params(settings))
    if len(signals) < 2:
        raise ValueError(f"Dati insufficienti per {symbol} ({tf})")

    closed = signals.iloc[:-1]  # scarta la candela in formazione
    last = closed.iloc[-1]
    prev = closed.iloc[-2]

    extra = ""
    if settings.strategy_family == "breakout":
        extra = f"Bande: upper {last['upper']:.8g} | lower {last['lower']:.8g}"
        if bool(last["entry"]):
            status, reason = "ENTRY", (
                f"rottura del massimo delle ultime {settings.breakout_entry_lookback} barre")
        elif bool(last["exit"]):
            status, reason = "EXIT", (
                f"rottura del minimo delle ultime {settings.breakout_exit_lookback} barre")
        else:
            status = "NEUTRAL"
            if last["close"] > last["upper"] * 0.99:
                reason = "vicino alla rottura del massimo"
            elif last["close"] < last["lower"] * 1.01:
                reason = "vicino alla rottura del minimo"
            else:
                reason = "prezzo tra le bande"
    elif bool(last["entry"]):
        status, reason = "ENTRY", "RSI ipervenduto + incrocio MACD rialzista"
    elif bool(last["exit"]):
        status, reason = "EXIT", "RSI ipercomprato o incrocio MACD ribassista"
    else:
        status = "NEUTRAL"
        reasons = []
        if last["rsi"] < settings.rsi_oversold + 10:
            reasons.append("RSI vicino alla zona di ingresso")
        if last["rsi"] > settings.rsi_overbought - 10:
            reasons.append("RSI vicino alla zona di uscita")
        reason = "; ".join(reasons) or "condizioni non allineate"

    rsi_val, hist_val, prev_hist_val = _indicator_value(signals, df, settings, last, prev)
    return SignalSnapshot(
        symbol=symbol,
        timeframe=tf,
        candle_time=closed.index[-1],
        price=float(last["close"]),
        rsi=rsi_val,
        macd_hist=hist_val,
        prev_macd_hist=prev_hist_val,
        status=status,
        reason=reason,
        extra=extra,
    )


def _indicator_value(signals, df, settings, last, prev):
    """Valori RSI/MACD per la visualizzazione: dalle colonne se presenti, altrimenti calcolati."""
    if "rsi" in signals.columns:
        return float(last["rsi"]), float(last["macd_hist"]), float(prev["macd_hist"])
    rsi_series = rsi_fn(df["close"], settings.rsi_period)
    _, _, hist = macd_fn(df["close"], settings.macd_fast, settings.macd_slow, settings.macd_signal)
    return (
        float(rsi_series.loc[last.name]),
        float(hist.loc[last.name]),
        float(hist.loc[prev.name]),
    )
