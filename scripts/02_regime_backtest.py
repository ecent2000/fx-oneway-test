from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.regime_backtest_runner import default_params  # noqa: E402
from fx_factor.regime_backtest_runner import run_regime_backtest  # noqa: E402


def add_strategy_args(parser: argparse.ArgumentParser) -> None:
    defaults = default_params()
    parser.add_argument("--channel-lookback", type=int, default=defaults["channel_lookback"])
    parser.add_argument("--atr-lookback", type=int, default=defaults["atr_lookback"])
    parser.add_argument("--trend-lookback", type=int, default=defaults["trend_lookback"])
    parser.add_argument("--momentum-lookback", type=int, default=defaults["momentum_lookback"])
    parser.add_argument("--breakout-lookback", type=int, default=defaults["breakout_lookback"])
    parser.add_argument("--short-ma-lookback", type=int, default=defaults["short_ma_lookback"])
    parser.add_argument("--confirm-bars", type=int, default=defaults["confirm_bars"])
    parser.add_argument("--long-confirm-bars", type=int, default=defaults["long_confirm_bars"])
    parser.add_argument("--short-confirm-bars", type=int, default=defaults["short_confirm_bars"])
    parser.add_argument("--bull-threshold", type=float, default=defaults["bull_threshold"])
    parser.add_argument("--bear-threshold", type=float, default=defaults["bear_threshold"])
    parser.add_argument("--flat-threshold", type=float, default=defaults["flat_threshold"])
    parser.add_argument("--narrow-width-threshold", type=float, default=defaults["narrow_width_threshold"])
    parser.add_argument("--wide-range-threshold", type=float, default=defaults["wide_range_threshold"])
    parser.add_argument("--momentum-entry", type=float, default=defaults["momentum_entry"])
    parser.add_argument("--pullback-buy-zone", type=float, default=defaults["pullback_buy_zone"])
    parser.add_argument("--pullback-sell-zone", type=float, default=defaults["pullback_sell_zone"])
    parser.add_argument("--lower-third", type=float, default=defaults["lower_third"])
    parser.add_argument("--upper-third", type=float, default=defaults["upper_third"])
    parser.add_argument("--stop-atr-mult", type=float, default=defaults["stop_atr_mult"])
    parser.add_argument("--long-stop-atr-mult", type=float, default=defaults["long_stop_atr_mult"])
    parser.add_argument("--short-stop-atr-mult", type=float, default=defaults["short_stop_atr_mult"])
    parser.add_argument("--max-position-bars", type=int, default=defaults["max_position_bars"])
    parser.add_argument("--max-spread-points", type=int, default=defaults["max_spread_points"])
    parser.add_argument("--cooldown-bars", type=int, default=defaults["cooldown_bars"])
    parser.add_argument("--min-regime-bars", type=int, default=defaults["min_regime_bars"])
    parser.add_argument("--min-target-atr-mult", type=float, default=defaults["min_target_atr_mult"])
    parser.add_argument("--long-min-target-atr-mult", type=float, default=defaults["long_min_target_atr_mult"])
    parser.add_argument("--short-min-target-atr-mult", type=float, default=defaults["short_min_target_atr_mult"])
    parser.add_argument("--min-target-spread-mult", type=float, default=defaults["min_target_spread_mult"])
    parser.add_argument("--assumed-spread-points", type=int, default=defaults["assumed_spread_points"])
    parser.add_argument("--trade-direction", choices=["long_short", "long_only", "short_only"], default=defaults["trade_direction"])
    parser.add_argument("--strict-long-filter", dest="strict_long_filter", action="store_true")
    parser.add_argument("--no-strict-long-filter", dest="strict_long_filter", action="store_false")
    parser.set_defaults(strict_long_filter=defaults["strict_long_filter"])
    parser.add_argument("--strict-long-trend-mult", type=float, default=defaults["strict_long_trend_mult"])
    parser.add_argument("--disable-wide-range-longs", action="store_true", default=defaults["disable_wide_range_longs"])
    parser.add_argument("--range-breakout-buffer-atr", type=float, default=defaults["range_breakout_buffer_atr"])
    parser.add_argument("--no-range-structure-filter", action="store_true")
    parser.add_argument("--range-structure-lookback", type=int, default=defaults["range_structure_lookback"])
    parser.add_argument("--disabled-entry-regimes", default=defaults["disabled_entry_regimes"])
    parser.add_argument("--entry-hours-utc", default=defaults["entry_hours_utc"])
    parser.add_argument("--long-enabled-regimes", default=defaults["long_enabled_regimes"])
    parser.add_argument("--short-enabled-regimes", default=defaults["short_enabled_regimes"])
    parser.add_argument("--long-entry-hours-utc", default=defaults["long_entry_hours_utc"])
    parser.add_argument("--short-entry-hours-utc", default=defaults["short_entry_hours_utc"])


def params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "channel_lookback": args.channel_lookback,
        "atr_lookback": args.atr_lookback,
        "trend_lookback": args.trend_lookback,
        "momentum_lookback": args.momentum_lookback,
        "breakout_lookback": args.breakout_lookback,
        "short_ma_lookback": args.short_ma_lookback,
        "confirm_bars": args.confirm_bars,
        "long_confirm_bars": args.long_confirm_bars,
        "short_confirm_bars": args.short_confirm_bars,
        "bull_threshold": args.bull_threshold,
        "bear_threshold": args.bear_threshold,
        "flat_threshold": args.flat_threshold,
        "narrow_width_threshold": args.narrow_width_threshold,
        "wide_range_threshold": args.wide_range_threshold,
        "momentum_entry": args.momentum_entry,
        "pullback_buy_zone": args.pullback_buy_zone,
        "pullback_sell_zone": args.pullback_sell_zone,
        "lower_third": args.lower_third,
        "upper_third": args.upper_third,
        "stop_atr_mult": args.stop_atr_mult,
        "long_stop_atr_mult": args.long_stop_atr_mult,
        "short_stop_atr_mult": args.short_stop_atr_mult,
        "max_position_bars": args.max_position_bars,
        "max_spread_points": args.max_spread_points,
        "cooldown_bars": args.cooldown_bars,
        "min_regime_bars": args.min_regime_bars,
        "min_target_atr_mult": args.min_target_atr_mult,
        "long_min_target_atr_mult": args.long_min_target_atr_mult,
        "short_min_target_atr_mult": args.short_min_target_atr_mult,
        "min_target_spread_mult": args.min_target_spread_mult,
        "assumed_spread_points": args.assumed_spread_points,
        "trade_direction": args.trade_direction,
        "strict_long_filter": args.strict_long_filter,
        "strict_long_trend_mult": args.strict_long_trend_mult,
        "disable_wide_range_longs": args.disable_wide_range_longs,
        "range_breakout_buffer_atr": args.range_breakout_buffer_atr,
        "range_structure_filter": not args.no_range_structure_filter,
        "range_structure_lookback": args.range_structure_lookback,
        "disabled_entry_regimes": args.disabled_entry_regimes,
        "entry_hours_utc": args.entry_hours_utc,
        "long_enabled_regimes": args.long_enabled_regimes,
        "short_enabled_regimes": args.short_enabled_regimes,
        "long_entry_hours_utc": args.long_entry_hours_utc,
        "short_entry_hours_utc": args.short_entry_hours_utc,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single Market Regime Adaptive FX backtest.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-type", default="EUR/USD.SIM-15-MINUTE-MID-EXTERNAL")
    parser.add_argument("--start", default="2024-10-01T00:00:00Z")
    parser.add_argument("--end", default="2024-11-01T00:00:00Z")
    parser.add_argument("--include-quote-ticks", action="store_true")
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--log-level", default="ERROR")
    add_strategy_args(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_regime_backtest(
        catalog=args.catalog,
        reports_root=args.reports_root,
        instrument_id=args.instrument_id,
        bar_type=args.bar_type,
        start=args.start,
        end=args.end,
        params=params_from_args(args),
        trade_size=args.trade_size,
        starting_balance=args.starting_balance,
        chunk_size=args.chunk_size,
        log_level=args.log_level,
        report_prefix="single_backtest",
        write_reports=True,
        include_quote_ticks=args.include_quote_ticks,
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
    for side in ["long", "short"]:
        side_metrics = summary["side_metrics"][side]
        print(
            f"{side}_metrics:   trades={side_metrics['trades']} "
            f"pnl={side_metrics['pnl_usd']} "
            f"win_rate={side_metrics['win_rate']} "
            f"pf={side_metrics['profit_factor']} "
            f"max_dd={side_metrics['max_drawdown_usd']}"
        )


if __name__ == "__main__":
    main()
