from __future__ import annotations

import argparse
import itertools
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan entry regime/session filters for regime-adaptive FX.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--disabled-entry-regimes", nargs="+", default=["", "narrow_bear", "narrow_bear,narrow_bull"])
    parser.add_argument("--entry-hours-utc", nargs="+", default=["", "7-15", "8-15", "12-15", "12-16", "7-20"])
    parser.add_argument("--limit", type=int, default=0, help="Optional cap for smoke tests.")
    return parser.parse_args()


def stability_score(summary: dict[str, Any]) -> float:
    return (
        summary["positive_splits"] * 10_000.0
        + summary["positive_final_tests"] * 5_000.0
        + summary["positive_years"] * 3_000.0
        + summary["total_pnl_usd"] / 10.0
        + summary["median_profit_factor"] * 100.0
        + summary["median_win_rate"] * 100.0
        + summary["worst_split_pnl_usd"] / 5.0
    )


def summarize_candidate(candidate_id: int, params: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    table = pd.DataFrame(rows)
    year_pnl = {
        year: table[table["split"].str.startswith(year)]["pnl_usd"].sum()
        for year in ["2023", "2024", "2025"]
    }
    final_tests = table[table["split"].str.endswith("final_test")]
    summary = {
        "candidate": candidate_id,
        "disabled_entry_regimes": params["disabled_entry_regimes"],
        "entry_hours_utc": params["entry_hours_utc"],
        "positive_splits": int((table["pnl_usd"] > 0.0).sum()),
        "positive_final_tests": int((final_tests["pnl_usd"] > 0.0).sum()),
        "positive_years": sum(1 for pnl in year_pnl.values() if pnl > 0.0),
        "total_pnl_usd": float(table["pnl_usd"].sum()),
        "avg_pnl_usd": float(table["pnl_usd"].mean()),
        "median_pnl_usd": float(table["pnl_usd"].median()),
        "worst_split_pnl_usd": float(table["pnl_usd"].min()),
        "best_split_pnl_usd": float(table["pnl_usd"].max()),
        "avg_win_rate": float(table["win_rate"].mean()),
        "median_win_rate": float(table["win_rate"].median()),
        "min_win_rate": float(table["win_rate"].min()),
        "avg_profit_factor": float(table["profit_factor"].mean()),
        "median_profit_factor": float(table["profit_factor"].median()),
        "min_profit_factor": float(table["profit_factor"].min()),
        "total_positions": int(table["positions"].sum()),
        "pnl_2023": float(year_pnl["2023"]),
        "pnl_2024": float(year_pnl["2024"]),
        "pnl_2025": float(year_pnl["2025"]),
    }
    summary["stability_score"] = stability_score(summary)
    return summary


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / REGIME_STRATEGY_ID / f"filter_scan_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = default_params()

    grid = list(itertools.product(args.disabled_entry_regimes, args.entry_hours_utc))
    if args.limit > 0:
        grid = grid[: args.limit]

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for candidate_id, (disabled_entry_regimes, entry_hours_utc) in enumerate(grid, 1):
        params = {
            **base,
            "disabled_entry_regimes": disabled_entry_regimes,
            "entry_hours_utc": entry_hours_utc,
        }
        print(
            f"candidate {candidate_id}/{len(grid)} "
            f"disabled={disabled_entry_regimes or '<none>'} hours={entry_hours_utc or '<all>'}",
            flush=True,
        )

        candidate_rows = []
        for split, (start, end) in SPLITS.items():
            print(f"  {split}", flush=True)
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
                report_prefix=f"filter_c{candidate_id}_{split}",
                write_reports=False,
            )
            row = {
                "candidate": candidate_id,
                "split": split,
                "start": start,
                "end": end,
                "bar_minutes": args.bar_minutes,
                **params,
                **summary["metrics"],
            }
            candidate_rows.append(row)
            all_rows.append(row)
            pd.DataFrame(all_rows).to_csv(out_dir / "filter_split_results.csv", index=False)

        summaries.append(summarize_candidate(candidate_id, params, candidate_rows))
        pd.DataFrame(summaries).to_csv(out_dir / "filter_summary.csv", index=False)

    summary_table = pd.DataFrame(summaries).sort_values(
        [
            "stability_score",
            "positive_splits",
            "positive_final_tests",
            "positive_years",
            "total_pnl_usd",
            "worst_split_pnl_usd",
        ],
        ascending=[False, False, False, False, False, False],
    )
    summary_table.to_csv(out_dir / "filter_summary.csv", index=False)
    print()
    print(f"results: {out_dir / 'filter_summary.csv'}")
    print(summary_table.to_string(index=False))


if __name__ == "__main__":
    main()
