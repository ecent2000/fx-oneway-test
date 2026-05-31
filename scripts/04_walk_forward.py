from __future__ import annotations

import argparse
import itertools
import sys
from datetime import date
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


def iso(day: pd.Timestamp | date) -> str:
    ts = pd.Timestamp(day)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward rolling z-score FX parameters.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--windows", type=int, default=12)
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument("--lookbacks", type=int, nargs="+", default=[48, 96, 192])
    parser.add_argument("--entry-zs", type=float, nargs="+", default=[1.0, 1.5, 2.0])
    parser.add_argument("--exit-zs", type=float, nargs="+", default=[0.0, 0.2, 0.5])
    parser.add_argument("--bar-minutes", type=int, nargs="+", default=[15])
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for smoke tests.")
    return parser.parse_args()


def run_one(args: argparse.Namespace, out_dir: Path, row: dict, start: str, end: str, prefix: str):
    return run_backtest(
        catalog=args.catalog,
        reports_root=out_dir,
        instrument_id=args.instrument_id,
        bar_type=bar_type_for(args.instrument_id, int(row["bar_minutes"]), "MID"),
        data_kind="bars",
        start=start,
        end=end,
        lookback=int(row["lookback"]),
        entry_z=float(row["entry_z"]),
        exit_z=float(row["exit_z"]),
        stop_z=float(row.get("stop_z", 0.0)),
        max_position_bars=int(row.get("max_position_bars", 0)),
        trade_size=args.trade_size,
        log_level="OFF",
        report_prefix=prefix,
        write_reports=False,
    )


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / f"walk_forward_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    grid = [
        {
            "bar_minutes": bar_minutes,
            "lookback": lookback,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "stop_z": 0.0,
            "max_position_bars": 0,
        }
        for bar_minutes, lookback, entry_z, exit_z in itertools.product(
            args.bar_minutes,
            args.lookbacks,
            args.entry_zs,
            args.exit_zs,
        )
    ]
    if args.limit > 0:
        grid = grid[: args.limit]

    rows = []
    anchor = pd.Timestamp(args.start, tz="UTC")
    for window_idx in range(args.windows):
        train_start = anchor + pd.DateOffset(months=window_idx * args.test_months)
        train_end = train_start + pd.DateOffset(months=args.train_months)
        test_end = train_end + pd.DateOffset(months=args.test_months)
        print(f"window {window_idx + 1}/{args.windows}: train {train_start.date()}->{train_end.date()} test {train_end.date()}->{test_end.date()}")

        train_rows = []
        for params in grid:
            train_summary = run_one(args, out_dir, params, iso(train_start), iso(train_end), "wf_train")
            train_rows.append({**params, **train_summary["metrics"]})
        best = sorted(
            train_rows,
            key=lambda r: (r.get("profit_factor") or 0.0, r.get("sharpe") or 0.0, r.get("pnl_usd") or 0.0),
            reverse=True,
        )[0]
        test_summary = run_one(args, out_dir, best, iso(train_end), iso(test_end), "wf_test")
        rows.append(
            {
                "window": window_idx + 1,
                "train_start": iso(train_start),
                "train_end": iso(train_end),
                "test_end": iso(test_end),
                **{f"param_{k}": best[k] for k in ["bar_minutes", "lookback", "entry_z", "exit_z"]},
                "train_pnl_usd": best["pnl_usd"],
                "train_profit_factor": best["profit_factor"],
                "train_sharpe": best["sharpe"],
                **{f"test_{k}": v for k, v in test_summary["metrics"].items()},
            },
        )
        pd.DataFrame(rows).to_csv(out_dir / "walk_forward_results.csv", index=False)

    results = pd.DataFrame(rows)
    print()
    print(f"results: {out_dir / 'walk_forward_results.csv'}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
