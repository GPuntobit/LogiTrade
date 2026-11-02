"""Ottimizzazione parametri con validazione walk-forward.

Grid search sui parametri della strategia (RSI) e del rischio (SL/TP), con
validazione fuori campione a finestre scorrevoli: i parametri vengono scelti
sul periodo di train e valutati sul periodo di test successivo, cosi' il
risultato out-of-sample non soffre di overfitting sullo storico completo.

Le entry/exit dipendono solo dai parametri di strategia: i segnali vengono
generati una volta per combo di strategia e riutilizzati per tutte le combo
di rischio (che toccano solo il motore di backtest).
"""

import dataclasses
from dataclasses import dataclass, replace
from itertools import product
from typing import Callable

import pandas as pd

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .strategy import StrategyParams, generate_signals

DEFAULT_GRID = {
    "rsi_oversold": [20.0, 25.0, 30.0, 35.0],
    "rsi_overbought": [65.0, 70.0, 75.0],
    "stop_loss_pct": [3.0, 5.0, 8.0],
    "take_profit_pct": [10.0, 15.0, 20.0],
}

_STRATEGY_FIELDS = {f.name for f in dataclasses.fields(StrategyParams)}
_RISK_FIELDS = {f.name for f in dataclasses.fields(BacktestConfig)}


@dataclass
class GridRow:
    strategy: StrategyParams
    risk: BacktestConfig
    score: float
    result: BacktestResult


@dataclass
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp      # esclusa
    test_start: pd.Timestamp
    test_end: pd.Timestamp       # esclusa
    best: GridRow                # scelto sul train
    test_result: BacktestResult  # valutazione out-of-sample


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    oos_return_pct: float        # rendimento composto fuori campione


def _combos(grid: dict) -> list[dict]:
    keys = sorted(grid)
    if not keys:
        return [{}]
    return [dict(zip(keys, values)) for values in product(*(grid[k] for k in keys))]


def grid_search(df: pd.DataFrame, base_strategy: StrategyParams, base_risk: BacktestConfig,
                grid: dict | None = None, min_trades: int = 1,
                signals_fn: Callable | None = None) -> list[GridRow]:
    """Cerca i parametri migliori su df. Ritorna righe ordinate per score decrescente.

    Score = rendimento netto; le combo senza trade (o con meno di min_trades)
    finiscono in fondo con score -inf. Le chiavi di `grid` non riconosciute
    vengono ignorate. `grid=None` usa DEFAULT_GRID; `grid={}` prova solo i
    parametri base. `base_strategy` puo' essere qualsiasi dataclass di parametri
    (es. StrategyParams o BreakoutParams) e `signals_fn` la rispettiva funzione
    di generazione segnali.
    """
    grid = DEFAULT_GRID if grid is None else grid
    signals_fn = signals_fn or generate_signals
    strategy_fields = {f.name for f in dataclasses.fields(base_strategy)}
    strat_grid = {k: v for k, v in grid.items() if k in strategy_fields}
    risk_grid = {k: v for k, v in grid.items() if k in _RISK_FIELDS}

    rows: list[GridRow] = []
    for sc in _combos(strat_grid):
        sp = replace(base_strategy, **sc)
        signals = signals_fn(df, sp)
        for rc in _combos(risk_grid):
            cfg = replace(base_risk, **rc)
            res = run_backtest(signals, cfg)
            score = res.total_return_pct if res.n_trades >= min_trades else float("-inf")
            rows.append(GridRow(strategy=sp, risk=cfg, score=score, result=res))

    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


def walk_forward(df: pd.DataFrame, base_strategy: StrategyParams, base_risk: BacktestConfig,
                 grid: dict | None = None, train_days: int = 180, test_days: int = 90,
                 min_trades: int = 1, signals_fn: Callable | None = None) -> WalkForwardResult:
    """Validazione walk-forward a finestre scorrevoli.

    Finestre di test di `test_days` giorni; per ognuna i parametri vengono scelti
    sui `train_days` giorni immediatamente precedenti. Il rendimento out-of-sample
    e' il prodotto composto dei rendimenti delle finestre di test.
    """
    grid = DEFAULT_GRID if grid is None else grid
    signals_fn = signals_fn or generate_signals
    idx = df.index
    start, end = idx[0], idx[-1]

    windows: list[WalkForwardWindow] = []
    oos_factor = 1.0
    test_start = start + pd.Timedelta(days=train_days)

    while test_start < end:
        test_end = min(test_start + pd.Timedelta(days=test_days), end)
        train_start = test_start - pd.Timedelta(days=train_days)
        train_df = df[(idx >= train_start) & (idx < test_start)]
        test_df = df[(idx >= test_start) & (idx < test_end)]
        if len(train_df) < 2 or len(test_df) < 2:
            break

        best = grid_search(train_df, base_strategy, base_risk, grid, min_trades, signals_fn)[0]
        test_signals = signals_fn(test_df, best.strategy)
        test_result = run_backtest(test_signals, best.risk)

        windows.append(WalkForwardWindow(
            train_start=train_start, train_end=test_start,
            test_start=test_start, test_end=test_end,
            best=best, test_result=test_result,
        ))
        oos_factor *= 1 + test_result.total_return_pct / 100
        test_start = test_end

    return WalkForwardResult(windows=windows, oos_return_pct=(oos_factor - 1) * 100)
