"""CLI di backtest: python -m logitrade.backtest_cli PEPE/USDT 1h 2024-01-01

Senza argomenti esegue la scan di tutti i simboli di config.yaml.
--compare TF [TF...] confronta la scan su piu' timeframe:
  python -m logitrade.backtest_cli --compare 1h 4h 1d
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .config import load_settings
from .report import equity_chart, format_report
from .runner import compare_timeframes, run_symbol


def main() -> None:
    settings = load_settings()
    args = sys.argv[1:]

    if args and args[0] == "--compare":
        timeframes = args[1:] or [settings.timeframe, "4h", "1d"]
        print(f"Confronto di {len(settings.symbols)} simboli su timeframe: {', '.join(timeframes)} "
              f"(dal {settings.since})\n")
        results = compare_timeframes(settings.symbols, settings, timeframes)
        print(f"{'symbol':12s} " + " ".join(f"{tf:>16s}" for tf in timeframes))
        for symbol in settings.symbols:
            cells = []
            for tf in timeframes:
                r = results[tf].get(symbol)
                cells.append(f"{r.total_return_pct:+7.2f}%/{r.n_trades:2d}t" if r is not None else "ERRORE")
            print(f"{symbol:12s} " + " ".join(f"{c:>16s}" for c in cells))
        return

    if args:
        symbol = args[0].upper()
        timeframe = args[1] if len(args) > 1 else settings.timeframe
        since = args[2] if len(args) > 2 else settings.since
        out = run_symbol(symbol, settings, timeframe, since)
        print(format_report(out.symbol, out.timeframe, out.since, out.result).replace("<b>", "").replace("</b>", ""))
        with open("equity.png", "wb") as f:
            f.write(equity_chart(out.result, out.symbol))
        print("\nGrafico salvato in equity.png")
        return

    print(f"Scan di {len(settings.symbols)} simboli ({settings.timeframe}, dal {settings.since})\n")
    for symbol in settings.symbols:
        try:
            out = run_symbol(symbol, settings)
            r = out.result
            print(f"{symbol:12s} netto {r.total_return_pct:+8.2f}% | dd {r.max_drawdown_pct:6.2f}% | "
                  f"win {r.win_rate_pct:5.1f}% | trade {r.n_trades:3d} | buy&hold {r.buy_hold_return_pct:+8.2f}%")
        except Exception as e:  # noqa: BLE001
            print(f"{symbol:12s} ERRORE: {e}")


if __name__ == "__main__":
    main()
