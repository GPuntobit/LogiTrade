"""Motore di backtest: simulazione long-only con commissioni, slippage, stop loss e take profit."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    commission_pct: float = 0.1     # per lato
    slippage_pct: float = 0.05      # per lato
    position_pct: float = 25.0      # % del capitale per posizione
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 15.0
    trailing_stop_pct: float = 0.0  # 0 = trailing stop disattivato


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    size: float = 0.0               # quantità di asset
    reason: str = ""                # signal | stop_loss | take_profit | trailing_stop
    peak_price: float = 0.0         # massimo close dall'ingresso (per il trailing stop)

    @property
    def return_pct(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price / self.entry_price - 1) * 100


@dataclass
class BacktestResult:
    equity: pd.Series = field(default_factory=pd.Series)
    trades: list[Trade] = field(default_factory=list)
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    n_trades: int = 0
    avg_trade_pct: float = 0.0
    buy_hold_return_pct: float = 0.0


def run_backtest(signals: pd.DataFrame, cfg: BacktestConfig | None = None) -> BacktestResult:
    """Esegue il backtest su un DataFrame con colonne close, entry, exit."""
    cfg = cfg or BacktestConfig()
    required = {"close", "entry", "exit"}
    if not required.issubset(signals.columns):
        raise ValueError(f"Mancano colonne nel DataFrame: {required - set(signals.columns)}")

    cash = cfg.initial_cash
    position: Trade | None = None
    trades: list[Trade] = []
    equity_curve: list[float] = []
    entry_times: list[pd.Timestamp] = []

    fee = cfg.commission_pct / 100
    slip = cfg.slippage_pct / 100

    for ts, row in signals.iterrows():
        price = float(row["close"])

        # gestione posizione aperta: stop loss / take profit / trailing intrabar
        # (sul close, approssimazione conservativa)
        if position is not None:
            change = price / position.entry_price - 1
            hit_sl = change <= -cfg.stop_loss_pct / 100
            hit_tp = change >= cfg.take_profit_pct / 100
            hit_trail = cfg.trailing_stop_pct > 0 and price <= position.peak_price * (1 - cfg.trailing_stop_pct / 100)
            if hit_sl or hit_tp or hit_trail:
                exit_price = price * (1 - slip)
                cash += position.size * exit_price * (1 - fee)
                position.exit_time = ts
                position.exit_price = exit_price
                if hit_sl:
                    position.reason = "stop_loss"
                elif hit_tp:
                    position.reason = "take_profit"
                else:
                    position.reason = "trailing_stop"
                trades.append(position)
                position = None
            elif bool(row["exit"]):
                exit_price = price * (1 - slip)
                cash += position.size * exit_price * (1 - fee)
                position.exit_time = ts
                position.exit_price = exit_price
                position.reason = "signal"
                trades.append(position)
                position = None

        # ingresso
        if position is None and bool(row["entry"]) and ts not in entry_times:
            entry_price = price * (1 + slip)
            invest = cash * cfg.position_pct / 100
            if invest > 0:
                size = invest * (1 - fee) / entry_price
                cash -= invest
                position = Trade(entry_time=ts, entry_price=entry_price, size=size, peak_price=price)
                entry_times.append(ts)
        elif position is not None:
            # aggiorna il massimo DOPO i controlli di uscita (trailing sulla barra precedente)
            position.peak_price = max(position.peak_price, price)

        equity_curve.append(cash + (position.size * price if position else 0.0))

    # chiusura posizione finale
    if position is not None:
        last_price = float(signals["close"].iloc[-1]) * (1 - slip)
        cash += position.size * last_price * (1 - fee)
        position.exit_time = signals.index[-1]
        position.exit_price = last_price
        position.reason = "end_of_data"
        trades.append(position)
        equity_curve[-1] = cash

    equity = pd.Series(equity_curve, index=signals.index, name="equity")

    # metriche
    total_return = (equity.iloc[-1] / cfg.initial_cash - 1) * 100
    running_max = equity.cummax()
    drawdown = (equity / running_max - 1) * 100
    max_dd = float(drawdown.min())

    closed = [t for t in trades if t.exit_price is not None]
    wins = [t for t in closed if t.return_pct > 0]
    losses = [t for t in closed if t.return_pct <= 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0.0
    gross_profit = sum(t.return_pct for t in wins)
    gross_loss = abs(sum(t.return_pct for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    avg_trade = float(np.mean([t.return_pct for t in closed])) if closed else 0.0

    buy_hold = (signals["close"].iloc[-1] / signals["close"].iloc[0] - 1) * 100

    return BacktestResult(
        equity=equity,
        trades=trades,
        final_equity=equity.iloc[-1],
        total_return_pct=total_return,
        max_drawdown_pct=max_dd,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        n_trades=len(closed),
        avg_trade_pct=avg_trade,
        buy_hold_return_pct=buy_hold,
    )
