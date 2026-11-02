"""Interfaccia Telegram per LogiTrade.

Comandi:
  /help                          - mostra i comandi
  /symbols                       - meme coin monitorati
  /backtest SYMBOL [tf] [data]   - es. /backtest PEPE/USDT 1h 2024-01-01
  /scan [tf]                     - backtest di tutti i simboli configurati
  /signal [SYMBOL] [tf]          - segnale live (tutti i simboli se omesso)
  /watch [on [tf] | off]         - alert automatici ogni N minuti
  /portfolio                     - portafoglio simulato (paper trading)

Bottoni 🟢BUY / 🔴SELL: operazioni SIMULATE, nessun ordine reale.
"""

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import Settings, load_settings
from .data import fetch_last_price
from .paper import Portfolio
from .report import equity_chart, format_portfolio, format_report, format_signal, format_signal_line
from .runner import run_symbol
from .signals import check_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIMEFRAMES = {"5m", "15m", "30m", "1h", "4h", "1d"}


def _trade_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 BUY", callback_data=f"buy:{symbol}"),
        InlineKeyboardButton("🔴 SELL", callback_data=f"sell:{symbol}"),
    ]])


def _authorized(settings: Settings):
    async def guard(update: Update) -> bool:
        if not settings.telegram_allowed_chat_ids:
            return True
        if update.effective_chat and update.effective_chat.id in settings.telegram_allowed_chat_ids:
            return True
        if update.message:
            await update.message.reply_text("⛔ Chat non autorizzata.")
        return False
    return guard


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 LogiTrade - backtest meme coin RSI + MACD\n\n"
        "/symbols - lista simboli monitorati\n"
        "/backtest SYMBOL [tf] [data] - backtest singolo\n"
        "   es: /backtest PEPE/USDT 1h 2024-01-01\n"
        "/scan [tf] - backtest di tutti i simboli\n"
        "/signal [SYMBOL] [tf] - segnale live sulla candela chiusa\n"
        "/watch [on [tf] | off] - alert automatici quando parte un segnale\n"
        "/portfolio - portafoglio simulato\n\n"
        "🟢BUY / 🔴SELL: operazioni simulate (paper trading)"
    )


async def cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    await update.message.reply_text("📈 Simboli monitorati:\n" + "\n".join(f"• {s}" for s in settings.symbols))


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    args = ctx.args or []
    if not args:
        await update.message.reply_text("Uso: /backtest SYMBOL [tf] [data]  es: /backtest PEPE/USDT 1h 2024-01-01")
        return

    symbol = args[0].upper()
    timeframe = args[1] if len(args) > 1 else settings.timeframe
    since = args[2] if len(args) > 2 else settings.since
    if timeframe not in TIMEFRAMES:
        await update.message.reply_text(f"Timeframe non valido: {timeframe}. Usa uno di: {', '.join(sorted(TIMEFRAMES))}")
        return

    status = await update.message.reply_text(f"⏳ Backtest {symbol} ({timeframe}) in corso...")
    try:
        out = run_symbol(symbol, settings, timeframe, since)
        report = format_report(out.symbol, out.timeframe, out.since, out.result)
        await status.delete()
        await update.message.reply_text(report, parse_mode=ParseMode.HTML)
        await update.message.reply_photo(equity_chart(out.result, out.symbol), caption="Curva di equity")
    except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
        logger.exception("Backtest fallito")
        await status.edit_text(f"❌ Errore: {html.escape(str(e))}")


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    timeframe = (ctx.args[0] if ctx.args else settings.timeframe)
    if timeframe not in TIMEFRAMES:
        await update.message.reply_text(f"Timeframe non valido: {timeframe}")
        return

    status = await update.message.reply_text(f"⏳ Scan di {len(settings.symbols)} simboli ({timeframe})...")
    summary = [f"📊 <b>Scan {timeframe}</b> (dal {settings.since})", ""]
    for symbol in settings.symbols:
        try:
            out = run_symbol(symbol, settings, timeframe)
            r = out.result
            summary.append(
                f"{symbol}: netto <b>{r.total_return_pct:+.2f}%</b> | dd {r.max_drawdown_pct:.1f}% | "
                f"win {r.win_rate_pct:.0f}% | {r.n_trades} trade"
            )
        except Exception as e:  # noqa: BLE001
            summary.append(f"{symbol}: ❌ {html.escape(str(e))}")
    await status.delete()
    await update.message.reply_text("\n".join(summary), parse_mode=ParseMode.HTML)


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    pf = Portfolio.load(initial_cash=settings.initial_cash)
    status = await update.message.reply_text("⏳ Valutazione portafoglio...")
    prices: dict[str, float] = {}
    for symbol in pf.positions:
        try:
            prices[symbol] = fetch_last_price(symbol, settings.exchange_id)
        except Exception:  # noqa: BLE001
            logger.exception("Prezzo non disponibile per %s", symbol)
    await status.delete()
    await update.message.reply_text(format_portfolio(pf, prices), parse_mode=ParseMode.HTML)


async def cb_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Bottoni 🟢BUY / 🔴SELL: operazione SIMULATA sul portafoglio paper."""
    query = update.callback_query
    settings: Settings = ctx.bot_data["settings"]
    action, _, symbol = query.data.partition(":")
    try:
        price = fetch_last_price(symbol, settings.exchange_id)
        pf = Portfolio.load(initial_cash=settings.initial_cash)
        if action == "buy":
            usdt = pf.cash * settings.position_pct / 100
            pos = pf.buy(symbol, price, usdt, settings.commission_pct, settings.slippage_pct)
            pf.save()
            msg = (
                f"🟢 <b>Acquisto SIMULATO</b> {symbol}\n"
                f"Quantità: {pos.qty:.6g} @ {pos.entry_price:.8g}\n"
                f"Investito: {usdt:,.2f} USDT | Cash rimanente: {pf.cash:,.2f} USDT"
            )
        elif action == "sell":
            trade = pf.sell(symbol, price, settings.commission_pct, settings.slippage_pct)
            pf.save()
            msg = (
                f"🔴 <b>Vendita SIMULATA</b> {symbol}\n"
                f"Quantità: {trade['qty']:.6g} @ {trade['price']:.8g}\n"
                f"Incassato: {trade['usdt']:,.2f} USDT | PnL: {trade['pnl']:+,.2f} USDT\n"
                f"Cash: {pf.cash:,.2f} USDT"
            )
        else:
            msg = f"Azione sconosciuta: {action}"
        await query.answer()
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
    except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
        logger.exception("Operazione simulata fallita")
        await query.answer(f"Errore: {e}", show_alert=True)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_help(update, ctx)


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    args = ctx.args or []

    if args:  # dettaglio su un simbolo
        symbol = args[0].upper()
        tf = args[1] if len(args) > 1 else settings.timeframe
        if tf not in TIMEFRAMES:
            await update.message.reply_text(f"Timeframe non valido: {tf}. Usa uno di: {', '.join(sorted(TIMEFRAMES))}")
            return
        status = await update.message.reply_text(f"⏳ Controllo segnale {symbol} ({tf})...")
        try:
            snap = check_signal(symbol, settings, tf)
            await status.delete()
            await update.message.reply_text(format_signal(snap, settings), parse_mode=ParseMode.HTML,
                                            reply_markup=_trade_keyboard(symbol))
        except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
            logger.exception("Controllo segnale fallito")
            await status.edit_text(f"❌ Errore: {html.escape(str(e))}")
        return

    # scan compatto di tutti i simboli
    tf = settings.timeframe
    status = await update.message.reply_text(f"⏳ Controllo segnali ({tf})...")
    lines = [f"📡 <b>Segnali live</b> ({tf}) — candela chiusa", ""]
    for symbol in settings.symbols:
        try:
            snap = check_signal(symbol, settings, tf)
            lines.append(format_signal_line(snap))
        except Exception as e:  # noqa: BLE001
            lines.append(f"{symbol}: ❌ {html.escape(str(e))}")
    await status.delete()
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.bot_data["settings"]
    watch = ctx.bot_data["watch"]
    args = ctx.args or []
    chat_id = update.effective_chat.id

    if not args:
        state = "🟢 attivo" if watch["enabled"] else "⚪ spento"
        subs = ", ".join(str(c) for c in sorted(watch["chats"])) or "nessuna"
        await update.message.reply_text(
            f"👁 Watch: {state} | intervallo {watch['interval_minutes']} min | tf {watch['timeframe']}\n"
            f"Chat iscritte: {subs}\n\n"
            "Comandi: /watch on [tf] · /watch off"
        )
        return

    action = args[0].lower()
    if action == "on":
        tf = args[1] if len(args) > 1 else settings.timeframe
        if tf not in TIMEFRAMES:
            await update.message.reply_text(f"Timeframe non valido: {tf}")
            return
        watch["timeframe"] = tf
        watch["chats"].add(chat_id)
        watch["enabled"] = True
        await update.message.reply_text(
            f"👁 Watch attivo ogni {watch['interval_minutes']} min su {tf}.\n"
            "Riceverai un alert solo quando si genera un segnale di INGRESSO o USCITA."
        )
    elif action == "off":
        watch["chats"].discard(chat_id)
        if not watch["chats"]:
            watch["enabled"] = False
        await update.message.reply_text("👁 Watch disattivato per questa chat.")
    else:
        await update.message.reply_text("Uso: /watch [on [tf] | off]")


async def watch_loop(app: Application) -> None:
    """Controlla periodicamente i segnali e invia alert alle chat iscritte (nessun ordine eseguito)."""
    settings: Settings = app.bot_data["settings"]
    watch = app.bot_data["watch"]
    while True:
        await asyncio.sleep(watch["interval_minutes"] * 60)
        if not watch["enabled"] or not watch["chats"]:
            continue
        for symbol in settings.symbols:
            try:
                snap = check_signal(symbol, settings, watch["timeframe"])
            except Exception:  # noqa: BLE001
                logger.exception("Watch: controllo fallito per %s", symbol)
                continue
            if snap.status == "NEUTRAL":
                continue
            key = (symbol, snap.status, snap.candle_time)
            if key in watch["seen"]:
                continue
            watch["seen"].add(key)
            if len(watch["seen"]) > 1000:
                watch["seen"].clear()
            text = format_signal(snap, settings)
            for cid in sorted(watch["chats"]):
                try:
                    await app.bot.send_message(cid, text, parse_mode=ParseMode.HTML,
                                               reply_markup=_trade_keyboard(symbol))
                except Exception:  # noqa: BLE001
                    logger.exception("Watch: invio fallito verso %s", cid)


def main() -> None:
    settings = load_settings()
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante: copia .env.example in .env e inserisci il token di @BotFather")

    guard = _authorized(settings)

    async def wrapped(handler):
        async def inner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            if await guard(update):
                await handler(update, ctx)
        return inner

    async def build() -> None:
        app = Application.builder().token(settings.telegram_token).build()
        app.bot_data["settings"] = settings
        app.bot_data["watch"] = {
            "enabled": False,
            "chats": set(),
            "seen": set(),
            "interval_minutes": settings.signal_check_minutes,
            "timeframe": settings.timeframe,
        }
        for name, handler in [
            ("start", cmd_start), ("help", cmd_help), ("symbols", cmd_symbols),
            ("backtest", cmd_backtest), ("scan", cmd_scan),
            ("signal", cmd_signal), ("watch", cmd_watch), ("portfolio", cmd_portfolio),
        ]:
            app.add_handler(CommandHandler(name, await wrapped(handler)))
        app.add_handler(CallbackQueryHandler(await wrapped(cb_trade)))
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        asyncio.create_task(watch_loop(app))
        logger.info("Bot Telegram avviato.")
        await asyncio.Event().wait()

    asyncio.run(build())


if __name__ == "__main__":
    main()
