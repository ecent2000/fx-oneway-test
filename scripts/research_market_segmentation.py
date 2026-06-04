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
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports" / "market-segmentation"
DEFAULT_BAR_TYPE = "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL"
DEFAULT_START = "2023-01-01T00:00:00Z"
DEFAULT_END = "2026-01-01T00:00:00Z"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.market_segmentation import DEFAULT_FEATURE_COLUMNS  # noqa: E402
from fx_factor.market_segmentation import MarketSegmentationParams  # noqa: E402
from fx_factor.market_segmentation import SegmentationMethod  # noqa: E402
from fx_factor.market_segmentation import compute_all_market_segmentations  # noqa: E402
from fx_factor.market_segmentation_eval import SegmentationEvalParams  # noqa: E402
from fx_factor.market_segmentation_eval import evaluate_market_segmentations  # noqa: E402
from fx_factor.market_segmentation_eval import segment_summary  # noqa: E402


METHOD_COLORS = {
    "pelt": "#235789",
    "binseg": "#8f3f71",
    "cusum": "#c46d1d",
    "zigzag": "#2f7d55",
    "hmm": "#6f5bb8",
}
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research offline market-structure segmentation methods.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--bar-type", default=DEFAULT_BAR_TYPE)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--output-stem", default="market_segmentation_2023_2025")
    parser.add_argument("--methods", nargs="+", default=["pelt", "binseg", "cusum", "zigzag", "hmm"])
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--min-segment-bars", type=int, default=12)
    parser.add_argument("--pelt-penalty", type=float, default=6.0)
    return parser.parse_args()


def load_bars(catalog: Path, bar_type: str, start: str, end: str) -> pd.DataFrame:
    data_catalog = ParquetDataCatalog(str(catalog.resolve()))
    bars = data_catalog.bars(bar_types=[bar_type], start=start, end=end)
    if not bars:
        raise RuntimeError(f"No bars found for {bar_type} between {start} and {end}")

    frame = pd.DataFrame(Bar.to_dict(bar) for bar in bars)
    frame["timestamp"] = pd.to_datetime(frame["ts_init"], utc=True)
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.loc[:, ["timestamp", "open", "high", "low", "close"]]
        .dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .reset_index(drop=True)
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
    lines = bundle_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if "sourceMappingURL" not in line)


def time_seconds(timestamp: pd.Timestamp) -> int:
    return int(pd.Timestamp(timestamp).timestamp())


def candle_data(frame: pd.DataFrame) -> list[dict[str, float | int]]:
    return [
        {
            "time": time_seconds(row.timestamp),
            "open": round(float(row.open), 5),
            "high": round(float(row.high), 5),
            "low": round(float(row.low), 5),
            "close": round(float(row.close), 5),
        }
        for row in frame.itertuples(index=False)
    ]


def boundary_markers(frame: pd.DataFrame, method: str) -> list[dict[str, Any]]:
    color = METHOD_COLORS.get(method, "#235789")
    markers: list[dict[str, Any]] = []
    boundaries = frame.loc[frame["is_boundary"].fillna(False), ["timestamp", "boundary_score", "segment_id"]]
    for row in boundaries.itertuples(index=False):
        markers.append(
            {
                "time": time_seconds(row.timestamp),
                "position": "aboveBar",
                "color": color,
                "shape": "circle",
                "text": f"S{int(row.segment_id)} {float(row.boundary_score):.2f}",
            },
        )
    return markers


def segmented_candle_data(frame: pd.DataFrame) -> list[dict[str, float | int | str]]:
    palette = [
        ("#1f7a57", "#176b4b"),
        ("#2c6da3", "#235b8d"),
        ("#b56b25", "#9e5b1f"),
        ("#7a579c", "#684986"),
        ("#60717c", "#53636d"),
        ("#a34d54", "#8f4148"),
    ]
    candles: list[dict[str, float | int | str]] = []
    for row in frame.itertuples(index=False):
        segment_id = int(getattr(row, "segment_id"))
        fill, border = palette[(segment_id - 1) % len(palette)]
        candles.append(
            {
                "time": time_seconds(row.timestamp),
                "open": round(float(row.open), 5),
                "high": round(float(row.high), 5),
                "low": round(float(row.low), 5),
                "close": round(float(row.close), 5),
                "color": fill,
                "borderColor": border,
                "wickColor": border,
            },
        )
    return candles


def render_method_html(
    *,
    title: str,
    method: str,
    segmentation: pd.DataFrame,
    metrics: dict[str, Any],
    initial_months: int,
) -> str:
    bundle = lightweight_charts_bundle()
    candles = segmented_candle_data(segmentation)
    markers = boundary_markers(segmentation, method)
    summary_rows = segment_summary(segmentation).head(120).to_dict(orient="records")
    payload = {
        "candles": candles,
        "markers": markers,
        "metrics": metrics,
        "summaryRows": summary_rows,
    }
    color = METHOD_COLORS.get(method, "#235789")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #17202e;
      --muted: #667282;
      --line: #d9e0e8;
      --accent: {color};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
    }}
    header {{
      min-height: 82px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 16px;
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
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }}
    button {{
      min-width: 44px;
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #eef2f6;
      color: var(--ink);
      cursor: pointer;
      font-size: 13px;
    }}
    button:hover {{ background: #e3e9f0; }}
    .dashboard {{
      display: grid;
      grid-template-rows: minmax(420px, 1fr) 150px;
      height: calc(100vh - 82px);
      width: 100%;
      overflow: hidden;
    }}
    .chart-panel {{
      position: relative;
      min-height: 0;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      overflow: hidden;
    }}
    .chart {{
      position: absolute;
      inset: 0;
    }}
    .pane-title {{
      position: absolute;
      z-index: 6;
      top: 8px;
      left: 12px;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(255,255,255,0.86);
      color: var(--muted);
      font-size: 12px;
      pointer-events: none;
    }}
    .summary {{
      overflow: auto;
      border-top: 1px solid var(--line);
      background: var(--panel);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    th, td {{
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
    }}
    th:first-child, td:first-child,
    th:nth-child(2), td:nth-child(2),
    th:nth-child(3), td:nth-child(3) {{
      text-align: left;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f8fafc;
      color: var(--muted);
      font-weight: 650;
      z-index: 1;
    }}
    @media (max-width: 760px) {{
      header {{
        min-height: 132px;
        align-items: flex-start;
        flex-direction: column;
      }}
      .toolbar {{ justify-content: flex-start; }}
      .dashboard {{
        height: calc(100vh - 132px);
        grid-template-rows: minmax(320px, 1fr) 130px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{title}</h1>
      <div class="meta">
        quality {float(metrics.get("quality_score", 0.0)):.3f} |
        segments {int(metrics.get("segment_count", 0))} |
        avg length {float(metrics.get("average_segment_length", 0.0)):.1f} |
        stability {float(metrics.get("within_segment_stability", 0.0)):.3f} |
        between diff {float(metrics.get("between_segment_difference", 0.0)):.3f}
      </div>
    </div>
    <div class="toolbar">
      <button id="range-3m" type="button">3M</button>
      <button id="range-6m" type="button">6M</button>
      <button id="range-1y" type="button">1Y</button>
      <button id="range-2023" type="button">2023</button>
      <button id="range-2024" type="button">2024</button>
      <button id="range-2025" type="button">2025</button>
      <button id="range-all" type="button">All</button>
    </div>
  </header>
  <main class="dashboard">
    <section class="chart-panel">
      <div class="pane-title">Price colored by segment_id; circles mark boundaries</div>
      <div id="price-chart" class="chart"></div>
    </section>
    <section class="summary">
      <table>
        <thead>
          <tr>
            <th>segment</th>
            <th>start</th>
            <th>end</th>
            <th>bars</th>
            <th>log_return</th>
            <th>vol</th>
            <th>score</th>
          </tr>
        </thead>
        <tbody id="summary-body"></tbody>
      </table>
    </section>
  </main>
  <script>
{bundle}
  </script>
  <script>
    const payload = {json.dumps(payload, separators=(",", ":"))};
    const initialMonths = {int(initial_months)};

    const chart = LightweightCharts.createChart(document.getElementById("price-chart"), {{
        autoSize: true,
        layout: {{
          background: {{ type: "solid", color: "#ffffff" }},
          textColor: "#17202e",
          fontFamily: "Arial, Helvetica, sans-serif",
        }},
        grid: {{
          vertLines: {{ color: "#edf1f4" }},
          horzLines: {{ color: "#edf1f4" }},
        }},
        rightPriceScale: {{
          borderColor: "#d9e0e8",
          scaleMargins: {{ top: 0.16, bottom: 0.12 }},
        }},
        timeScale: {{
          borderColor: "#d9e0e8",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 8,
          barSpacing: 6,
        }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        localization: {{ priceFormatter: price => price.toFixed(5) }},
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
    const markerPrimitive = LightweightCharts.createSeriesMarkers(candleSeries, [], {{ zOrder: "top" }});

    function lowerBoundByTime(data, target) {{
      let low = 0;
      let high = data.length;
      while (low < high) {{
        const mid = (low + high) >> 1;
        if (data[mid].time < target) low = mid + 1;
        else high = mid;
      }}
      return low;
    }}
    function sliceByTime(data, from, to) {{
      const start = Math.max(0, lowerBoundByTime(data, from));
      const end = Math.min(data.length, lowerBoundByTime(data, to) + 1);
      return data.slice(start, end);
    }}
    function sliceMarkersByTime(markers, from, to) {{
      const start = Math.max(0, lowerBoundByTime(markers, from));
      const end = Math.min(markers.length, lowerBoundByTime(markers, to) + 1);
      return markers.slice(start, end);
    }}
    function setMarkers(markers) {{
      if (markerPrimitive && typeof markerPrimitive.setMarkers === "function") {{
        markerPrimitive.setMarkers(markers);
      }}
    }}
    function renderSummary() {{
      const body = document.getElementById("summary-body");
      body.innerHTML = payload.summaryRows.map(row => `
        <tr>
          <td>S${{row.segment_id}}</td>
          <td>${{row.start}}</td>
          <td>${{row.end}}</td>
          <td>${{row.bars}}</td>
          <td>${{Number(row.log_return_sum || 0).toFixed(5)}}</td>
          <td>${{Number(row.return_vol || 0).toFixed(6)}}</td>
          <td>${{Number(row.mean_boundary_score || 0).toFixed(3)}}</td>
        </tr>
      `).join("");
    }}
    function fullRange() {{
      return {{
        from: payload.candles[0].time,
        to: payload.candles[payload.candles.length - 1].time,
      }};
    }}
    function loadFineRange(from, to) {{
      const paddedFrom = Math.max(fullRange().from, from - 7 * 24 * 60 * 60);
      const paddedTo = Math.min(fullRange().to, to + 7 * 24 * 60 * 60);
      candleSeries.setData(sliceByTime(payload.candles, paddedFrom, paddedTo));
      setMarkers(sliceMarkersByTime(payload.markers, paddedFrom, paddedTo));
      chart.timeScale().setVisibleRange({{ from, to }});
    }}
    function setRecentMonths(months) {{
      if (!payload.candles.length) return;
      const last = payload.candles[payload.candles.length - 1].time;
      const first = Math.max(payload.candles[0].time, last - months * 30 * 24 * 60 * 60);
      loadFineRange(first, last);
    }}
    function setYear(year) {{
      const from = Math.floor(Date.UTC(year, 0, 1) / 1000);
      const to = Math.floor(Date.UTC(year + 1, 0, 1) / 1000);
      loadFineRange(Math.max(fullRange().from, from), Math.min(fullRange().to, to));
    }}
    function setOverviewAll() {{
      candleSeries.setData(payload.candles);
      setMarkers(payload.markers);
      chart.timeScale().fitContent();
    }}

    document.getElementById("range-3m").addEventListener("click", () => setRecentMonths(3));
    document.getElementById("range-6m").addEventListener("click", () => setRecentMonths(6));
    document.getElementById("range-1y").addEventListener("click", () => setRecentMonths(12));
    document.getElementById("range-2023").addEventListener("click", () => setYear(2023));
    document.getElementById("range-2024").addEventListener("click", () => setYear(2024));
    document.getElementById("range-2025").addEventListener("click", () => setYear(2025));
    document.getElementById("range-all").addEventListener("click", setOverviewAll);

    renderSummary();
    setRecentMonths(initialMonths);
  </script>
</body>
</html>
"""


def render_index_html(title: str, evaluation: pd.DataFrame, html_paths: dict[str, Path]) -> str:
    rows = []
    for row in evaluation.itertuples(index=False):
        method = str(row.method)
        href = html_paths.get(method, Path(""))
        rows.append(
            "<tr>"
            f"<td><a href=\"{href.name}\">{method}</a></td>"
            f"<td>{float(row.quality_score):.3f}</td>"
            f"<td>{int(row.segment_count)}</td>"
            f"<td>{float(row.average_segment_length):.1f}</td>"
            f"<td>{float(row.within_segment_stability):.3f}</td>"
            f"<td>{float(row.between_segment_difference):.3f}</td>"
            f"<td>{float(row.forward_trend_clarity):.3f}</td>"
            "</tr>",
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f4f6f8;
      color: #17202e;
      font-family: Arial, Helvetica, sans-serif;
    }}
    h1 {{
      margin: 0 0 16px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #ffffff;
      border: 1px solid #d9e0e8;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid #d9e0e8;
      text-align: left;
      font-size: 13px;
    }}
    th {{ color: #667282; font-weight: 650; }}
    a {{ color: #235789; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <table>
    <thead>
      <tr>
        <th>method</th>
        <th>quality</th>
        <th>segments</th>
        <th>avg length</th>
        <th>stability</th>
        <th>between diff</th>
        <th>trend clarity</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


def write_outputs(args: argparse.Namespace) -> tuple[pd.DataFrame, Path]:
    methods = tuple(str(method).lower() for method in args.methods)
    allowed = {"pelt", "binseg", "cusum", "zigzag", "hmm"}
    invalid = set(methods) - allowed
    if invalid:
        raise ValueError(f"unknown methods: {sorted(invalid)}")

    bars = load_bars(args.catalog, args.bar_type, args.start, args.end)
    params = MarketSegmentationParams(
        min_segment_bars=args.min_segment_bars,
        pelt_penalty=args.pelt_penalty,
    )
    segmentations = compute_all_market_segmentations(
        bars,
        params=params,
        methods=methods,  # type: ignore[arg-type]
        feature_columns=DEFAULT_FEATURE_COLUMNS,
    )
    evaluation = evaluate_market_segmentations(
        segmentations,
        SegmentationEvalParams(
            min_segment_bars=args.min_segment_bars,
            feature_columns=DEFAULT_FEATURE_COLUMNS,
        ),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    html_paths: dict[str, Path] = {}
    for method, segmentation in segmentations.items():
        csv_path = args.output_dir / f"{args.output_stem}_{method}.csv"
        summary_path = args.output_dir / f"{args.output_stem}_{method}_segments.csv"
        html_path = args.output_dir / f"{args.output_stem}_{method}.html"
        segmentation.to_csv(csv_path, index=False)
        segment_summary(segmentation).to_csv(summary_path, index=False)
        metrics = evaluation.loc[evaluation["method"] == method].iloc[0].to_dict()
        html_path.write_text(
            render_method_html(
                title=f"{args.bar_type} market segmentation: {method}",
                method=method,
                segmentation=segmentation,
                metrics=metrics,
                initial_months=args.months,
            ),
            encoding="utf-8",
        )
        html_paths[method] = html_path

    evaluation_path = args.output_dir / f"{args.output_stem}_evaluation.csv"
    evaluation_json_path = args.output_dir / f"{args.output_stem}_evaluation.json"
    index_path = args.output_dir / f"{args.output_stem}_index.html"
    evaluation.to_csv(evaluation_path, index=False)
    evaluation_json_path.write_text(json.dumps(evaluation.to_dict(orient="records"), indent=2), encoding="utf-8")
    index_path.write_text(
        render_index_html(f"{args.bar_type} market segmentation research", evaluation, html_paths),
        encoding="utf-8",
    )
    return evaluation, index_path


def main() -> None:
    args = parse_args()
    evaluation, index_path = write_outputs(args)
    print(evaluation.to_string(index=False))
    print(f"index: {index_path}")


if __name__ == "__main__":
    main()
