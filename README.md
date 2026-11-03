# LogiTrade 🐕

Bot di **backtesting per meme coin** con strategia **RSI + MACD**, interfaccia **Telegram** e CLI.

## Strategia

Solo long (spot), parametri configurabili in `config.yaml`. **Famiglia attiva:
`breakout`** (Donchian, trend-following) su timeframe 1d. Le regole della famiglia
`rsi_macd` (di default in passato) restano disponibili e documentate sotto.

| Evento | Regola |
|---|---|
| Ingresso | RSI < 30 (ipervenduto) **E** MACD incrocia al rialzo la signal line **E** (filtro trend) SMA(200) in pendenza positiva |
| Uscita | RSI > 70 (ipercomprato) **oppure** MACD incrocia al ribasso |
| Stop loss | -5% dall'ingresso |
| Take profit | +15% dall'ingresso |

Il backtest considera commissioni (0.1% per lato) e slippage (0.05% per lato).

**Filtro di trend** (attivo di default): gli ingressi sono permessi solo se la SMA(200)
è in pendenza positiva sulle ultime 50 barre. Usa la pendenza e non `prezzo > SMA`
perché il rimbalzo da ipervenduto avviene tipicamente sotto la media.

**Modalità di ingresso** (`entry_mode`): `classic` (default, RSI<30 e incrocio MACD
sulla stessa barra) oppure `dip_turn` (RSI<30 entro le ultime `entry_lookback` barre
+ incrocio MACD ora). `dip_turn` genera più segnali su 4h/1d ma senza edge reale
(motivo per cui il default resta `classic`).

**Trailing stop** (`risk.trailing_stop_pct`, 0 = off): con SL/TP stretti (5%/15%)
è praticamente ininfluente — vedi `TASKS.md` per l'analisi completa.

**Famiglia di strategia** (`strategy.family`): `rsi_macd` oppure `breakout`
(Donchian, trend-following: ingresso su rottura del massimo delle N barre precedenti,
uscita su rottura del minimo delle M barre). **Configurazione attiva:**

```yaml
strategy:
  family: breakout
  trend_sma_period: 0     # il filtro trend danneggia il breakout
backtest:
  timeframe: 1d
```

Walk-forward OOS su 1d: **+117% composto** (parametri default 20/10, 5/6 simboli
positivi) contro ~0 della strategia RSI/MACD sugli stessi timeframe. Dettagli in
`TASKS.md` (task 7).

## Setup

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # opzionale, per i test
cp .env.example .env   # poi inserisci il token Telegram di @BotFather
```

## Test

```bash
python -m pytest
```

Suite su indicatori (RSI/MACD), regole della strategia, motore di backtest
(contabilità, commissioni, SL/TP), segnali live (anti-repainting della candela
in formazione) e configurazione. I dati di mercato sono sintetici: nessuna
chiamata di rete.

## Uso

**CLI (backtest):**

```bash
# scan di tutti i simboli configurati
python -m logitrade.backtest_cli

# backtest singolo: SYMBOL [timeframe] [data inizio]
python -m logitrade.backtest_cli PEPE/USDT 1h 2024-01-01

# confronto multi-timeframe
python -m logitrade.backtest_cli --compare 1h 4h 1d
```

**Ottimizzazione parametri (grid search + walk-forward):**

```bash
python -m logitrade.optimize_cli PEPE/USDT 1h 2024-01-01
python -m logitrade.optimize_cli   # riepilogo su tutti i simboli
```

**Bot Telegram:**

```bash
python -m logitrade.telegram_bot
```

Poi su Telegram:

- `/backtest PEPE/USDT 1h 2024-01-01` — backtest singolo con equity curve
- `/scan 4h` — scan di tutti i meme coin monitorati
- `/symbols` — lista simboli
- `/signal` — segnale live su tutti i simboli (candela chiusa)
- `/signal PEPE/USDT 4h` — dettaglio segnale su un simbolo, con SL/TP suggeriti
- `/watch on 1h` — alert automatici quando parte un segnale di ingresso/uscita
- `/watch off` — disattiva gli alert
- `/portfolio` — portafoglio simulato (cash, posizioni aperte, PnL)
- `/buy PEPE/USDT 500` — **acquisto simulato con importo scelto**: `/buy SYMBOL [importo]`,
  importo in USDT (`500`), in percentuale del cash (`25%`) oppure omesso
  (`position_pct` della config). Il simbolo si può scrivere anche senza quota
  (`/buy PEPE 500` → PEPE/USDT)
- `/sell PEPE/USDT 200` — **vendita simulata con importo scelto**: `/sell SYMBOL [importo]`,
  in USDT (`200`) o in percentuale della posizione (`50%`); con l'importo omesso
  chiude tutta la posizione
- `/auto on 1d` — **buy/sell automatici simulati**: ENTRY → acquisto (25% del cash),
  EXIT → vendita della posizione aperta, eseguiti al prezzo di chiusura candela
  (come il backtest). `/auto off` per disattivare

I bottoni **🟢 BUY** e **🔴 SELL** compaiono sui messaggi di `/signal` (singolo
simbolo e scan di tutti), su `/portfolio` e sugli alert di `/watch`. Il **SELL**
esegue subito la chiusura della posizione; il **BUY** prima apre la **scelta
dell'importo** (preset in USDT ≤ cash disponibile, `% del cash`, `Tutto il cash`,
`✖ Annulla`) e solo alla scelta esegue l'acquisto — per un importo libero si usa
`/buy`. Tutte le operazioni sono **simulate** (paper trading)
su un portafoglio virtuale salvato in `paper_portfolio.json`, applicando le stesse
commissioni e slippage del backtest. **Nessun ordine reale viene inviato
all'exchange** — per il trading reale servirebbe un progetto dedicato con API key
e controlli di rischio.

Con `/auto` le stesse operazioni vengono eseguite **automaticamente** in base
all'andamento della meme coin (segnali della strategia configurata), con al
massima una posizione per simbolo (no pyramiding) e un'operazione per candela.

I segnali live usano le candele più recenti dell'exchange (niente cache) e valutano
la strategia sull'ultima candela **chiusa**, così il segnale non cambia durante la
formazione. Nessun ordine viene eseguito: solo notifiche.

## Configurazione

Tutto in `config.yaml`: simboli, timeframe, parametri RSI/MACD, filtro di trend
(`trend_sma_period`/`trend_slope_bars`, 0 = off), rischio (size posizione, SL/TP),
intervallo dei controlli di `/watch` (`signal.check_interval_minutes`).

Su `.env`: `TELEGRAM_BOT_TOKEN` (da @BotFather) e `TELEGRAM_ALLOWED_CHAT_IDS` (per limitare l'accesso).

I dati storici vengono cachati in `data_cache/` (formato Parquet) per non riscaricarli.

## ⚠️ Disclaimer

Software a scopo didattico. I risultati passati non garantiscono profitti futuri. I meme coin sono estremamente volatili: **non investire capitale che non puoi permetterti di perdere**.
# LogiTrade
