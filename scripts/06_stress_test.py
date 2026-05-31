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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run cost and stress scenarios for rolling z-score FX.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--start", default="2024-10-01T00:00:00Z")
    parser.add_argument("--end", default="2025-01-01T00:00:00Z")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--lookback", type=int, default=96)
    parser.add_argument("--entry-z", type=float, default=1.5)
    parser.add_argument("--exit-z", type=float, default=0.2)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / f"stress_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios = [
        {"scenario": "baseline", "price_protection_points": 0, "leverage": 30.0, "stop_z": 0.0, "max_position_bars": 0},
        {"scenario": "tight_protection", "price_protection_points": 1, "leverage": 30.0, "stop_z": 0.0, "max_position_bars": 0},
        {"scenario": "lower_leverage", "price_protection_points": 0, "leverage": 10.0, "stop_z": 0.0, "max_position_bars": 0},
        {"scenario": "z_stop_3", "price_protection_points": 0, "leverage": 30.0, "stop_z": 3.0, "max_position_bars": 0},
        {"scenario": "max_hold_2d", "price_protection_points": 0, "leverage": 30.0, "stop_z": 0.0, "max_position_bars": 192},
    ]
    rows = []
    for scenario in scenarios:
        print(f"scenario: {scenario['scenario']}")
        summary = run_backtest(
            catalog=args.catalog,
            reports_root=out_dir,
            instrument_id=args.instrument_id,
            bar_type=bar_type_for(args.instrument_id, args.bar_minutes, "MID"),
            data_kind="bars",
            start=args.start,
            end=args.end,
            lookback=args.lookback,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            stop_z=scenario["stop_z"],
            max_position_bars=scenario["max_position_bars"],
            trade_size=args.trade_size,
            log_level="OFF",
            report_prefix=scenario["scenario"],
            write_reports=False,
            default_leverage=scenario["leverage"],
            price_protection_points=scenario["price_protection_points"],
        )
        rows.append({**scenario, **summary["metrics"]})
        pd.DataFrame(rows).to_csv(out_dir / "stress_results.csv", index=False)

    results = pd.DataFrame(rows)
    print()
    print(f"results: {out_dir / 'stress_results.csv'}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
