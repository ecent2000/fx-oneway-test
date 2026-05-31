from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.backtest_runner import run_backtest  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an EUR/USD rolling z-score backtest.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-type", default="EUR/USD.SIM-15-MINUTE-MID-EXTERNAL")
    parser.add_argument("--data-kind", choices=["bars", "ticks"], default="bars")
    parser.add_argument("--start", default="2024-10-01T00:00:00Z")
    parser.add_argument("--end", default="2024-11-01T00:00:00Z")
    parser.add_argument("--lookback", type=int, default=96)
    parser.add_argument("--entry-z", type=float, default=1.5)
    parser.add_argument("--exit-z", type=float, default=0.2)
    parser.add_argument("--stop-z", type=float, default=0.0)
    parser.add_argument("--max-position-bars", type=int, default=0)
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--log-level", default="ERROR")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_backtest(
        catalog=args.catalog,
        reports_root=args.reports_root,
        instrument_id=args.instrument_id,
        bar_type=args.bar_type,
        data_kind=args.data_kind,
        start=args.start,
        end=args.end,
        lookback=args.lookback,
        entry_z=args.entry_z,
        exit_z=args.exit_z,
        stop_z=args.stop_z,
        max_position_bars=args.max_position_bars,
        trade_size=args.trade_size,
        starting_balance=args.starting_balance,
        chunk_size=args.chunk_size,
        log_level=args.log_level,
        report_prefix="backtest",
        write_reports=True,
    )

    metrics = summary["metrics"]
    print(f"reports:         {summary['reports']['dir']}")
    print(f"iterations:      {metrics['iterations']:,}")
    print(f"orders:          {metrics['orders']:,}")
    print(f"positions:       {metrics['positions']:,}")
    print(f"pnl_usd:         {metrics['pnl_usd']}")
    print(f"win_rate:        {metrics['win_rate']}")
    print(f"sharpe:          {metrics['sharpe']}")
    print(f"profit_factor:   {metrics['profit_factor']}")


if __name__ == "__main__":
    main()
