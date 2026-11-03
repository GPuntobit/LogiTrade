"""Interfaccia Telegram per LogiTrade.

Comandi:
  /help                          - mostra i comandi
  /symbols                       - meme coin monitorati
  /backtest SYMBOL [tf] [data]   - es. /backtest PEPE/USDT 1h 2024-01-01
  /scan [tf]                     - backtest di tutti i simboli configurati
  /signal [SYMBOL] [tf]          - segnale live (tutti i simboli se omesso)
  /watch [on [tf] | off]         - alert automatici ogni N minuti
  /portfolio                     - portafoglio simulato (paper trading)
  /auto [on [tf] | off]          - buy/sell automatici simulati sui segnali
  /buy SYMBOL [importo]          - acquisto simulato con importo scelto
                                   (es. /buy PEPE/USDT 500 · /buy PEPE 25%)
  /sell SYMBOL [importo]         - vendita simulata (tutta se l'importo e' omesso)

Bottoni 🟢BUY / 🔴SELL: operazioni SIMULATE, nessun ordine reale.
Il BUY apre la scelta dell'importo (preset o /buy per un importo libero).
"""

import asyncio
import atexit
import html
import logging
import os
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import PROJECT_ROOT, Settings, load_settings
from .data import fetch_last_price
from .paper import Portfolio
from .report import (equity_chart, format_auto_trade, format_portfolio, format_report,
                     format_signal, format_signal_line)
from .runner import run_symbol
from .signals import check_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIMEFRAMES = {"5m", "15m", "30m", "1h", "4h", "1d"}

# Momento di avvio del processo: con /help si capisce se il bot sta girando con
# codice vecchio (non ricaricato dopo una modifica) -> va riavviato.
STARTED_AT = datetime.now().astimezone()

# --- Singola istanza -----------------------------------------------------
# Telegram rifiuta due poller sullo stesso token (HTTP 409 Conflict): il file
# bot.pid tiene traccia dell'istanza attiva e un secondo avvio fallisce subito
# con un messaggio chiaro invece di un'esplosione di errori in log.
LOCK_FILE = PROJECT_ROOT / "bot.pid"


class _ConflictHintFilter(logging.Filter):
    """Aggiunge una spiegazione in italiano al Conflict (istanza doppia) di PTB."""

    HINT = ("\n💡 Solo un'istanza può fare polling: ferma l'altra "
            "(il file bot.pid contiene il PID di quella attiva).")

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        needs_hint = ((exc is not None and "only one bot instance" in str(exc))
                      or "only one bot instance" in record.getMessage())
        if needs_hint and "bot.pid" not in record.getMessage():
            record.msg = record.getMessage() + self.HINT
            record.args = ()
        return True


logging.getLogger("telegram.ext.Updater").addFilter(_ConflictHintFilter())


def _pid_running(pid: int) -> bool:
    """True se `pid` e' un nostro bot ancora vivo (su Linux controlla /proc)."""
    if pid <= 0 or pid > 4_194_304:  # PID_MAX: numeri fuori range = lock corrotto
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()  # evita falsi pid riutilizzati
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    return b"logitrade.telegram_bot" in cmdline


def acquire_instance_lock(path: Path = LOCK_FILE) -> None:
    """Un solo'istanza del bot: esce con un errore chiaro se un'altra gira gia'."""
    if path.exists():
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = -1
        if pid != os.getpid() and _pid_running(pid):
            raise SystemExit(
                f"⚠️ Bot già in esecuzione (PID {pid}): chiudi quell'istanza prima di avviarne un'altra.\n"
                "   Telegram accetta un solo poller (altrimenti: HTTP 409 Conflict).\n"
                f"   per fermarla: kill {pid}"
            )
    path.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(release_instance_lock, path)


def release_instance_lock(path: Path = LOCK_FILE) -> None:
    """Rimuove il lock solo se e' nostro (non tocca il lock di un'altra istanza)."""
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        logger.warning("Lock %s non rimovibile", path, exc_info=True)


def _trade_keyboard(symbols: list[str], holdings: set[str] | None = None) -> InlineKeyboardMarkup:
    """Bottoni 🟢BUY/🔴SELL: una riga per simbolo.

    Con `holdings` (simboli con posizione aperta) il SELL è mostrato solo per chi
    ha qualcosa da vendere; senza `holdings` il SELL compare sempre.
    """
    multi = len(symbols) > 1
    rows = []
    for symbol in symbols:
        suffix = f" {symbol}" if multi else ""
        row = [InlineKeyboardButton(f"🟢 BUY{suffix}", callback_data=f"buy:{symbol}")]
        if holdings is None or symbol in holdings:
            row.append(InlineKeyboardButton(f"🔴 SELL{suffix}", callback_data=f"sell:{symbol}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


# Importi predefiniti proposti dal bottone 🟢BUY (USDT)
_AMOUNT_PRESETS = (100, 250, 500, 1000)


def normalize_symbol(token: str, default_quote: str = "USDT") -> str:
    """Normalizza l'argomento simbolo dei comandi: 'pepe' -> 'PEPE/USDT'."""
    sym = token.strip().upper().replace("-", "/")
    if "/" not in sym:
        sym = f"{sym}/{default_quote}"
    base, _, quote = sym.partition("/")
    # alnum anche per evitare iniezioni HTML nei messaggi di conferma
    if not base.isalnum() or not quote.isalnum():
        raise ValueError(f"Simbolo non valido: {token} (uso: BASE/USDT, es. PEPE/USDT)")
    return sym


def parse_amount(token: str, cash: float, pct_base: float | None = None) -> float:
    """Importo USDT da un argomento comando.

    '500' -> 500 USDT;  '25%' -> 25% di `pct_base` (default: il cash disponibile).
    """
    raw = token.strip().replace(",", ".")
    base = cash if pct_base is None else pct_base
    try:
        if raw.endswith("%"):
            usdt = base * float(raw[:-1]) / 100
        else:
            usdt = float(raw)
    except ValueError:
        raise ValueError(f"Importo non valido: {token} (uso: 500 oppure 25%)") from None
    if usdt <= 0:
        raise ValueError("Importo deve essere positivo")
    return usdt


def _amount_keyboard(symbol: str, cash: float, position_pct: float) -> InlineKeyboardMarkup:
    """Bottoni di scelta importo per 🟢BUY (solo valori <= cash disponibile)."""
    rows: list[list[InlineKeyboardButton]] = []
    presets = [v for v in _AMOUNT_PRESETS if v <= cash]
    for i in range(0, len(presets), 3):
        rows.append([InlineKeyboardButton(f"{v:g} USDT", callback_data=f"buya:{symbol}:{v:g}")
                     for v in presets[i:i + 3]])
    pct_row = []
    if cash * position_pct / 100 > 0:
        pct_row.append(InlineKeyboardButton(f"{position_pct:g}% del cash",
                                            callback_data=f"buyp:{symbol}"))
    pct_row.append(InlineKeyboardButton("💰 Tutto il cash", callback_data=f"buya:{symbol}:all"))
    rows.append(pct_row)
    rows.append([InlineKeyboardButton("✖ Annulla", callback_data="bcancel")])
    return InlineKeyboardMarkup(rows)


def _fmt_buy(symbol: str, requested: float, invested: float, pos, cash: float) -> str:
    lines = [
        f"🟢 <b>Acquisto SIMULATO</b> {symbol}",
        f"Quantità: {pos.qty:.6g} @ {pos.entry_price:.8g}",
        f"Investito: {invested:,.2f} USDT | Cash rimanente: {cash:,.2f} USDT",
    ]
    if invested < requested:
        lines.append(f"⚠️ Importo richiesto {requested:,.2f} ridotto al cash disponibile.")
    return "\n".join(lines)


def _fmt_sell(symbol: str, trade: dict, cash: float, pf: Portfolio) -> str:
    lines = [
        f"🔴 <b>Vendita SIMULATA</b> {symbol}",
        f"Quantità: {trade['qty']:.6g} @ {trade['price']:.8g}",
        f"Incassato: {trade['usdt']:,.2f} USDT | PnL: {trade['pnl']:+,.2f} USDT",
        f"Cash: {cash:,.2f} USDT",
    ]
    pos = pf.positions.get(symbol)
    if pos:
        lines.append(f"Posizione residua: {pos.qty:.6g} @ {pos.entry_price:.8g}")
    return "\n".join(lines)


async def _answer(query, text: str | None = None, alert: bool = False) -> None:
    """Acknowledge del callback senza mai far fallire il flusso.

    Una doppia risposta (o una query scaduta) non deve bloccare l'operazione:
    l'errore viene loggato e si continua.
    """
    try:
        await query.answer(text, show_alert=alert)
    except Exception:  # noqa: BLE001 - query gia' risposta/scaduta
        logger.warning("query.answer fallito", exc_info=True)


async def _deliver(query, text: str, reply_markup=None, prefer_edit: bool = True) -> None:
    """Conferma SEMPRE visibile: edita il messaggio del bottone, in fallback un nuovo messaggio.

    Se non si puo' inviare nulla l'eccezione si propaga: il global handler
    (`on_error`) notifica nella chat invece di restare silenzioso.
    """
    if prefer_edit and query.message is not None:
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            return
        except Exception as e:  # noqa: BLE001
            if "not modified" in str(e).lower():
                return  # la conferma e' gia' sul messaggio (doppio tap)
            logger.warning("edit_message_text fallito: provo come nuovo messaggio", exc_info=True)
    if query.message is None:
        raise RuntimeError("Nessun messaggio a cui inviare la conferma")
    await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


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
    settings: Settings = ctx.bot_data["settings"]
    await update.message.reply_text(
        f"🤖 LogiTrade — meme coin: backtest, segnali e paper trading\n"
        f"Strategia attiva: <b>{settings.strategy_family}</b> · timeframe {settings.timeframe}\n\n"
        "/symbols - lista simboli monitorati\n"
        "/backtest SYMBOL [tf] [data] - backtest singolo\n"
        "   es: /backtest PEPE/USDT 1h 2024-01-01\n"
        "/scan [tf] - backtest di tutti i simboli\n"
        "/signal [SYMBOL] [tf] - segnale live sulla candela chiusa\n"
        "/watch [on [tf] | off] - alert automatici quando parte un segnale\n"
        "/portfolio - portafoglio simulato (solo lettura)\n"
        "/auto [on [tf] | off] - buy/sell automatici simulati sui segnali\n"
        "/buy SYMBOL [importo] - acquisto simulato con importo scelto\n"
        "   es: /buy PEPE/USDT 500 | /buy PEPE 25% | /buy PEPE (position_pct)\n"
        "/sell SYMBOL [importo] - vendita simulata (tutta se l'importo e' omesso)\n"
        "   es: /sell PEPE/USDT | /sell PEPE/USDT 200\n\n"
        "🟢BUY / 🔴SELL: operazioni simulate (paper trading)\n"
        "Il BUY ti chiede quanto investire (preset o /buy per un importo libero)\n\n"
        f"🕓 Bot avviato: {STARTED_AT:%d/%m %H:%M} — se questa data è più vecchia "
        "delle modifiche al codice, il bot gira con codice non ricaricato: riavvialo",
        parse_mode=ParseMode.HTML,
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
        # in thread: il loop resta libero e gli altri comandi rispondono subito
        out = await asyncio.to_thread(run_symbol, symbol, settings, timeframe, since)
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
            out = await asyncio.to_thread(run_symbol, symbol, settings, timeframe)
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
    """Portafoglio in SOLO LETTURA: niente bottoni BUY/SELL, prezzi in parallelo."""
    settings: Settings = ctx.bot_data["settings"]
    pf = Portfolio.load(initial_cash=settings.initial_cash)
    status = await update.message.reply_text("⏳ Valutazione portafoglio...")
    # fetch prezzi in thread e in parallelo: niente blocchi del loop, risposta rapida
    held = list(pf.positions)
    results = await asyncio.gather(
        *(asyncio.to_thread(fetch_last_price, s, settings.exchange_id) for s in held),
        return_exceptions=True)
    prices: dict[str, float] = {}
    for symbol, res in zip(held, results):
        if isinstance(res, BaseException):
            logger.warning("Prezzo non disponibile per %s: %r", symbol, res)
        else:
            prices[symbol] = res
    await status.delete()
    # solo lettura: per operare ci sono i bottoni sui segnali e i comandi /buy e /sell
    await update.message.reply_text(format_portfolio(pf, prices), parse_mode=ParseMode.HTML)


async def cb_trade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Bottoni 🟢BUY / 🔴SELL: operazioni SIMULATE sul portafoglio paper.

    Il BUY non esegue subito: apre la scelta dell'importo (preset, % del cash o
    tutto). L'esecuzione avviene su `buya:` (importo fisso/tutto) o `buyp:`
    (position_pct della config). `bcancel` annulla.
    """
    query = update.callback_query
    settings: Settings = ctx.bot_data["settings"]
    action, _, rest = query.data.partition(":")

    if action == "buy":  # scelta importo
        symbol = rest
        try:
            pf = Portfolio.load(initial_cash=settings.initial_cash)
            text = (
                f"🟢 <b>Acquisto SIMULATO</b> {symbol}\n"
                f"Scegli quanto investire — cash disponibile: {pf.cash:,.2f} USDT\n\n"
                f"Importo libero: <code>/buy {symbol} 350</code>"
            )
            markup = _amount_keyboard(symbol, pf.cash, settings.position_pct)
            # lista multi-simbolo (/portfolio, /signal senza argomenti): la scelta
            # arriva come nuovo messaggio, così la lista resta usabile
            multi = bool(query.message and query.message.reply_markup
                         and len(query.message.reply_markup.inline_keyboard) > 1)
            await _answer(query)
            await _deliver(query, text, markup, prefer_edit=not multi)
        except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
            logger.exception("Scelta importo fallita")
            await _answer(query, f"Errore: {e}", alert=True)
        return

    if action == "bcancel":
        await _answer(query)
        await _deliver(query, "✖ Operazione annullata.")
        return

    if action in ("buya", "buyp"):  # esecuzione con importo scelto
        symbol, _, spec = rest.partition(":")
        try:
            price = await asyncio.to_thread(fetch_last_price, symbol, settings.exchange_id)
            pf = Portfolio.load(initial_cash=settings.initial_cash)
            if action == "buyp":
                requested = pf.cash * settings.position_pct / 100
            elif spec == "all":
                requested = pf.cash
            else:
                requested = float(spec)
            invested = min(requested, pf.cash)
            if invested <= 0:
                raise ValueError("Cash insufficiente")
            pos = pf.buy(symbol, price, invested, settings.commission_pct, settings.slippage_pct)
            pf.save()
        except ValueError as e:
            await _answer(query, str(e), alert=True)
            return
        except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
            logger.exception("Operazione simulata fallita")
            await _answer(query, f"Errore: {e}", alert=True)
            return
        await _answer(query)
        # la conferma edita il messaggio della scelta importo; se non si puo',
        # arriva come nuovo messaggio (mai silenzio dopo un acquisto riuscito)
        await _deliver(query, _fmt_buy(symbol, requested, invested, pos, pf.cash))
        return

    # vendita (e casi sconosciuti, come prima)
    symbol = rest
    try:
        price = await asyncio.to_thread(fetch_last_price, symbol, settings.exchange_id)
        pf = Portfolio.load(initial_cash=settings.initial_cash)
        if action == "sell":
            trade = pf.sell(symbol, price, settings.commission_pct, settings.slippage_pct)
            pf.save()
            msg = _fmt_sell(symbol, trade, pf.cash, pf)
        else:
            msg = f"Azione sconosciuta: {action}"
    except ValueError as e:
        await _answer(query, str(e), alert=True)
        return
    except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
        logger.exception("Operazione simulata fallita")
        await _answer(query, f"Errore: {e}", alert=True)
        return
    await _answer(query)
    markup = query.message.reply_markup if query.message else None
    # lista multi-simbolo (/portfolio, /signal senza argomenti): la conferma
    # arriva come nuovo messaggio, così la lista e i suoi bottoni restano usabili
    await _deliver(query, msg, prefer_edit=not (markup and len(markup.inline_keyboard) > 1))


async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Acquisto simulato con simbolo e importo scelti: /buy SYMBOL [importo]."""
    settings: Settings = ctx.bot_data["settings"]
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Uso: /buy SYMBOL [importo]\n"
            "   es: /buy PEPE/USDT 500  (500 USDT)\n"
            "       /buy PEPE 25%        (25% del cash)\n"
            "       /buy PEPE            (position_pct del cash)")
        return

    try:
        symbol = normalize_symbol(args[0])
        price = await asyncio.to_thread(fetch_last_price, symbol, settings.exchange_id)
        pf = Portfolio.load(initial_cash=settings.initial_cash)
        if pf.cash <= 0:
            raise ValueError("Cash insufficiente")
        if len(args) > 1:
            requested = parse_amount(args[1], pf.cash)
        else:
            requested = pf.cash * settings.position_pct / 100
        invested = min(requested, pf.cash)
        if invested <= 0:
            raise ValueError("Cash insufficiente")
        pos = pf.buy(symbol, price, invested, settings.commission_pct, settings.slippage_pct)
        pf.save()
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
        logger.exception("Acquisto simulato fallito")
        await update.message.reply_text(f"❌ Errore: {html.escape(str(e))}")
        return

    msg = _fmt_buy(symbol, requested, invested, pos, pf.cash)
    if symbol not in settings.symbols:
        msg += f"\nℹ️ {symbol} non è tra i simboli monitorati: nessun segnale per esso."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Vendita simulata: /sell SYMBOL [importo USDT] (tutta se l'importo e' omesso)."""
    settings: Settings = ctx.bot_data["settings"]
    args = ctx.args or []
    if not args:
        pf = Portfolio.load(initial_cash=settings.initial_cash)
        held = ", ".join(pf.positions) or "nessuna"
        await update.message.reply_text(
            "Uso: /sell SYMBOL [importo]\n"
            "   es: /sell PEPE/USDT      (tutta la posizione)\n"
            "       /sell PEPE/USDT 200  (vende fino a 200 USDT)\n"
            "       /sell PEPE/USDT 50%  (metà della posizione)\n\n"
            f"Posizioni aperte: {held}")
        return

    try:
        symbol = normalize_symbol(args[0])
        pf = Portfolio.load(initial_cash=settings.initial_cash)
        pos = pf.positions.get(symbol)
        if pos is None:
            raise ValueError(f"Nessuna posizione aperta per {symbol}")
        price = await asyncio.to_thread(fetch_last_price, symbol, settings.exchange_id)
        # '%' sulla posizione: valore netto attuale (dopo slippage e commissioni)
        pct_base = pos.qty * price * (1 - settings.slippage_pct / 100) \
            * (1 - settings.commission_pct / 100)
        usdt_amount = parse_amount(args[1], pf.cash, pct_base) if len(args) > 1 else None
        trade = pf.sell(symbol, price, settings.commission_pct, settings.slippage_pct,
                        usdt_amount=usdt_amount)
        pf.save()
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
        logger.exception("Vendita simulata fallita")
        await update.message.reply_text(f"❌ Errore: {html.escape(str(e))}")
        return

    await update.message.reply_text(_fmt_sell(symbol, trade, pf.cash, pf), parse_mode=ParseMode.HTML)


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Buy/sell AUTOMATICI simulati: operazioni in base all'andamento (segnali)."""
    settings: Settings = ctx.bot_data["settings"]
    watch = ctx.bot_data["watch"]
    args = ctx.args or []
    chat_id = update.effective_chat.id

    if not args:
        state = "🟢 attivo" if chat_id in watch["auto_chats"] else "⚪ spento"
        await update.message.reply_text(
            f"🤖 Auto-trade: {state} (paper trading) | tf {watch['timeframe']} | "
            f"intervallo {watch['interval_minutes']} min\n\n"
            "Comandi: /auto on [tf] · /auto off\n"
            "Con l'auto attivo: segnale ENTRY → acquisto simulato (position_pct del cash),\n"
            "segnale EXIT → vendita simulata della posizione aperta.\n"
            "Esecuzione al prezzo di chiusura candela (come il backtest)."
        )
        return

    action = args[0].lower()
    if action == "on":
        tf = args[1] if len(args) > 1 else settings.timeframe
        if tf not in TIMEFRAMES:
            await update.message.reply_text(f"Timeframe non valido: {tf}")
            return
        watch["timeframe"] = tf
        watch["chats"].add(chat_id)        # l'auto implica il watch (per i messaggi)
        watch["auto_chats"].add(chat_id)
        watch["enabled"] = True
        await update.message.reply_text(
            f"🤖 Auto-trade attivo ogni {watch['interval_minutes']} min su {tf} (simulato).\n"
            "Riceverai conferma di ogni operazione eseguita."
        )
    elif action == "off":
        watch["auto_chats"].discard(chat_id)
        await update.message.reply_text("🤖 Auto-trade disattivato per questa chat.")
    else:
        await update.message.reply_text("Uso: /auto [on [tf] | off]")


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
            snap = await asyncio.to_thread(check_signal, symbol, settings, tf)
            await status.delete()
            await update.message.reply_text(format_signal(snap, settings), parse_mode=ParseMode.HTML,
                                            reply_markup=_trade_keyboard([symbol]))
        except Exception as e:  # noqa: BLE001 - errore da riportare all'utente
            logger.exception("Controllo segnale fallito")
            await status.edit_text(f"❌ Errore: {html.escape(str(e))}")
        return

    # scan compatto di tutti i simboli
    tf = settings.timeframe
    status = await update.message.reply_text(f"⏳ Controllo segnali ({tf})...")
    lines = [f"📡 <b>Segnali live</b> ({tf}) — candela chiusa", ""]
    # tutti i controlli in parallelo (thread): scan rapido e loop non bloccato
    results = await asyncio.gather(
        *(asyncio.to_thread(check_signal, s, settings, tf) for s in settings.symbols),
        return_exceptions=True)
    for symbol, res in zip(settings.symbols, results):
        if isinstance(res, BaseException):
            lines.append(f"{symbol}: ❌ {html.escape(str(res))}")
        else:
            lines.append(format_signal_line(res))
    await status.delete()
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                    reply_markup=_trade_keyboard(settings.symbols))


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
                snap = await asyncio.to_thread(check_signal, symbol, settings, watch["timeframe"])
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
            if watch["auto_chats"]:
                # paper trade automatico in base all'andamento (segnale)
                try:
                    pf = Portfolio.load(initial_cash=settings.initial_cash)
                    result = pf.auto_trade(symbol, snap.status, snap.price,
                                           settings.position_pct,
                                           settings.commission_pct, settings.slippage_pct)
                    if result:
                        pf.save()
                        text += "\n\n" + format_auto_trade(result)
                except Exception as e:  # noqa: BLE001
                    logger.exception("Auto: operazione fallita per %s", symbol)
                    text += f"\n\n⚠️ Operazione automatica non eseguita: {html.escape(str(e))}"
            for cid in sorted(watch["chats"]):
                try:
                    await app.bot.send_message(cid, text, parse_mode=ParseMode.HTML,
                                               reply_markup=_trade_keyboard(symbol))
                except Exception:  # noqa: BLE001
                    logger.exception("Watch: invio fallito verso %s", cid)


async def on_error(update: Update | None, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: nessun errore deve restare silenzioso per l'utente.

    Logga il traceback e notifica nella chat quando e' possibile (comandi e
    bottoni), così un'eccezione non si traduce in "non risponde più".
    """
    err = context.error
    logger.error("Errore non gestito nel bot: %r", err,
                 exc_info=(type(err), err, err.__traceback__) if err is not None else None)
    try:
        message = update.effective_message if update else None
        if message is not None:
            await message.reply_text(f"❌ Errore interno: {html.escape(str(err))}")
    except Exception:  # noqa: BLE001 - niente da fare se la chat non risponde
        logger.exception("Notifica di errore non inviata")


def _run_until_interrupted(coro_factory) -> None:
    """Esegue il loop principale: Ctrl-C termina con un log pulito, senza traceback."""
    try:
        asyncio.run(coro_factory())
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrotto (Ctrl-C): arresto pulito.")


def main() -> None:
    settings = load_settings()
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante: copia .env.example in .env e inserisci il token di @BotFather")

    guard = _authorized(settings)
    acquire_instance_lock()

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
            "auto_chats": set(),
            "seen": set(),
            "interval_minutes": settings.signal_check_minutes,
            "timeframe": settings.timeframe,
        }
        for name, handler in [
            ("start", cmd_start), ("help", cmd_help), ("symbols", cmd_symbols),
            ("backtest", cmd_backtest), ("scan", cmd_scan),
            ("signal", cmd_signal), ("watch", cmd_watch), ("portfolio", cmd_portfolio),
            ("auto", cmd_auto), ("buy", cmd_buy), ("sell", cmd_sell),
        ]:
            app.add_handler(CommandHandler(name, await wrapped(handler)))
        app.add_handler(CallbackQueryHandler(await wrapped(cb_trade)))
        app.add_error_handler(on_error)
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        asyncio.create_task(watch_loop(app))
        logger.info("Bot Telegram avviato.")
        await asyncio.Event().wait()

    try:
        _run_until_interrupted(build)
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
