from __future__ import annotations

import argparse
import sys
from collections import deque
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nautilus_trader.persistence.catalog import ParquetDataCatalog


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
from fx_factor.strategies.regime_adaptive_fx import REGIME_NARROW_BEAR  # noqa: E402
from fx_factor.strategies.regime_adaptive_fx import REGIME_NARROW_BULL  # noqa: E402
from fx_factor.strategies.regime_adaptive_fx import REGIME_WIDE_BEAR  # noqa: E402
from fx_factor.strategies.regime_adaptive_fx import REGIME_WIDE_BULL  # noqa: E402
from fx_factor.strategies.regime_adaptive_fx import REGIME_WIDE_RANGE  # noqa: E402


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attribute regime-adaptive FX trades by regime and time buckets.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--start", default="2023-01-01T00:00:00Z")
    parser.add_argument("--end", default="2024-01-01T00:00:00Z")
    parser.add_argument("--trade-size", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--starting-balance", default="1000000 USD")
    parser.add_argument("--positions-csv", type=Path, default=None)
    parser.add_argument("--params-csv", type=Path, default=None)
    parser.add_argument("--params-row", type=int, default=0)
    parser.add_argument("--disable-default-filters", action="store_true")
    parser.add_argument("--no-run-backtest", action="store_true")
    return parser.parse_args()


def path_slug(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace("T", "_")
        .replace("Z", "")
        .replace("+", "")
    )


def unique_output_dir(root: Path, start: str, end: str) -> Path:
    base = root / f"attribution_{path_slug(start)}_{path_slug(end)}_{utc_stamp()}"
    if not base.exists():
        return base
    for suffix in range(1, 1000):
        candidate = Path(f"{base}_{suffix:03d}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate a unique output directory under {root}")


def load_params(args: argparse.Namespace) -> dict[str, Any]:
    params = default_params()
    if args.disable_default_filters:
        params["disabled_entry_regimes"] = ""
        params["entry_hours_utc"] = ""
    if args.params_csv is None:
        return params

    table = pd.read_csv(args.params_csv, keep_default_na=False)
    row = table.iloc[args.params_row].to_dict()
    for key, current in params.items():
        if key not in row or pd.isna(row[key]):
            continue
        value = row[key]
        if isinstance(current, bool):
            params[key] = parse_bool(value)
        elif isinstance(current, int):
            params[key] = int(value)
        elif isinstance(current, float):
            params[key] = float(value)
        else:
            params[key] = value
    return params


def classify_regime(params: dict[str, Any], trend_slope: float, width_score: float) -> str | None:
    if trend_slope > params["bull_threshold"]:
        if width_score <= params["narrow_width_threshold"]:
            return REGIME_NARROW_BULL
        return REGIME_WIDE_BULL
    if abs(trend_slope) <= params["flat_threshold"] and width_score >= params["wide_range_threshold"]:
        return REGIME_WIDE_RANGE
    if trend_slope < -params["bear_threshold"]:
        if width_score <= params["narrow_width_threshold"]:
            return REGIME_NARROW_BEAR
        return REGIME_WIDE_BEAR
    return None


def linear_regression_slope(values: list[float]) -> float:
    y = np.asarray(values, dtype=np.float64)
    x = np.arange(len(y), dtype=np.float64)
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    denom = float(np.sum((x - x_mean) ** 2))
    if denom <= 0.0:
        return 0.0
    return float(np.sum((x - x_mean) * (y - y_mean)) / denom)


def factors_for(params: dict[str, Any], highs: deque[float], lows: deque[float], closes: deque[float], true_ranges: deque[float]) -> dict[str, float]:
    channel_highs = list(highs)[-params["channel_lookback"] :]
    channel_lows = list(lows)[-params["channel_lookback"] :]
    close_values = list(closes)
    close = close_values[-1]
    rolling_high = max(channel_highs)
    rolling_low = min(channel_lows)
    channel_width = rolling_high - rolling_low
    atr = float(np.mean(np.asarray(true_ranges, dtype=np.float64)))
    width_score = channel_width / max(atr, 1e-12)
    range_pos = (close - rolling_low) / max(channel_width, 1e-12)
    trend_slope = linear_regression_slope(close_values[-params["trend_lookback"] :]) / max(atr, 1e-12)
    momentum = close / close_values[-params["momentum_lookback"] - 1] - 1.0
    short_ma = float(np.mean(np.asarray(close_values[-params["short_ma_lookback"] :], dtype=np.float64)))
    prev_high = max(list(highs)[-params["breakout_lookback"] - 1 : -1])
    prev_low = min(list(lows)[-params["breakout_lookback"] - 1 : -1])
    return {
        "close": close,
        "rolling_high": rolling_high,
        "rolling_low": rolling_low,
        "channel_width": channel_width,
        "atr": atr,
        "width_score": width_score,
        "range_pos": range_pos,
        "trend_slope": trend_slope,
        "momentum": momentum,
        "short_ma": short_ma,
        "prev_high": prev_high,
        "prev_low": prev_low,
    }


def has_warmup(params: dict[str, Any], closes: deque[float], true_ranges: deque[float]) -> bool:
    return (
        len(closes) >= params["channel_lookback"]
        and len(closes) >= params["trend_lookback"]
        and len(closes) > params["momentum_lookback"]
        and len(closes) > params["breakout_lookback"]
        and len(true_ranges) >= params["atr_lookback"]
    )


def load_bars(catalog: Path, bar_type: str, start: str, end: str) -> pd.DataFrame:
    data_catalog = ParquetDataCatalog(path=catalog.resolve().as_posix())
    bars = data_catalog.bars(bar_types=[bar_type], start=start, end=end)
    rows = [
        {
            "timestamp": pd.Timestamp(bar.ts_event, unit="ns", tz="UTC"),
            "open": bar.open.as_double(),
            "high": bar.high.as_double(),
            "low": bar.low.as_double(),
            "close": bar.close.as_double(),
            "volume": bar.volume.as_double(),
        }
        for bar in bars
    ]
    if not rows:
        raise RuntimeError(f"No bars found for {bar_type} between {start} and {end}")
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def reconstruct_regimes(bars: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    max_window = max(
        params["channel_lookback"],
        params["atr_lookback"] + 1,
        params["trend_lookback"],
        params["momentum_lookback"] + 1,
        params["breakout_lookback"] + 1,
        params["short_ma_lookback"],
    )
    highs: deque[float] = deque(maxlen=max_window)
    lows: deque[float] = deque(maxlen=max_window)
    closes: deque[float] = deque(maxlen=max_window)
    true_ranges: deque[float] = deque(maxlen=params["atr_lookback"])
    pending_regime: str | None = None
    pending_regime_count = 0
    confirmed_regime: str | None = None
    confirmed_regime_age = 0
    rows: list[dict[str, Any]] = []

    for bar in bars.itertuples(index=False):
        prev_close = closes[-1] if closes else float(bar.close)
        highs.append(float(bar.high))
        lows.append(float(bar.low))
        closes.append(float(bar.close))
        true_ranges.append(max(float(bar.high) - float(bar.low), abs(float(bar.high) - prev_close), abs(float(bar.low) - prev_close)))

        if not has_warmup(params, closes, true_ranges):
            rows.append({"timestamp": bar.timestamp, "raw_regime": None, "confirmed_regime": None, "regime_changed": False})
            continue

        factors = factors_for(params, highs, lows, closes, true_ranges)
        raw_regime = classify_regime(params, factors["trend_slope"], factors["width_score"])
        regime_changed = False
        if raw_regime is None:
            pending_regime = None
            pending_regime_count = 0
        else:
            if raw_regime == pending_regime:
                pending_regime_count += 1
            else:
                pending_regime = raw_regime
                pending_regime_count = 1

            if pending_regime_count >= params["confirm_bars"] and raw_regime != confirmed_regime:
                confirmed_regime = raw_regime
                confirmed_regime_age = 0
                regime_changed = True
            elif raw_regime == confirmed_regime:
                confirmed_regime_age += 1

        rows.append(
            {
                "timestamp": bar.timestamp,
                "raw_regime": raw_regime,
                "confirmed_regime": confirmed_regime,
                "confirmed_regime_age": confirmed_regime_age,
                "regime_changed": regime_changed,
                **factors,
            },
        )

    return pd.DataFrame(rows)


def pnl_to_float(value: object) -> float:
    text = str(value).replace("USD", "").strip()
    return float(text) if text else 0.0


def session_for_hour(hour: int) -> str:
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london_morning"
    if 12 <= hour < 16:
        return "london_ny_overlap"
    if 16 <= hour < 21:
        return "ny_afternoon"
    return "rollover_late_us"


def load_positions(path: Path) -> pd.DataFrame:
    positions = pd.read_csv(path)
    if positions.empty:
        return positions
    positions["ts_opened"] = pd.to_datetime(positions["ts_opened"], utc=True)
    positions["ts_closed"] = pd.to_datetime(positions["ts_closed"], utc=True)
    positions["pnl_usd"] = positions["realized_pnl"].map(pnl_to_float)
    positions["duration_hours"] = positions["duration_ns"].astype(float) / 3_600_000_000_000.0
    positions["entry_hour_utc"] = positions["ts_opened"].dt.hour
    positions["entry_weekday"] = positions["ts_opened"].dt.day_name()
    positions["entry_month"] = positions["ts_opened"].dt.to_period("M").astype(str)
    positions["entry_session"] = positions["entry_hour_utc"].map(session_for_hour)
    positions["duration_bucket"] = pd.cut(
        positions["duration_hours"],
        bins=[-0.01, 0.5, 1.5, 3.0, 6.0, 12.0, 1_000.0],
        labels=["<=0.5h", "0.5-1.5h", "1.5-3h", "3-6h", "6-12h", ">12h"],
    )
    return positions


def summarize(grouped: pd.core.groupby.DataFrameGroupBy) -> pd.DataFrame:
    summary = grouped["pnl_usd"].agg(
        trades="count",
        pnl_usd="sum",
        avg_pnl_usd="mean",
        median_pnl_usd="median",
        win_rate=lambda values: float((values > 0.0).mean()),
        avg_duration_hours=lambda values: float(grouped.obj.loc[values.index, "duration_hours"].mean()),
    )
    winners = grouped["pnl_usd"].apply(lambda values: float(values[values > 0.0].sum()))
    losers = grouped["pnl_usd"].apply(lambda values: float(-values[values < 0.0].sum()))
    summary["profit_factor"] = winners / losers.replace(0.0, np.nan)
    return summary.reset_index().sort_values(["pnl_usd", "trades"], ascending=[True, False])


def write_summary(name: str, trades: pd.DataFrame, columns: list[str], out_dir: Path) -> None:
    if trades.empty:
        (out_dir / f"{name}.csv").write_text("", encoding="utf-8")
        return
    table = summarize(trades.groupby(columns, dropna=False))
    table.to_csv(out_dir / f"{name}.csv", index=False)


def add_regime_distribution(regimes: pd.DataFrame, out_dir: Path) -> None:
    frame = regimes.copy()
    frame["hour_utc"] = frame["timestamp"].dt.hour
    frame["session"] = frame["hour_utc"].map(session_for_hour)
    frame["month"] = frame["timestamp"].dt.to_period("M").astype(str)
    frame["weekday"] = frame["timestamp"].dt.day_name()
    for name, columns in {
        "bar_regime_distribution": ["confirmed_regime"],
        "bar_regime_by_session": ["confirmed_regime", "session"],
        "bar_regime_by_month": ["confirmed_regime", "month"],
    }.items():
        table = frame.groupby(columns, dropna=False).size().reset_index(name="bars")
        table["bar_pct"] = table["bars"] / len(frame)
        table.to_csv(out_dir / f"{name}.csv", index=False)


def run_or_load_positions(args: argparse.Namespace, params: dict[str, Any], out_dir: Path, bar_type: str) -> Path:
    if args.positions_csv is not None:
        return args.positions_csv
    if args.no_run_backtest:
        raise RuntimeError("--no-run-backtest requires --positions-csv")

    summary = run_regime_backtest(
        catalog=args.catalog,
        reports_root=args.reports_root,
        instrument_id=args.instrument_id,
        bar_type=bar_type,
        start=args.start,
        end=args.end,
        params=params,
        trade_size=args.trade_size,
        starting_balance=args.starting_balance,
        log_level="OFF",
        report_prefix=f"attribution_backtest_{path_slug(args.start)}_{path_slug(args.end)}",
        write_reports=True,
    )
    source = Path(summary["reports"]["positions"])
    target = out_dir / "positions.csv"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def main() -> None:
    args = parse_args()
    out_root = args.reports_root.resolve() / REGIME_STRATEGY_ID
    out_dir = unique_output_dir(out_root, args.start, args.end)
    out_dir.mkdir(parents=True, exist_ok=True)
    params = load_params(args)
    bar_type = bar_type_for(args.instrument_id, args.bar_minutes, "MID")

    positions_path = run_or_load_positions(args, params, out_dir, bar_type)
    bars = load_bars(args.catalog, bar_type, args.start, args.end)
    regimes = reconstruct_regimes(bars, params)
    positions = load_positions(positions_path)

    enriched = positions.merge(
        regimes,
        left_on="ts_opened",
        right_on="timestamp",
        how="left",
        validate="many_to_one",
    )
    enriched["trade_regime"] = enriched["confirmed_regime"].fillna("unknown")
    enriched["entry_side"] = enriched["entry"].str.lower()
    enriched["result"] = np.where(enriched["pnl_usd"] > 0.0, "win", "loss")
    enriched.to_csv(out_dir / "trades_enriched.csv", index=False)
    regimes.to_csv(out_dir / "regime_timeline.csv", index=False)
    add_regime_distribution(regimes, out_dir)

    write_summary("by_regime", enriched, ["trade_regime"], out_dir)
    write_summary("by_side", enriched, ["entry_side"], out_dir)
    write_summary("by_regime_side", enriched, ["trade_regime", "entry_side"], out_dir)
    write_summary("by_session", enriched, ["entry_session"], out_dir)
    write_summary("by_hour", enriched, ["entry_hour_utc"], out_dir)
    write_summary("by_weekday", enriched, ["entry_weekday"], out_dir)
    write_summary("by_month", enriched, ["entry_month"], out_dir)
    write_summary("by_duration_bucket", enriched, ["duration_bucket"], out_dir)
    write_summary("by_regime_session", enriched, ["trade_regime", "entry_session"], out_dir)

    overview = {
        "reports_dir": out_dir.as_posix(),
        "positions_csv": positions_path.as_posix(),
        "trades": int(len(enriched)),
        "pnl_usd": float(enriched["pnl_usd"].sum()) if not enriched.empty else 0.0,
        "win_rate": float((enriched["pnl_usd"] > 0.0).mean()) if not enriched.empty else 0.0,
        "start": args.start,
        "end": args.end,
        "bar_type": bar_type,
        "params": params,
    }
    pd.Series(overview, dtype="object").to_json(out_dir / "overview.json", indent=2)

    print(f"results: {out_dir}")
    if not enriched.empty:
        print(f"trades:  {len(enriched)}")
        print(f"pnl_usd: {enriched['pnl_usd'].sum():.2f}")
        print(f"winrate: {(enriched['pnl_usd'] > 0.0).mean():.4f}")
        print()
        print("by_regime")
        print(pd.read_csv(out_dir / "by_regime.csv").to_string(index=False))
        print()
        print("by_session")
        print(pd.read_csv(out_dir / "by_session.csv").to_string(index=False))


if __name__ == "__main__":
    main()
