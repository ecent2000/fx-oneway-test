from __future__ import annotations

import argparse
import itertools
import sys
from decimal import Decimal
from math import isfinite
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
    parser = argparse.ArgumentParser(description="Scan long/short-balanced parameters for regime-adaptive FX.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--long-enabled-regimes", nargs="+", default=["wide_bull", "wide_range", "wide_bull,wide_range"])
    parser.add_argument("--short-enabled-regimes", nargs="+", default=["wide_bear", "wide_bear,wide_range"])
    parser.add_argument("--long-entry-hours-utc", nargs="+", default=["6-8", "7-9", "12-21"])
    parser.add_argument("--short-entry-hours-utc", nargs="+", default=["12-15", "13-15"])
    parser.add_argument("--long-min-target-atr-mult", nargs="+", type=float, default=[0.8, 1.0, 1.2])
    parser.add_argument("--short-min-target-atr-mult", nargs="+", type=float, default=[0.8, 1.0, 1.2])
    parser.add_argument("--long-stop-atr-mult", nargs="+", type=float, default=[1.5, 2.0, 2.5])
    parser.add_argument("--short-stop-atr-mult", nargs="+", type=float, default=[1.5, 2.0, 2.5])
    parser.add_argument("--long-confirm-bars", nargs="+", type=int, default=[4, 6, 8])
    parser.add_argument("--short-confirm-bars", nargs="+", type=int, default=[4, 6])
    parser.add_argument("--strict-long-filter", choices=["true", "false"], default="false")
    parser.add_argument("--limit", type=int, default=0, help="Optional candidate cap for smoke tests.")
    return parser.parse_args()


def bool_text(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def flatten_side_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for side in ["long", "short"]:
        for key, value in summary["side_metrics"][side].items():
            metrics[f"{side}_{key}"] = value
    return metrics


def candidate_grid(args: argparse.Namespace) -> list[dict[str, Any]]:
    base = default_params()
    base.update(
        {
            "trade_direction": "long_short",
            "strict_long_filter": bool_text(args.strict_long_filter),
            "disabled_entry_regimes": "",
            "entry_hours_utc": "",
        }
    )
    keys = [
        "long_enabled_regimes",
        "short_enabled_regimes",
        "long_entry_hours_utc",
        "short_entry_hours_utc",
        "long_min_target_atr_mult",
        "short_min_target_atr_mult",
        "long_stop_atr_mult",
        "short_stop_atr_mult",
        "long_confirm_bars",
        "short_confirm_bars",
    ]
    values = [
        args.long_enabled_regimes,
        args.short_enabled_regimes,
        args.long_entry_hours_utc,
        args.short_entry_hours_utc,
        args.long_min_target_atr_mult,
        args.short_min_target_atr_mult,
        args.long_stop_atr_mult,
        args.short_stop_atr_mult,
        args.long_confirm_bars,
        args.short_confirm_bars,
    ]
    grid = []
    for combo in itertools.product(*values):
        params = {**base, **dict(zip(keys, combo, strict=True))}
        grid.append(params)
    if args.limit > 0:
        grid = grid[: args.limit]
    return grid


def aggregate_side(rows: pd.DataFrame, side: str) -> dict[str, Any]:
    trades = int(rows[f"{side}_trades"].sum())
    pnl = float(rows[f"{side}_pnl_usd"].sum())
    split_values = rows[f"{side}_pnl_usd"]
    gross_profit = float(rows[f"{side}_gross_profit_usd"].sum())
    gross_loss = float(rows[f"{side}_gross_loss_usd"].sum())
    weighted_win_rate = (
        float((rows[f"{side}_win_rate"] * rows[f"{side}_trades"]).sum() / trades)
        if trades > 0
        else float("nan")
    )
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    return {
        f"{side}_trades": trades,
        f"{side}_pnl_usd": pnl,
        f"{side}_gross_profit_usd": gross_profit,
        f"{side}_gross_loss_usd": gross_loss,
        f"{side}_win_rate": weighted_win_rate,
        f"{side}_profit_factor": profit_factor,
        f"{side}_min_split_pnl_usd": float(split_values.min()),
        f"{side}_worst_split_drawdown_usd": float(rows[f"{side}_max_drawdown_usd"].min()),
    }


def summarize_candidate(candidate_id: int, params: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    table = pd.DataFrame(rows)
    long = aggregate_side(table, "long")
    short = aggregate_side(table, "short")
    total_trades = long["long_trades"] + short["short_trades"]
    long_share = long["long_trades"] / total_trades if total_trades > 0 else float("nan")
    win_rate_gap = abs(long["long_win_rate"] - 0.55) + abs(short["short_win_rate"] - 0.55)
    balance_gap = abs(long_share - 0.5) if isfinite(long_share) else float("inf")
    balance_penalty = 0.0
    if isfinite(long_share):
        balance_penalty = max(0.0, 0.4 - long_share, long_share - 0.6)
    total_pnl = float(table["pnl_usd"].sum())
    summary = {
        "candidate": candidate_id,
        **params,
        "total_pnl_usd": total_pnl,
        "total_positions": int(table["positions"].sum()),
        "overall_median_win_rate": float(table["win_rate"].median()),
        "overall_median_profit_factor": float(table["profit_factor"].median()),
        "positive_splits": int((table["pnl_usd"] > 0.0).sum()),
        "worst_split_pnl_usd": float(table["pnl_usd"].min()),
        "long_share": float(long_share),
        "win_rate_gap_to_55": float(win_rate_gap),
        "balance_gap_to_50": float(balance_gap),
        **long,
        **short,
    }
    summary["constraints_pass"] = (
        long["long_trades"] >= 40
        and short["short_trades"] >= 40
        and long["long_win_rate"] >= 0.52
        and short["short_win_rate"] >= 0.52
        and long["long_profit_factor"] >= 1.15
        and short["short_profit_factor"] >= 1.15
        and 0.4 <= long_share <= 0.6
        and total_pnl > 0.0
    )
    summary["balance_score"] = (
        (1.0 - win_rate_gap) * 1_000.0
        - balance_gap * 500.0
        + total_pnl / 100.0
        + long["long_profit_factor"] * 50.0
        + short["short_profit_factor"] * 50.0
        - balance_penalty * 2_000.0
        + summary["positive_splits"] * 25.0
        + summary["worst_split_pnl_usd"] / 10.0
    )
    return summary


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / REGIME_STRATEGY_ID / f"balanced_scan_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    grid = candidate_grid(args)

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for candidate_id, params in enumerate(grid, 1):
        print(
            f"candidate {candidate_id}/{len(grid)} "
            f"L={params['long_enabled_regimes']}@{params['long_entry_hours_utc']} "
            f"S={params['short_enabled_regimes']}@{params['short_entry_hours_utc']} "
            f"target={params['long_min_target_atr_mult']}/{params['short_min_target_atr_mult']} "
            f"stop={params['long_stop_atr_mult']}/{params['short_stop_atr_mult']} "
            f"confirm={params['long_confirm_bars']}/{params['short_confirm_bars']}",
            flush=True,
        )

        candidate_rows = []
        for split, (start, end) in SPLITS.items():
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
                report_prefix=f"balanced_c{candidate_id}_{split}",
                write_reports=False,
            )
            row = {
                "candidate": candidate_id,
                "split": split,
                "start": start,
                "end": end,
                **params,
                **summary["metrics"],
                **flatten_side_metrics(summary),
            }
            candidate_rows.append(row)
            all_rows.append(row)
        pd.DataFrame(all_rows).to_csv(out_dir / "balanced_split_results.csv", index=False)

        summaries.append(summarize_candidate(candidate_id, params, candidate_rows))
        pd.DataFrame(summaries).to_csv(out_dir / "balanced_summary.csv", index=False)

    summary_table = pd.DataFrame(summaries).sort_values(
        [
            "constraints_pass",
            "win_rate_gap_to_55",
            "balance_gap_to_50",
            "balance_score",
            "total_pnl_usd",
        ],
        ascending=[False, True, True, False, False],
    )
    summary_table.to_csv(out_dir / "balanced_summary.csv", index=False)
    passed = summary_table[summary_table["constraints_pass"]]
    passed.to_csv(out_dir / "balanced_passed.csv", index=False)
    print()
    print(f"results: {out_dir / 'balanced_summary.csv'}")
    print(summary_table.head(25).to_string(index=False))


if __name__ == "__main__":
    main()
