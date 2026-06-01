from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.regime_backtest_runner import REGIME_STRATEGY_ID  # noqa: E402
from fx_factor.regime_backtest_runner import bar_type_for  # noqa: E402
from fx_factor.regime_backtest_runner import default_params  # noqa: E402
from fx_factor.regime_backtest_runner import run_regime_backtest  # noqa: E402
from fx_factor.regime_backtest_runner import utc_stamp  # noqa: E402


SPLITS = {
    "2023_train_opt": ("2023-01-01T00:00:00Z", "2023-07-01T00:00:00Z"),
    "2023_validation": ("2023-07-01T00:00:00Z", "2023-10-01T00:00:00Z"),
    "2023_final_test": ("2023-10-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    "2024_train_opt": ("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
    "2024_validation": ("2024-07-01T00:00:00Z", "2024-10-01T00:00:00Z"),
    "2024_final_test": ("2024-10-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    "2025_train_opt": ("2025-01-01T00:00:00Z", "2025-07-01T00:00:00Z"),
    "2025_validation": ("2025-07-01T00:00:00Z", "2025-10-01T00:00:00Z"),
    "2025_final_test": ("2025-10-01T00:00:00Z", "2026-01-01T00:00:00Z"),
}


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run annual OOS checks for regime-adaptive FX timing.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--params-csv", type=Path, default=None)
    parser.add_argument("--params-row", type=int, default=0)
    return parser.parse_args()


def load_params(args: argparse.Namespace) -> dict[str, Any]:
    params = default_params()
    if args.params_csv is None:
        return params

    table = pd.read_csv(args.params_csv, keep_default_na=False)
    row = table.iloc[args.params_row].to_dict()
    for key in params:
        if key in row and pd.notna(row[key]):
            value = row[key]
            current = params[key]
            if isinstance(current, bool):
                params[key] = parse_bool(value)
            elif isinstance(current, int):
                params[key] = int(value)
            elif isinstance(current, float):
                params[key] = float(value)
            else:
                params[key] = value
    return params


def flatten_side_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for side in ["long", "short"]:
        for key, value in summary["side_metrics"][side].items():
            metrics[f"{side}_{key}"] = value
    return metrics


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / REGIME_STRATEGY_ID / f"validate_oos_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    params = load_params(args)

    rows = []
    for split, (start, end) in SPLITS.items():
        print(f"{split}: {start} -> {end}")
        summary = run_regime_backtest(
            catalog=args.catalog,
            reports_root=args.reports_root,
            instrument_id=args.instrument_id,
            bar_type=bar_type_for(args.instrument_id, args.bar_minutes, "MID"),
            start=start,
            end=end,
            params=params,
            trade_size=args.trade_size,
            starting_balance=args.starting_balance,
            log_level="OFF",
            report_prefix=split,
            write_reports=False,
        )
        rows.append(
            {
                "split": split,
                "start": start,
                "end": end,
                **params,
                **summary["metrics"],
                **flatten_side_metrics(summary),
            }
        )
        pd.DataFrame(rows).to_csv(out_dir / "oos_results.csv", index=False)

    results = pd.DataFrame(rows)
    print()
    print(f"results: {out_dir / 'oos_results.csv'}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
