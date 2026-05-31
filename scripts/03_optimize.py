from __future__ import annotations

import argparse
import itertools
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.backtest_runner import bar_type_for  # noqa: E402
from fx_factor.backtest_runner import run_backtest  # noqa: E402
from fx_factor.backtest_runner import utc_stamp  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grid-search rolling z-score FX parameters.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2024-07-01T00:00:00Z")
    parser.add_argument("--lookbacks", type=int, nargs="+", default=[48, 96, 192])
    parser.add_argument("--entry-zs", type=float, nargs="+", default=[1.0, 1.5, 2.0])
    parser.add_argument("--exit-zs", type=float, nargs="+", default=[0.0, 0.2, 0.5])
    parser.add_argument("--bar-minutes", type=int, nargs="+", default=[5, 15, 60])
    parser.add_argument("--stop-zs", type=float, nargs="+", default=[0.0])
    parser.add_argument("--max-position-bars", type=int, nargs="+", default=[0])
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for smoke tests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / f"optimize_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    grid = list(
        itertools.product(
            args.bar_minutes,
            args.lookbacks,
            args.entry_zs,
            args.exit_zs,
            args.stop_zs,
            args.max_position_bars,
        ),
    )
    if args.limit > 0:
        grid = grid[: args.limit]

    for i, (bar_minutes, lookback, entry_z, exit_z, stop_z, max_position_bars) in enumerate(grid, 1):
        bar_type = bar_type_for(args.instrument_id, bar_minutes, "MID")
        print(
            f"[{i}/{len(grid)}] {bar_minutes}m lookback={lookback} "
            f"entry={entry_z} exit={exit_z} stop={stop_z} max_bars={max_position_bars}",
            flush=True,
        )
        summary = run_backtest(
            catalog=args.catalog,
            reports_root=out_dir,
            instrument_id=args.instrument_id,
            bar_type=bar_type,
            data_kind="bars",
            start=args.start,
            end=args.end,
            lookback=lookback,
            entry_z=entry_z,
            exit_z=exit_z,
            stop_z=stop_z,
            max_position_bars=max_position_bars,
            trade_size=args.trade_size,
            starting_balance=args.starting_balance,
            log_level="OFF",
            report_prefix="trial",
            write_reports=False,
        )
        row = {
            "bar_minutes": bar_minutes,
            "lookback": lookback,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "stop_z": stop_z,
            "max_position_bars": max_position_bars,
            **summary["metrics"],
        }
        rows.append(row)
        pd.DataFrame(rows).to_csv(out_dir / "optimization_results.csv", index=False)

    results = pd.DataFrame(rows)
    if not results.empty:
        results["score"] = (
            results["profit_factor"].fillna(0.0)
            + results["sharpe"].fillna(0.0)
            + results["pnl_usd"].fillna(0.0) / 10_000.0
        )
        results = results.sort_values(
            ["profit_factor", "sharpe", "pnl_usd", "positions"],
            ascending=[False, False, False, False],
        )
        results.to_csv(out_dir / "optimization_results.csv", index=False)
        print()
        print(f"results: {out_dir / 'optimization_results.csv'}")
        print(results.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
