from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from nautilus_trader.persistence.catalog import ParquetDataCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"


@dataclass
class MatchStats:
    offset_minutes: int
    matched_rows: int
    mt5_rows: int
    nautilus_rows: int
    close_mae: float | None
    close_max_abs: float | None
    z_mae: float | None
    z_max_abs: float | None
    z_corr: float | None
    raw_entry_match_rate: float | None
    state_signal_match_rate: float | None
    state_non_hold_match_rate: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Align MT5 EA signal logs with Nautilus catalog signals.")
    parser.add_argument("--mt5-signal-log", type=Path, required=True)
    parser.add_argument("--mt5-order-log", type=Path)
    parser.add_argument("--mt5-error-log", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--bar-type", default="EUR/USD.SIM-15-MINUTE-BID-EXTERNAL")
    parser.add_argument("--start", default="2023-01-01T00:00:00Z")
    parser.add_argument("--end", default="2026-01-01T00:00:00Z")
    parser.add_argument("--lookback", type=int, default=96)
    parser.add_argument("--entry-z", type=float, default=1.5)
    parser.add_argument("--exit-z", type=float, default=0.2)
    parser.add_argument("--stop-z", type=float, default=0.0)
    parser.add_argument("--max-position-bars", type=int, default=0)
    parser.add_argument("--bar-time-mode", choices=["end", "open"], default="end")
    return parser.parse_args()


def read_mt5_signal(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y.%m.%d %H:%M:%S", utc=True)
    for col in ["close", "mean", "std", "z_score", "entry_z", "exit_z", "stop_z"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Re-running the same Strategy Tester date range can reuse the same file name. Keep
    # the last row for each bar timestamp so an accidentally appended prior run does not
    # pollute the alignment.
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["raw_entry_zone"] = np.select(
        [df["z_score"] < -df["entry_z"], df["z_score"] > df["entry_z"]],
        ["LONG_ZONE", "SHORT_ZONE"],
        default="NONE",
    )
    return df


def read_optional_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_nautilus_bars(args: argparse.Namespace) -> pd.DataFrame:
    catalog = ParquetDataCatalog(path=args.catalog.resolve().as_posix())
    # Pull extra history before the requested start when available so the rolling window can
    # warm up like MT5, then filter after indicator calculation.
    start = pd.Timestamp(args.start)
    warmup_start = (start - pd.Timedelta(days=7)).isoformat().replace("+00:00", "Z")
    bars = catalog.bars([args.bar_type], start=warmup_start, end=args.end)
    if not bars:
        raise RuntimeError(f"No bars found for {args.bar_type} from {warmup_start} to {args.end}")

    rows = []
    for bar in bars:
        ts = pd.to_datetime(bar.ts_event, unit="ns", utc=True)
        if args.bar_time_mode == "open":
            ts = ts - pd.Timedelta(minutes=15)
        rows.append(
            {
                "timestamp": ts,
                "close": bar.close.as_double(),
            }
        )
    df = pd.DataFrame(rows).drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["mean"] = df["close"].rolling(args.lookback).mean()
    df["std"] = df["close"].rolling(args.lookback).std(ddof=0)
    df["z_score"] = (df["close"] - df["mean"]) / df["std"]
    df = df.dropna(subset=["z_score"]).reset_index(drop=True)
    df = df[(df["timestamp"] >= pd.Timestamp(args.start)) & (df["timestamp"] < pd.Timestamp(args.end))]
    df["raw_entry_zone"] = np.select(
        [df["z_score"] < -args.entry_z, df["z_score"] > args.entry_z],
        ["LONG_ZONE", "SHORT_ZONE"],
        default="NONE",
    )
    df["state_signal"] = simulate_state_signals(df, args)
    return df.reset_index(drop=True)


def simulate_state_signals(df: pd.DataFrame, args: argparse.Namespace) -> list[str]:
    signals: list[str] = []
    position = 0
    position_bars = 0

    for z_score in df["z_score"]:
        signal = "HOLD"
        if position == 0:
            position_bars = 0
            if z_score < -args.entry_z:
                signal = "ENTER_LONG"
                position = 1
            elif z_score > args.entry_z:
                signal = "ENTER_SHORT"
                position = -1
        elif position > 0:
            position_bars += 1
            if z_score > -args.exit_z:
                signal = "EXIT_LONG_MEAN_REVERT"
                position = 0
                position_bars = 0
            elif args.stop_z > 0.0 and z_score < -args.stop_z:
                signal = "EXIT_LONG_STOP_Z"
                position = 0
                position_bars = 0
            elif args.max_position_bars > 0 and position_bars >= args.max_position_bars:
                signal = "EXIT_LONG_MAX_BARS"
                position = 0
                position_bars = 0
        elif position < 0:
            position_bars += 1
            if z_score < args.exit_z:
                signal = "EXIT_SHORT_MEAN_REVERT"
                position = 0
                position_bars = 0
            elif args.stop_z > 0.0 and z_score > args.stop_z:
                signal = "EXIT_SHORT_STOP_Z"
                position = 0
                position_bars = 0
            elif args.max_position_bars > 0 and position_bars >= args.max_position_bars:
                signal = "EXIT_SHORT_MAX_BARS"
                position = 0
                position_bars = 0
        signals.append(signal)

    return signals


def score_offset(mt5: pd.DataFrame, nt: pd.DataFrame, offset_minutes: int) -> tuple[MatchStats, pd.DataFrame]:
    shifted = mt5.copy()
    shifted["join_timestamp"] = shifted["timestamp"] + pd.Timedelta(minutes=offset_minutes)
    merged = shifted.merge(
        nt,
        left_on="join_timestamp",
        right_on="timestamp",
        how="inner",
        suffixes=("_mt5", "_nt"),
    )

    if merged.empty:
        return (
            MatchStats(offset_minutes, 0, len(mt5), len(nt), None, None, None, None, None, None, None, None),
            merged,
        )

    close_diff = merged["close_mt5"] - merged["close_nt"]
    z_diff = merged["z_score_mt5"] - merged["z_score_nt"]
    z_corr = merged[["z_score_mt5", "z_score_nt"]].corr().iloc[0, 1]
    raw_match = (merged["raw_entry_zone_mt5"] == merged["raw_entry_zone_nt"]).mean()
    state_match = (merged["signal"] == merged["state_signal"]).mean()
    non_hold = merged[(merged["signal"] != "HOLD") | (merged["state_signal"] != "HOLD")]
    non_hold_match = None if non_hold.empty else float((non_hold["signal"] == non_hold["state_signal"]).mean())
    return (
        MatchStats(
            offset_minutes=offset_minutes,
            matched_rows=len(merged),
            mt5_rows=len(mt5),
            nautilus_rows=len(nt),
            close_mae=float(close_diff.abs().mean()),
            close_max_abs=float(close_diff.abs().max()),
            z_mae=float(z_diff.abs().mean()),
            z_max_abs=float(z_diff.abs().max()),
            z_corr=float(z_corr) if not np.isnan(z_corr) else None,
            raw_entry_match_rate=float(raw_match),
            state_signal_match_rate=float(state_match),
            state_non_hold_match_rate=non_hold_match,
        ),
        merged,
    )


def best_alignment(mt5: pd.DataFrame, nt: pd.DataFrame) -> tuple[MatchStats, pd.DataFrame, list[MatchStats]]:
    candidates: list[MatchStats] = []
    merged_by_offset: dict[int, pd.DataFrame] = {}
    for offset in range(-180, 181, 15):
        stats, merged = score_offset(mt5, nt, offset)
        candidates.append(stats)
        merged_by_offset[offset] = merged

    def key(stats: MatchStats) -> tuple[int, float, float]:
        return (
            stats.matched_rows,
            stats.z_corr if stats.z_corr is not None else -999.0,
            -(stats.z_mae if stats.z_mae is not None else 999.0),
        )

    best = max(candidates, key=key)
    return best, merged_by_offset[best.offset_minutes], candidates


def summarize_orders(order_log: pd.DataFrame, error_log: pd.DataFrame) -> dict[str, object]:
    summary: dict[str, object] = {}
    if not order_log.empty:
        dedup_cols = [col for col in ["timestamp", "action", "direction", "ticket", "comment"] if col in order_log]
        if dedup_cols:
            order_log = order_log.drop_duplicates(subset=dedup_cols, keep="last")
        summary["orders"] = len(order_log)
        summary["orders_by_action"] = dict(Counter(order_log.get("action", [])))
        summary["orders_by_comment"] = dict(Counter(order_log.get("comment", [])))
        summary["orders_failed"] = int((order_log.get("success", pd.Series(dtype=str)).astype(str) == "False").sum())
    if not error_log.empty:
        dedup_cols = [col for col in ["timestamp", "code", "message"] if col in error_log]
        if dedup_cols:
            error_log = error_log.drop_duplicates(subset=dedup_cols, keep="last")
        summary["errors"] = len(error_log)
        summary["errors_by_code"] = dict(Counter(error_log.get("code", [])))
    return summary


def main() -> None:
    args = parse_args()
    mt5 = read_mt5_signal(args.mt5_signal_log)
    nt = load_nautilus_bars(args)
    best, merged, candidates = best_alignment(mt5, nt)

    out_dir = args.out_dir
    if out_dir is None:
        out_dir = args.reports_root / f"align_mt5_nautilus_{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    order_log = read_optional_csv(args.mt5_order_log)
    error_log = read_optional_csv(args.mt5_error_log)
    order_summary = summarize_orders(order_log, error_log)

    merged["close_abs_diff"] = (merged["close_mt5"] - merged["close_nt"]).abs()
    merged["z_abs_diff"] = (merged["z_score_mt5"] - merged["z_score_nt"]).abs()
    merged["raw_entry_match"] = merged["raw_entry_zone_mt5"] == merged["raw_entry_zone_nt"]
    merged["state_signal_match"] = merged["signal"] == merged["state_signal"]
    worst = merged.sort_values(["z_abs_diff", "close_abs_diff"], ascending=False).head(50)
    raw_mismatches = merged[~merged["raw_entry_match"]].head(100)
    state_mismatches = merged[~merged["state_signal_match"]].head(200)

    summary = {
        "inputs": {
            "mt5_signal_log": str(args.mt5_signal_log),
            "mt5_order_log": str(args.mt5_order_log) if args.mt5_order_log else None,
            "mt5_error_log": str(args.mt5_error_log) if args.mt5_error_log else None,
            "catalog": str(args.catalog),
            "bar_type": args.bar_type,
            "start": args.start,
            "end": args.end,
            "lookback": args.lookback,
            "entry_z": args.entry_z,
            "exit_z": args.exit_z,
        },
        "best": asdict(best),
        "mt5_signal_counts": dict(Counter(mt5["signal"])),
        "mt5_raw_entry_zone_counts": dict(Counter(mt5["raw_entry_zone"])),
        "nautilus_raw_entry_zone_counts": dict(Counter(nt["raw_entry_zone"])),
        "nautilus_state_signal_counts": dict(Counter(nt["state_signal"])),
        "order_summary": order_summary,
        "offset_candidates": [asdict(item) for item in candidates],
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame([asdict(item) for item in candidates]).to_csv(out_dir / "offset_candidates.csv", index=False)
    merged.to_csv(out_dir / "matched_bars.csv", index=False)
    worst.to_csv(out_dir / "worst_z_diffs.csv", index=False)
    raw_mismatches.to_csv(out_dir / "raw_entry_zone_mismatches.csv", index=False)
    state_mismatches.to_csv(out_dir / "state_signal_mismatches.csv", index=False)

    print(f"reports:             {out_dir.resolve()}")
    print(f"best_offset_minutes: {best.offset_minutes}")
    print(f"matched_rows:        {best.matched_rows} / mt5={best.mt5_rows} nt={best.nautilus_rows}")
    print(f"close_mae:           {best.close_mae}")
    print(f"z_mae:               {best.z_mae}")
    print(f"z_corr:              {best.z_corr}")
    print(f"raw_entry_match:     {best.raw_entry_match_rate}")
    print(f"state_signal_match:  {best.state_signal_match_rate}")
    print(f"state_non_hold:      {best.state_non_hold_match_rate}")
    print(f"mt5_signal_counts:   {summary['mt5_signal_counts']}")
    print(f"nt_signal_counts:    {summary['nautilus_state_signal_counts']}")
    print(f"order_summary:       {order_summary}")


if __name__ == "__main__":
    main()
