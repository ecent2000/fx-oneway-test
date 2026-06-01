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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run cost and stress scenarios for regime-adaptive FX.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--start", default="2024-10-01T00:00:00Z")
    parser.add_argument("--end", default="2025-01-01T00:00:00Z")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
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
                params[key] = bool(value)
            elif isinstance(current, int):
                params[key] = int(value)
            elif isinstance(current, float):
                params[key] = float(value)
            else:
                params[key] = value
    return params


def main() -> None:
    args = parse_args()
    out_dir = args.reports_root.resolve() / REGIME_STRATEGY_ID / f"stress_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    params = load_params(args)
    scenarios = [
        {
            "scenario": "baseline",
            "max_spread_points": 0,
            "price_protection_points": 0,
            "leverage": 30.0,
            "prob_slippage": 0.0,
            "commission": None,
            "base_latency_nanos": 0,
            "include_quote_ticks": False,
        },
        {
            "scenario": "spread_filter_20",
            "max_spread_points": 20,
            "price_protection_points": 0,
            "leverage": 30.0,
            "prob_slippage": 0.0,
            "commission": None,
            "base_latency_nanos": 0,
            "include_quote_ticks": True,
        },
        {
            "scenario": "tight_price_protection",
            "max_spread_points": 0,
            "price_protection_points": 1,
            "leverage": 30.0,
            "prob_slippage": 0.0,
            "commission": None,
            "base_latency_nanos": 0,
            "include_quote_ticks": False,
        },
        {
            "scenario": "lower_leverage",
            "max_spread_points": 0,
            "price_protection_points": 0,
            "leverage": 10.0,
            "prob_slippage": 0.0,
            "commission": None,
            "base_latency_nanos": 0,
            "include_quote_ticks": False,
        },
        {
            "scenario": "prob_slippage_50",
            "max_spread_points": 0,
            "price_protection_points": 0,
            "leverage": 30.0,
            "prob_slippage": 0.5,
            "commission": None,
            "base_latency_nanos": 0,
            "include_quote_ticks": False,
        },
        {
            "scenario": "fixed_fee_2usd",
            "max_spread_points": 0,
            "price_protection_points": 0,
            "leverage": 30.0,
            "prob_slippage": 0.0,
            "commission": "2 USD",
            "base_latency_nanos": 0,
            "include_quote_ticks": False,
        },
        {
            "scenario": "latency_1s",
            "max_spread_points": 0,
            "price_protection_points": 0,
            "leverage": 30.0,
            "prob_slippage": 0.0,
            "commission": None,
            "base_latency_nanos": 1_000_000_000,
            "include_quote_ticks": False,
        },
    ]

    rows = []
    for scenario in scenarios:
        print(f"scenario: {scenario['scenario']}")
        scenario_params = {**params, "max_spread_points": scenario["max_spread_points"]}
        try:
            summary = run_regime_backtest(
                catalog=args.catalog,
                reports_root=args.reports_root,
                instrument_id=args.instrument_id,
                bar_type=bar_type_for(args.instrument_id, args.bar_minutes, "MID"),
                start=args.start,
                end=args.end,
                params=scenario_params,
                trade_size=args.trade_size,
                log_level="OFF",
                report_prefix=scenario["scenario"],
                write_reports=False,
                default_leverage=scenario["leverage"],
                price_protection_points=scenario["price_protection_points"],
                include_quote_ticks=scenario["include_quote_ticks"],
                prob_slippage=scenario["prob_slippage"],
                random_seed=42,
                commission=scenario["commission"],
                base_latency_nanos=scenario["base_latency_nanos"],
            )
            row = {**scenario, **summary["metrics"], "error": ""}
        except Exception as exc:
            row = {**scenario, "error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
        pd.DataFrame(rows).to_csv(out_dir / "stress_results.csv", index=False)

    results = pd.DataFrame(rows)
    print()
    print(f"results: {out_dir / 'stress_results.csv'}")
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
