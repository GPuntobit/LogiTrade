"""Portafoglio simulato (paper trading) per i bottoni BUY/SELL di Telegram.

Nessun ordine reale viene eseguito: le operazioni sono simulate in locale
(paper_portfolio.json) applicando le stesse commissioni e slippage del backtest.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT

PORTFOLIO_FILE = PROJECT_ROOT / "paper_portfolio.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float   # prezzo medio di carico (inclusi costi)
    entry_time: str


@dataclass
class Portfolio:
    cash: float = 10_000.0
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    # --- persistenza ---

    @classmethod
    def load(cls, path: Path = PORTFOLIO_FILE, initial_cash: float = 10_000.0) -> "Portfolio":
        if not path.exists():
            return cls(cash=initial_cash)
        data = json.loads(path.read_text(encoding="utf-8"))
        positions = {s: Position(**p) for s, p in data.get("positions", {}).items()}
        return cls(cash=float(data["cash"]), positions=positions, history=data.get("history", []))

    def save(self, path: Path = PORTFOLIO_FILE) -> None:
        data = {
            "cash": self.cash,
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "history": self.history,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # --- operazioni simulate ---

    def buy(self, symbol: str, price: float, usdt_amount: float,
            commission_pct: float = 0.1, slippage_pct: float = 0.05) -> Position:
        """Compra con `usdt_amount` USDT (al massimo il cash disponibile)."""
        if price <= 0:
            raise ValueError("Prezzo deve essere positivo")
        if usdt_amount <= 0:
            raise ValueError("Importo deve essere positivo")
        amount = min(usdt_amount, self.cash)
        if amount <= 0:
            raise ValueError("Cash insufficiente")

        fee = commission_pct / 100
        slip = slippage_pct / 100
        fill_price = price * (1 + slip)
        qty = amount * (1 - fee) / fill_price
        self.cash -= amount

        pos = self.positions.get(symbol)
        if pos:
            total_qty = pos.qty + qty
            pos.entry_price = (pos.entry_price * pos.qty + fill_price * qty) / total_qty
            pos.qty = total_qty
        else:
            pos = Position(symbol=symbol, qty=qty, entry_price=fill_price, entry_time=_now())
            self.positions[symbol] = pos

        self.history.append({"time": _now(), "side": "buy", "symbol": symbol,
                             "price": fill_price, "qty": qty, "usdt": amount})
        return pos

    def sell(self, symbol: str, price: float,
             commission_pct: float = 0.1, slippage_pct: float = 0.05) -> dict:
        """Chiude l'intera posizione per `symbol` e ritorna il riepilogo con PnL realized."""
        pos = self.positions.get(symbol)
        if pos is None:
            raise ValueError(f"Nessuna posizione aperta per {symbol}")
        if price <= 0:
            raise ValueError("Prezzo deve essere positivo")

        fee = commission_pct / 100
        slip = slippage_pct / 100
        fill_price = price * (1 - slip)
        proceeds = pos.qty * fill_price * (1 - fee)
        cost = pos.qty * pos.entry_price
        pnl = proceeds - cost

        self.cash += proceeds
        del self.positions[symbol]
        self.history.append({"time": _now(), "side": "sell", "symbol": symbol,
                             "price": fill_price, "qty": pos.qty, "usdt": proceeds, "pnl": pnl})
        return {"symbol": symbol, "price": fill_price, "qty": pos.qty,
                "usdt": proceeds, "pnl": pnl}

    def value(self, prices: dict[str, float]) -> float:
        """Valore totale a mercato: cash + posizioni valorizzate ai prezzi dati."""
        total = self.cash
        for sym, pos in self.positions.items():
            if sym in prices:
                total += pos.qty * prices[sym]
        return total
