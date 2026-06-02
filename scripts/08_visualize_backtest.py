from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "reports"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.regime_backtest_runner import REGIME_STRATEGY_ID  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Lightweight Charts trade visualization for a Nautilus backtest report.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Backtest report directory containing summary.json and fills.csv. Defaults to latest report.",
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=DEFAULT_REPORTS_ROOT,
        help="Reports root used with --latest.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output HTML path. Defaults to <report-dir>/bars_with_fills_lwc.html.",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Initial visible range in months.",
    )
    return parser.parse_args()


def latest_report_dir(reports_root: Path) -> Path:
    strategy_root = reports_root / REGIME_STRATEGY_ID
    candidates = [
        path
        for path in strategy_root.glob("*")
        if path.is_dir() and (path / "summary.json").exists() and (path / "fills.csv").exists()
    ]
    if not candidates:
        raise RuntimeError(f"No backtest reports found under {strategy_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_summary(report_dir: Path) -> dict[str, Any]:
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary.json: {summary_path}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def bars_frame_from_catalog(catalog: Path, bar_type: str, start: str, end: str) -> pd.DataFrame:
    data_catalog = ParquetDataCatalog(str(catalog.resolve()))
    bars = data_catalog.bars(bar_types=[bar_type], start=start, end=end)
    if not bars:
        raise RuntimeError(f"No bars found in catalog for {bar_type} between {start} and {end}")

    frame = pd.DataFrame(Bar.to_dict(bar) for bar in bars)
    frame["ts_init"] = pd.to_datetime(frame["ts_init"], utc=True)
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).sort_values("ts_init")


def aggregate_fills(fills: pd.DataFrame, instrument_id: str) -> pd.DataFrame:
    if fills.empty:
        return pd.DataFrame(columns=["ts_init", "order_side", "last_qty", "last_px"])

    frame = fills[fills["instrument_id"] == instrument_id].copy()
    if frame.empty:
        return pd.DataFrame(columns=["ts_init", "order_side", "last_qty", "last_px"])

    frame["ts_init"] = pd.to_datetime(frame["ts_init"], utc=True)
    frame["last_qty"] = pd.to_numeric(frame["last_qty"], errors="coerce")
    frame["last_px"] = pd.to_numeric(frame["last_px"], errors="coerce")
    frame = frame.dropna(subset=["ts_init", "last_qty", "last_px", "order_side"])
    if frame.empty:
        return pd.DataFrame(columns=["ts_init", "order_side", "last_qty", "last_px"])

    def weighted_fill(group: pd.DataFrame) -> pd.Series:
        quantity = float(group["last_qty"].sum())
        if quantity <= 0.0:
            price = float(group["last_px"].mean())
        else:
            price = float((group["last_px"] * group["last_qty"]).sum() / quantity)
        return pd.Series({"last_qty": quantity, "last_px": price})

    return (
        frame.groupby(["ts_init", "order_side"], as_index=False, dropna=False)
        .apply(weighted_fill, include_groups=False)
        .sort_values("ts_init")
    )


def lightweight_charts_bundle() -> str:
    bundle_path = (
        PROJECT_ROOT
        / "node_modules"
        / "lightweight-charts"
        / "dist"
        / "lightweight-charts.standalone.production.js"
    )
    if not bundle_path.exists():
        raise RuntimeError("Lightweight Charts bundle not found. Run `npm install` from the project root.")
    return bundle_path.read_text(encoding="utf-8")


def render_html(
    *,
    bar_type: str,
    candle_data: list[dict[str, float | int]],
    marker_data: list[dict[str, float | int | str]],
    stats: dict[str, str | int],
    initial_months: int,
) -> str:
    bundle = lightweight_charts_bundle()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{bar_type} - Lightweight Bars with Fills</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #14213d;
      --muted: #687387;
      --line: #d8deea;
      --button: #eef2f8;
      --button-hover: #dfe7f3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .meta {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    button {{
      min-width: 44px;
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--button);
      color: var(--ink);
      cursor: pointer;
      font-size: 13px;
    }}
    button:hover {{ background: var(--button-hover); }}
    #chart {{
      width: 100vw;
      height: calc(100vh - 70px);
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{bar_type} - Lightweight Bars with Fills</h1>
      <div class="meta">{stats["bar_count"]:,} bars, {stats["fill_count"]:,} fills | {stats["start"]} to {stats["end"]}</div>
    </div>
    <div class="toolbar">
      <button id="range-3m" type="button">3M</button>
      <button id="range-6m" type="button">6M</button>
      <button id="range-1y" type="button">1Y</button>
      <button id="range-all" type="button">All</button>
    </div>
  </header>
  <main id="chart"></main>
  <script>
{bundle}
  </script>
  <script>
    const candleData = {json.dumps(candle_data, separators=(",", ":"))};
    const markerData = {json.dumps(marker_data, separators=(",", ":"))};

    const container = document.getElementById("chart");
    const chart = LightweightCharts.createChart(container, {{
      autoSize: true,
      layout: {{
        background: {{ type: "solid", color: "#ffffff" }},
        textColor: "#14213d",
        fontFamily: "Arial, Helvetica, sans-serif",
      }},
      grid: {{
        vertLines: {{ color: "#edf1f7" }},
        horzLines: {{ color: "#edf1f7" }},
      }},
      rightPriceScale: {{
        borderColor: "#d8deea",
        scaleMargins: {{ top: 0.08, bottom: 0.08 }},
      }},
      timeScale: {{
        borderColor: "#d8deea",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 6,
      }},
      crosshair: {{
        mode: LightweightCharts.CrosshairMode.Normal,
      }},
      localization: {{
        priceFormatter: price => price.toFixed(5),
      }},
    }});

    const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {{
      upColor: "#178f45",
      downColor: "#c83232",
      borderUpColor: "#178f45",
      borderDownColor: "#c83232",
      wickUpColor: "#178f45",
      wickDownColor: "#c83232",
      priceFormat: {{ type: "price", precision: 5, minMove: 0.00001 }},
    }});
    candleSeries.setData(candleData);
    LightweightCharts.createSeriesMarkers(candleSeries, markerData, {{ zOrder: "top" }});

    function visibleRange(months) {{
      if (!candleData.length) return;
      const last = candleData[candleData.length - 1].time;
      const first = Math.max(candleData[0].time, last - months * 30 * 24 * 60 * 60);
      chart.timeScale().setVisibleRange({{ from: first, to: last }});
    }}

    document.getElementById("range-3m").addEventListener("click", () => visibleRange(3));
    document.getElementById("range-6m").addEventListener("click", () => visibleRange(6));
    document.getElementById("range-1y").addEventListener("click", () => visibleRange(12));
    document.getElementById("range-all").addEventListener("click", () => chart.timeScale().fitContent());

    visibleRange({initial_months});
  </script>
</body>
</html>
"""


def write_lightweight_chart(
    *,
    report_dir: Path,
    output_path: Path | None,
    initial_months: int,
) -> Path:
    summary = load_summary(report_dir)
    config = summary["config"]
    catalog = Path(config["catalog"])
    instrument_id = config["instrument_id"]
    bar_type = config["bar_type"]
    start = config["start"]
    end = config["end"]

    bars = bars_frame_from_catalog(catalog, bar_type, start, end)
    fills = pd.read_csv(report_dir / "fills.csv")
    fill_points = aggregate_fills(fills, instrument_id)

    candle_data = [
        {
            "time": int(row.ts_init.timestamp()),
            "open": round(float(row.open), 5),
            "high": round(float(row.high), 5),
            "low": round(float(row.low), 5),
            "close": round(float(row.close), 5),
        }
        for row in bars.itertuples(index=False)
    ]
    marker_data = [
        {
            "time": int(row.ts_init.timestamp()),
            "position": "atPriceMiddle",
            "price": round(float(row.last_px), 5),
            "color": "#178f45" if str(row.order_side).upper() == "BUY" else "#c83232",
            "shape": "arrowUp" if str(row.order_side).upper() == "BUY" else "arrowDown",
            "text": f"{str(row.order_side).upper()} {float(row.last_px):.5f}",
            "size": 1.4,
        }
        for row in fill_points.itertuples(index=False)
    ]
    stats = {
        "bar_count": len(candle_data),
        "fill_count": len(marker_data),
        "start": str(bars["ts_init"].min()),
        "end": str(bars["ts_init"].max()),
    }

    target = output_path or report_dir / "bars_with_fills_lwc.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_html(
            bar_type=bar_type,
            candle_data=candle_data,
            marker_data=marker_data,
            stats=stats,
            initial_months=initial_months,
        ),
        encoding="utf-8",
    )
    return target


def main() -> None:
    args = parse_args()
    report_dir = args.report_dir or latest_report_dir(args.reports_root)
    output_path = write_lightweight_chart(
        report_dir=report_dir.resolve(),
        output_path=args.output,
        initial_months=args.months,
    )
    print(f"report: {report_dir}")
    print(f"chart:  {output_path}")


if __name__ == "__main__":
    main()
