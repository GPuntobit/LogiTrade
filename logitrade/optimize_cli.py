"""CLI di ottimizzazione: grid search + validazione walk-forward.

Uso:
  python -m logitrade.optimize_cli SYMBOL [tf] [since]   - ottimizza un simbolo
  python -m logitrade.optimize_cli                        - tutti i simboli (riepilogo)

La famiglia di strategia usata e' quella di config.yaml (strategy.family).
"""

import dataclasses
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .backtest import BacktestConfig, run_backtest
from .breakout import generate_signals as breakout_signals
from .config import Settings, load_settings
from .data import fetch_ohlcv
from .optimizer import DEFAULT_GRID, WalkForwardResult, grid_search, walk_forward
from .runner import build_strategy_params
from .strategy import generate_signals as rsi_macd_signals


def _signals_fn(settings: Settings):
    if settings.strategy_family == "rsi_macd":
        return rsi_macd_signals
    if settings.strategy_family == "breakout":
        return breakout_signals
    raise ValueError(f"strategy.family sconosciuto: {settings.strategy_family!r}")


def _base_risk(settings: Settings) -> BacktestConfig:
    return BacktestConfig(
        initial_cash=settings.initial_cash,
        commission_pct=settings.commission_pct,
        slippage_pct=settings.slippage_pct,
        position_pct=settings.position_pct,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        trailing_stop_pct=settings.trailing_stop_pct,
    )


def _baseline_oos(df, strategy, risk: BacktestConfig, wf: WalkForwardResult, signals_fn) -> float:
    """Rendimento composto out-of-sample con i parametri di default (confronto)."""
    factor = 1.0
    for w in wf.windows:
        test_df = df[(df.index >= w.test_start) & (df.index < w.test_end)]
        res = run_backtest(signals_fn(test_df, strategy), risk)
        factor *= 1 + res.total_return_pct / 100
    return (factor - 1) * 100


def _fmt_params(row) -> str:
    parts = []
    for k, v in dataclasses.asdict(row.strategy).items():
        if k in ("trend_sma_period", "trend_slope_bars"):
            continue
        parts.append(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}")
    return f"{' '.join(parts)} | SL {row.risk.stop_loss_pct:g}% | TP {row.risk.take_profit_pct:g}%"


def run_symbol_report(symbol: str, settings: Settings, timeframe: str, since: str,
                      train_days: int = 180, test_days: int = 90) -> None:
    df = fetch_ohlcv(symbol, timeframe, since, settings.exchange_id)
    strategy = build_strategy_params(settings)
    risk = _base_risk(settings)
    signals_fn = _signals_fn(settings)

    print(f"\n=== {symbol} ({timeframe}, dal {since}) — famiglia {settings.strategy_family} ===")
    print(f"Grid: {len(DEFAULT_GRID.get('rsi_oversold', []))}x{len(DEFAULT_GRID.get('rsi_overbought', []))} "
          f"strategie x {len(DEFAULT_GRID.get('stop_loss_pct', []))}x{len(DEFAULT_GRID.get('take_profit_pct', []))} rischi")

    # migliori parametri in-sample (sull'intera storia — solo informativo)
    top = grid_search(df, strategy, risk, signals_fn=signals_fn)[0]
    print(f"Migliori in-sample:  {_fmt_params(top)}  ->  {top.score:+.2f}%")

    wf = walk_forward(df, strategy, risk, train_days=train_days, test_days=test_days, signals_fn=signals_fn)
    baseline = _baseline_oos(df, strategy, risk, wf, signals_fn)

    print(f"\nWalk-forward (train {train_days}g / test {test_days}g):")
    print(f"{'test window':24s} {'parametri scelti':52s} {'train':>8s} {'OOS':>8s}")
    for w in wf.windows:
        window = f"{w.test_start:%Y-%m-%d} -> {w.test_end:%Y-%m-%d}"
        print(f"{window:24s} {_fmt_params(w.best):52s} {w.best.score:+7.2f}% "
              f"{w.test_result.total_return_pct:+7.2f}%")

    print(f"\nOut-of-sample composto: {wf.oos_return_pct:+.2f}%   (parametri default: {baseline:+.2f}%)")
    if wf.oos_return_pct > baseline:
        print("L'ottimizzazione BATTE i parametri default fuori campione.")
    else:
        print("L'ottimizzazione NON batte i parametri default fuori campione.")


def main() -> None:
    settings = load_settings()
    args = sys.argv[1:]

    if args:
        symbol = args[0].upper()
        timeframe = args[1] if len(args) > 1 else settings.timeframe
        since = args[2] if len(args) > 2 else settings.since
        run_symbol_report(symbol, settings, timeframe, since)
        return

    # riepilogo su tutti i simboli
    print(f"Ottimizzazione walk-forward di {len(settings.symbols)} simboli "
          f"({settings.timeframe}, dal {settings.since}, famiglia {settings.strategy_family})")
    print(f"{'symbol':12s} {'OOS ottimizzato':>16s} {'OOS default':>12s} {'migliora?':>10s}")
    for symbol in settings.symbols:
        try:
            df = fetch_ohlcv(symbol, settings.timeframe, settings.since, settings.exchange_id)
            strategy = build_strategy_params(settings)
            risk = _base_risk(settings)
            signals_fn = _signals_fn(settings)
            wf = walk_forward(df, strategy, risk, signals_fn=signals_fn)
            baseline = _baseline_oos(df, strategy, risk, wf, signals_fn)
            better = "si" if wf.oos_return_pct > baseline else "no"
            print(f"{symbol:12s} {wf.oos_return_pct:+15.2f}% {baseline:+11.2f}% {better:>10s}")
        except Exception as e:  # noqa: BLE001
            print(f"{symbol:12s} ERRORE: {e}")


if __name__ == "__main__":
    main()
