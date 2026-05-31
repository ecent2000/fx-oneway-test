from __future__ import annotations

import argparse
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


SPLITS = {
    "train": ("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
    "validation": ("2024-07-01T00:00:00Z", "2024-10-01T00:00:00Z"),
    "final_test": ("2024-10-01T00:00:00Z", "2025-01-01T00:00:00Z"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fixed train/validation/final OOS checks.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--lookback", type=int, default=96)
    parser.add_argument("--entry-z", type=float, default=1.5)
    parser.add_argument("--exit-z", type=float, default=0.2)
    parser.add_argument("--stop-z", type=float, default=0.0)
    parser.add_argument("--max-position-bars", type=int, default=0)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / f"validate_oos_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for split, (start, end) in SPLITS.items():
        print(f"{split}: {start} -> {end}")
        summary = run_backtest(
            catalog=args.catalog,
            reports_root=out_dir,
            instrument_id=args.instrument_id,
            bar_type=bar_type_for(args.instrument_id, args.bar_minutes, "MID"),
            data_kind="bars",
            start=start,
            end=end,
            lookback=args.lookback,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            stop_z=args.stop_z,
            max_position_bars=args.max_position_bars,
            trade_size=args.trade_size,
            log_level="OFF",
            report_prefix=split,
            write_reports=False,
        )
        rows.append({"split": split, "start": start, "end": end, **summary["metrics"]})

    results = pd.DataFrame(rows)
    results.to_csv(out_dir / "oos_results.csv", index=False)
    print()
    print(f"results: {out_dir / 'oos_results.csv'}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
