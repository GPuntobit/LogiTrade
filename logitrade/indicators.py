"""Indicatori tecnici: RSI (Wilder) e MACD."""

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI secondo il metodo di Wilder (smoothing esponenziale alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0).where(avg_loss.notna() | (avg_loss == 0), pd.NA)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: ritorna (macd_line, signal_line, histogram)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
