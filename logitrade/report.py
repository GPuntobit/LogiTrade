"""Formattazione dei risultati di backtest (testo e grafico)."""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .backtest import BacktestResult
from .config import Settings
from .paper import Portfolio
from .signals import SignalSnapshot


def format_report(symbol: str, timeframe: str, since: str, res: BacktestResult) -> str:
    pf = "∞" if res.profit_factor == float("inf") else f"{res.profit_factor:.2f}"
    lines = [
        f"📊 <b>Backtest {symbol}</b> ({timeframe}, dal {since})",
        "",
        f"💰 Ritorno netto: <b>{res.total_return_pct:+.2f}%</b>  (buy&hold: {res.buy_hold_return_pct:+.2f}%)",
        f"💵 Equity finale: {res.final_equity:,.2f} USDT",
        f"📉 Max drawdown: {res.max_drawdown_pct:.2f}%",
        f"🎯 Trade chiusi: {res.n_trades}  |  Win rate: {res.win_rate_pct:.1f}%",
        f"⚖️ Profit factor: {pf}  |  Media per trade: {res.avg_trade_pct:+.2f}%",
    ]
    if res.trades:
        last = res.trades[-1]
        status = f"chiuso ({last.reason})" if last.exit_price else "APERTA"
        lines += ["", f"Ultimo trade: {status}  |  ingresso {last.entry_price:.8g}"]
    return "\n".join(lines)


def format_signal(snap: SignalSnapshot, settings: Settings) -> str:
    """Report HTML dettagliato del segnale live (per /signal SYMBOL e gli alert di /watch)."""
    if snap.macd_hist > 0:
        momentum = "rialzista"
    else:
        momentum = "ribassista"
    momentum += ", in espansione" if abs(snap.macd_hist) > abs(snap.prev_macd_hist) else ", in attenuazione"

    lines = [
        f"📡 <b>{snap.symbol}</b> — {snap.timeframe}",
        f"🕐 Candela chiusa: {snap.candle_time:%Y-%m-%d %H:%M} UTC",
        f"💰 Prezzo: {snap.price:.8g}",
        "",
        f"📈 RSI({settings.rsi_period}): {snap.rsi:.1f}  "
        f"(ingresso &lt; {settings.rsi_oversold:g}, uscita &gt; {settings.rsi_overbought:g})",
        f"📊 MACD hist: {snap.macd_hist:+.2e}  (momentum {momentum})",
    ]
    if snap.extra:
        lines.append(f"🎯 {snap.extra}")

    if snap.status == "ENTRY":
        sl = snap.price * (1 - settings.stop_loss_pct / 100)
        tp = snap.price * (1 + settings.take_profit_pct / 100)
        lines += [
            "",
            f"🚀 <b>SEGNALE DI INGRESSO</b> — {snap.reason}",
            f"Ingresso ~{snap.price:.8g} | SL {sl:.8g} (-{settings.stop_loss_pct:g}%) | "
            f"TP {tp:.8g} (+{settings.take_profit_pct:g}%)",
            f"Size consigliata: {settings.position_pct:g}% del capitale",
        ]
    elif snap.status == "EXIT":
        lines += ["", f"🔔 <b>SEGNALE DI USCITA</b> — {snap.reason}"]
    else:
        lines += ["", f"⚪ <b>NESSUN SEGNALE</b> — {snap.reason}"]

    return "\n".join(lines)


def format_signal_line(snap: SignalSnapshot) -> str:
    """Riga compatta per lo scan di tutti i simboli (/signal senza argomenti)."""
    icon = {"ENTRY": "🚀", "EXIT": "🔔", "NEUTRAL": "⚪"}[snap.status]
    return f"{icon} {snap.symbol}: {snap.status} | RSI {snap.rsi:.1f} | prezzo {snap.price:.8g}"


def format_portfolio(pf: Portfolio, prices: dict[str, float]) -> str:
    """Report HTML del portafoglio simulato (paper trading)."""
    lines = [
        "💼 <b>Portafoglio simulato</b> (paper trading, nessun ordine reale)",
        "",
        f"💵 Cash: {pf.cash:,.2f} USDT",
    ]
    total = pf.cash
    if pf.positions:
        lines.append("")
        lines.append("Posizioni aperte:")
        for sym, pos in pf.positions.items():
            price = prices.get(sym)
            if price:
                value = pos.qty * price
                pnl_pct = (price / pos.entry_price - 1) * 100
                total += value
                lines.append(
                    f"• {sym}: {pos.qty:.6g} @ {pos.entry_price:.8g} | "
                    f"ora {price:.8g} | PnL <b>{pnl_pct:+.2f}%</b>"
                )
            else:
                lines.append(f"• {sym}: {pos.qty:.6g} @ {pos.entry_price:.8g} | prezzo non disponibile")
    else:
        lines.append("Nessuna posizione aperta.")
    lines += ["", f"💰 Totale: {total:,.2f} USDT"]

    closed = [h for h in pf.history if h["side"] == "sell"]
    if closed:
        realized = sum(h.get("pnl", 0.0) for h in closed)
        lines.append(f"📊 Trade chiusi: {len(closed)} | PnL realized: {realized:+,.2f} USDT")
    return "\n".join(lines)


def equity_chart(res: BacktestResult, symbol: str) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(res.equity.index, res.equity.values, color="#1f77b4", linewidth=1.5)
    ax.set_title(f"Equity curve - {symbol}")
    ax.set_ylabel("USDT")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
