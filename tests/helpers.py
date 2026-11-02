"""Helper condivisi per i test: serie sintetiche con segnali noti."""

import numpy as np
import pandas as pd

from logitrade.config import Settings


def make_closes(n_down: int = 40, n_up: int = 35) -> list[float]:
    """Calo oscillante seguito da rally oscillante.

    Con (40, 35) produce esattamente un segnale di ingresso e uno di uscita
    (verificato empiricamente: entry ~barra 28 con RSI < 30, exit ~barra 43 con RSI > 70).
    Le oscillazioni impediscono i casi degeneri a RSI 0/100 (gain o loss media zero).
    """
    parts: list[float] = []
    p = 100.0
    for i in range(n_down):
        parts.append(p)
        p *= 0.99 if i % 3 else 1.004
    for i in range(n_up):
        parts.append(p)
        p *= 1.012 if i % 3 else 0.997
    return parts


def make_dip_turn_closes(n_down: int = 15, rate_down: float = 0.997,
                         n_up: int = 10, rate_up: float = 1.004) -> list[float]:
    """Discesa con micro-rimbalzi seguita da ripresa graduale.

    Genera un ingresso 'dip_turn' (RSI in ipervenduto e incrocio MACD in barre
    diverse) che la modalita' 'classic' NON intercetta: al momento dell'incrocio
    l'RSI e' gia' risalito sopra 30. Verificato empiricamente.
    """
    parts: list[float] = []
    p = 100.0
    for i in range(n_down):
        parts.append(p)
        p *= rate_down * (1.002 if i % 4 == 0 else 1.0)
    for i in range(n_up):
        parts.append(p)
        p *= rate_up * (0.998 if i % 3 == 0 else 1.0)
    return parts


def build_df(closes: list[float], start: str = "2024-01-01", freq: str = "h") -> pd.DataFrame:
    """DataFrame OHLCV sintetico con indice datetime UTC (come fetch_recent)."""
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000.0},
        index=idx,
    )


def random_walk_closes(n: int, seed: int, drift: float = 0.0, vol: float = 0.02,
                       start: float = 100.0) -> list[float]:
    """Serie sintetica deterministica (seed fissato) come random walk geometrico."""
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, vol)))
    return closes


def make_settings(**overrides) -> Settings:
    """Settings di test con default allineati a config.yaml."""
    defaults = dict(
        exchange_id="binance",
        symbols=["PEPE/USDT", "DOGE/USDT"],
        timeframe="1h",
        since="2024-01-01",
        initial_cash=10_000.0,
        commission_pct=0.1,
        slippage_pct=0.05,
        rsi_period=14,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        trend_sma_period=0,
        trend_slope_bars=50,
        entry_mode="classic",
        entry_lookback=5,
        strategy_family="rsi_macd",
        breakout_entry_lookback=20,
        breakout_exit_lookback=10,
        position_pct=25.0,
        stop_loss_pct=5.0,
        take_profit_pct=15.0,
        trailing_stop_pct=0.0,
        signal_check_minutes=60,
        telegram_token="",
        telegram_allowed_chat_ids=[],
    )
    defaults.update(overrides)
    return Settings(**defaults)
