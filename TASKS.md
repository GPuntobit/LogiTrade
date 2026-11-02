# LogiTrade — Piano di lavoro

Sotto-task generate in sequenza. Ogni task va completata, verificata (test + regressione)
e spuntata prima di passare alla successiva.

- [x] **1. Ottimizzazione parametri con walk-forward**
      Grid search su RSI/SL/TP con validazione walk-forward (train/test a finestre scorrevoli)
      per evitare overfitting. Deliverable: modulo `optimizer.py`, CLI `optimize_cli`,
      test automatici, risultati out-of-sample su tutti i simboli.

      **Risultati (1h, dal 2024-01-01, train 180g / test 90g):** l'ottimizzazione
      NON batte i parametri default fuori campione (tranne WIF, il peggiore):

      | symbol | OOS ottimizzato | OOS default | migliora? |
      |---|---|---|---|
      | PEPE/USDT | -3.75% | +3.01% | no |
      | DOGE/USDT | -1.30% | -0.48% | no |
      | SHIB/USDT | -2.13% | +0.04% | no |
      | WIF/USDT | -2.79% | -5.18% | si |
      | BONK/USDT | -1.28% | +0.24% | no |
      | FLOKI/USDT | -0.25% | +2.47% | no |

      Conclusione: i default sono gia' vicini all'ottimo e il grid search overfitta;
      il walk-forward lo dimostra. Uso: `python -m logitrade.optimize_cli [SYMBOL] [tf] [since]`.
- [x] **2. Filtro di trend per la strategia**
      Filtro SMA sulle entry (long solo in trend rialzista) per ridurre i trade in downtrend.
      Deliverable: parametro `trend_sma_period` in strategia/config, test, confronto
      backtest con/senza filtro.

      **Nota di design:** il filtro `prezzo > SMA` azzera TUTTI i trade (l'ingresso da
      ipervenduto avviene tipicamente sotto la media). Il filtro implementato usa la
      **pendenza** della SMA: `SMA(200) > SMA(200) di 50 bar fa`. Confronto 1h dal 2024-01-01:

      | symbol | senza filtro | con filtro (SMA200 +50bar) |
      |---|---|---|
      | PEPE/USDT | +2.30% / 5t / 60%w | +1.81% / 2t / 50%w |
      | DOGE/USDT | -1.25% / 7t / 29%w | +1.81% / 3t / 67%w |
      | SHIB/USDT | +0.04% / 4t / 50%w | +1.71% / 1t / 100%w |
      | WIF/USDT | -4.64% / 9t / 22%w | -2.08% / 5t / 40%w |
      | BONK/USDT | +0.19% / 4t / 25%w | -0.37% / 1t / 0%w |
      | FLOKI/USDT | +2.25% / 10t / 40%w | +0.01% / 4t / 25%w |
      | **SOMMA** | **-1.12%** | **+2.89%** |

      Il filtro e' attivo di default (`trend_sma_period: 200`, `trend_slope_bars: 50`),
      disattivabile con `trend_sma_period: 0`.
- [x] **3. Multi-timeframe: confronto 1h / 4h / 1d**
      Estensione della CLI per confrontare lo scan su più timeframe e analisi comparativa
      dei risultati. Deliverable: modalità `--compare` in `backtest_cli`, tabella riassuntiva.

      **Risultati (dal 2024-01-01, filtro trend attivo):** su 4h e 1d la strategia
      non produce praticamente alcun trade. Diagnostica (conteggio segnali senza filtro):
      la condizione di ingresso RSI<30 + incrocio MACD rialzista scatta 0-1 volte in
      ~3 anni su 4h/1d, contro 4-10 trade su 1h. **La strategia è efficace solo su 1h**:
      su timeframe superiori gli ingressi sono troppo rari per qualsiasi parametro.

      | symbol | 1h | 4h | 1d |
      |---|---|---|---|
      | PEPE/USDT | +1.81% / 2t | 0t | 0t |
      | DOGE/USDT | +1.81% / 3t | 0t | 0t |
      | SHIB/USDT | +1.71% / 1t | 0t | 0t |
      | WIF/USDT | -2.08% / 5t | 0t | 0t |
      | BONK/USDT | -0.37% / 1t | 0t | 0t |
      | FLOKI/USDT | +0.01% / 4t | 0t | 0t |

      Uso: `python -m logitrade.backtest_cli --compare 1h 4h 1d`.

## Prossime sotto-task

- [x] **4. Nuova condizione di ingresso per 4h/1d**
      - [x] 4.1 Analisi quantitativa: la causa della rarità è la SIMULTANEITÀ di
            RSI<30 e incrocio MACD (0-1 eventi/anno su 4h/1d). Disaccoppiandoli
            (dip entro K barre + incrocio ora) gli eventi salgono a 27-41 su 4h
      - [x] 4.2 Implementata modalità `entry_mode: dip_turn` (configurabile,
            `entry_lookback: 5`); default resta `classic`
      - [x] 4.3 Test automatici (8 nuovi: oracle, lookback=1 ≡ classic, dati
            sintetici dedicati `make_dip_turn_closes`)
      - [x] 4.4 Valutazione: `dip_turn` sblocca i segnali su 4h (5-15 trade) ma con
            **aspettativa negativa** (somma -18.6%, win rate 17-31%). Walk-forward
            OOS 4h: -8.31% somma, negativo su 5/6 simboli. Su 1d resta ~zero.
      - [x] 4.5 Conclusione: la strategia resta 1h-only. Le varianti mean-reversion
            non funzionano sui TF alti: servirebbe una famiglia diversa
            (trend-following/breakout). `dip_turn` resta disponibile come opzione.
- [x] **5. Trailing stop**
      - [x] 5.1 Motore: `trailing_stop_pct` (0 = off), trail sul massimo close
            dall'ingresso (aggiornato DOPO i controlli), priorità SL > TP >
            trailing > segnale
      - [x] 5.2 Test motore (11: trigger, priorità, peak, contabilità, default off)
      - [x] 5.3 Wiring config/CLI. Confronto 1h: **effetto quasi nullo** — 1 uscita
            trailing su 35 trade (WIF trail 5%: -2.28% vs -2.08%). I trade sono
            troppo brevi (SL/TP/segnale escono prima di un ribasso dal massimo).
            Raccomandazione: lasciare `trailing_stop_pct: 0` finché SL/TP restano
            stretti (5%/15%); utile solo con SL/TP allargati.
      - [x] 5.4 Docs aggiornate
- [x] **6. Test montecarlo del motore**
      - [x] 6.1 Generatore `random_walk_closes(n, seed, drift, vol)` deterministico
      - [x] 6.2 Invarianti su 40 seed × 2 config (trailing on/off) + drift ±:
            contabilità esatta (final = initial + Σ cash flow netti), trade sempre
            chiusi, nessuna sovrapposizione posizioni, motivi validi, equity > 0,
            drawdown ≤ 0
      - [x] 6.3 Scenari analitici: mercato piatto → perdita esatta = costi
            (final = initial · f^10 con f formula chiusa), bound perdita SL,
            bound guadagno TP, equity piatta senza segnali, SL ripetuti
      - [x] 6.4 Docs aggiornate. Suite totale: 120 test

- [x] **7. Strategia breakout/trend-following per 4h/1d**
      - [x] 7.1 Analisi: le rotture dei massimi N-barre hanno forward return positivi
            quasi ovunque (1d N=40: +25-48% a 20 barre su PEPE/SHIB/FLOKI),
            profilo trend-following: hit rate ~50% ma vincitori grandi
      - [x] 7.2 Implementata strategia breakout (Donchian, `logitrade/breakout.py`)
            + dispatcher in runner (`strategy.family: rsi_macd | breakout`) + config
      - [x] 7.3 Test (14 nuovi: oracle regola, bande su barre precedenti, dispatcher,
            optimizer generico per entrambe le famiglie). Suite: 146 test
      - [x] 7.4 Valutazione:

            | famiglia / TF | somma 4h | somma 1d |
            |---|---|---|
            | RSI/MACD (task 4) | -8.3% OOS | ~0 |
            | breakout 20/10 nofilter (full period) | +114.2% | +226.8% |
            | breakout 20/10 con filtro trend | +76.1% | +21.3% |
            | **breakout walk-forward OOS (default)** | **-5.4%** | **+117.3%** |
            | breakout walk-forward OOS (ottimizzato) | +6.8% | +85.5% |

            **Conclusione: breakout su 1d ha edge reale e walk-forward-robusto**
            (5/6 simboli OOS positivi con parametri NON toccati: PEPE +11.6%,
            DOGE +30.8%, WIF +27.3%, BONK +15.6%, FLOKI +9.7%). Su 4h è debole.
            Il filtro trend danneggia il breakout (i breakout SONO l'inizio del
            trend) e i parametri default battono quelli ottimizzati.
      - [x] 7.5 Segnali live: `check_signal` gestisce entrambe le famiglie
            (stato dalle bande di Donchian, riga `Bande: upper/lower`, /watch ok)
      - [x] 7.6 Raccomandazione: configurazione migliore trovata in tutto il progetto
            → `strategy.family: breakout`, `backtest.timeframe: 1d`,
            `trend_sma_period: 0`. **ATTIVATA come default** (config.yaml).

## Note di esecuzione

<!-- Aggiornare man mano che ogni task viene completata -->
