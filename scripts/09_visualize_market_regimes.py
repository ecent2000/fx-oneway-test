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
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports" / "regime-diagnostics"
DEFAULT_BAR_TYPE = "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL"
DEFAULT_START = "2023-01-01T00:00:00Z"
DEFAULT_END = "2026-01-01T00:00:00Z"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.market_regime import ALL_MARKET_REGIMES  # noqa: E402
from fx_factor.market_regime import MarketRegimeParams  # noqa: E402
from fx_factor.market_regime import compute_market_regimes  # noqa: E402


REGIME_COLORS = {
    "wide_bull": "#1b9e77",
    "narrow_bull": "#7fc97f",
    "wide_bear": "#d95f02",
    "narrow_bear": "#fdb462",
    "wide_range": "#7570b3",
    "narrow_range": "#80b1d3",
    "unknown": "#b9c0cc",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize EUR/USD 15m market-regime labels.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--bar-type", default=DEFAULT_BAR_TYPE)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--output-stem", default="market_regimes_2023_2025")
    parser.add_argument("--months", type=int, default=6)
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
    return bundle_path.read_text(encoding="utf-8")


def time_seconds(timestamp: pd.Timestamp) -> int:
    return int(pd.Timestamp(timestamp).timestamp())


def series_data(frame: pd.DataFrame, value_column: str, digits: int = 4) -> list[dict[str, float | int]]:
    values: list[dict[str, float | int]] = []
    for row in frame.loc[:, ["timestamp", value_column]].itertuples(index=False):
        value = float(getattr(row, value_column))
        if not pd.notna(value):
            continue
        values.append({"time": time_seconds(row.timestamp), "value": round(value, digits)})
    return values


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


def regime_segments(
    frame: pd.DataFrame,
    regime_column: str,
    *,
    fallback_regime: str = "unknown",
) -> list[dict[str, int | str]]:
    if frame.empty:
        return []

    times = [time_seconds(ts) for ts in frame["timestamp"]]
    deltas = [right - left for left, right in zip(times, times[1:]) if right > left]
    step = int(pd.Series(deltas).median()) if deltas else 15 * 60
    labels = frame[regime_column].fillna(fallback_regime).astype(str).tolist()

    segments: list[dict[str, int | str]] = []
    start_index = 0
    current = labels[0] if labels[0] in ALL_MARKET_REGIMES else fallback_regime
    for index, label in enumerate(labels[1:], start=1):
        label = label if label in ALL_MARKET_REGIMES else fallback_regime
        if label == current:
            continue
        segments.append(
            {
                "from": times[start_index],
                "to": times[index],
                "regime": current,
                "color": REGIME_COLORS.get(current, REGIME_COLORS[fallback_regime]),
            },
        )
        start_index = index
        current = label
    segments.append(
        {
            "from": times[start_index],
            "to": times[-1] + step,
            "regime": current,
            "color": REGIME_COLORS.get(current, REGIME_COLORS[fallback_regime]),
        },
    )
    return segments


def regime_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    counts = frame[column].fillna("unknown").astype(str).value_counts().to_dict()
    return {regime: int(counts.get(regime, 0)) for regime in sorted(ALL_MARKET_REGIMES)}


def overview_frames(
    bars: pd.DataFrame,
    diagnostics: pd.DataFrame,
    *,
    rule: str = "4h",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    indexed_bars = bars.set_index("timestamp").sort_index()
    overview_bars = (
        indexed_bars.resample(rule, label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    factor_columns = ["trend_score", "width_score", "trend_quality", "online_confidence"]
    indexed_diagnostics = diagnostics.set_index("timestamp").sort_index()
    overview_diagnostics = (
        indexed_diagnostics[factor_columns]
        .resample(rule, label="right", closed="right")
        .mean()
        .dropna(how="all")
        .reset_index()
    )
    return overview_bars, overview_diagnostics


def render_html(
    *,
    title: str,
    bars: pd.DataFrame,
    diagnostics: pd.DataFrame,
    online_segments: list[dict[str, int | str]],
    hindsight_segments: list[dict[str, int | str]],
    initial_months: int,
    stats: dict[str, Any],
) -> str:
    bundle = lightweight_charts_bundle()
    overview_bars, overview_diagnostics = overview_frames(bars, diagnostics)
    candles = candle_data(bars)
    overview_candles = candle_data(overview_bars)
    trend = series_data(diagnostics, "trend_score")
    width = series_data(diagnostics, "width_score")
    quality = series_data(diagnostics, "trend_quality")
    confidence = series_data(diagnostics, "online_confidence")
    overview_trend = series_data(overview_diagnostics, "trend_score")
    overview_width = series_data(overview_diagnostics, "width_score")
    overview_quality = series_data(overview_diagnostics, "trend_quality")
    overview_confidence = series_data(overview_diagnostics, "online_confidence")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #162033;
      --muted: #637083;
      --line: #d7deea;
      --button: #eef2f7;
      --button-hover: #e0e7f1;
    }}
    * {{ box-sizing: border-box; }}
    html {{
      width: 100%;
      height: 100%;
      overflow: hidden;
    }}
    body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, Helvetica, sans-serif;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 72px;
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
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
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
    .dashboard {{
      display: grid;
      grid-template-rows: minmax(420px, 1fr) 130px 130px 130px;
      width: 100%;
      height: calc(100vh - 72px);
      min-width: 0;
      overflow: hidden;
    }}
    .chart-panel {{
      position: relative;
      min-height: 0;
      min-width: 0;
      overflow: hidden;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    .chart {{
      position: absolute;
      inset: 0;
      min-width: 0;
      overflow: hidden;
    }}
    #regime-overlay {{
      position: absolute;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
      z-index: 5;
      pointer-events: none;
    }}
    .pane-title {{
      position: absolute;
      z-index: 6;
      top: 8px;
      left: 12px;
      padding: 2px 6px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.84);
      color: var(--muted);
      font-size: 12px;
      pointer-events: none;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      margin-top: 7px;
      font-size: 12px;
      color: var(--muted);
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      white-space: nowrap;
    }}
    .swatch {{
      width: 12px;
      height: 8px;
      border-radius: 2px;
      display: inline-block;
    }}
    @media (max-width: 760px) {{
      header {{
        align-items: flex-start;
        flex-direction: column;
        min-height: 126px;
      }}
      .dashboard {{
        height: calc(100vh - 126px);
        grid-template-rows: minmax(320px, 1fr) 108px 108px 108px;
      }}
      .toolbar {{
        justify-content: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>{title}</h1>
      <div class="meta">{stats["bar_count"]:,} bars | {stats["start"]} to {stats["end"]}</div>
      <div class="legend">
        <span><i class="swatch" style="background:#1b9e77"></i>wide_bull</span>
        <span><i class="swatch" style="background:#7fc97f"></i>narrow_bull</span>
        <span><i class="swatch" style="background:#d95f02"></i>wide_bear</span>
        <span><i class="swatch" style="background:#fdb462"></i>narrow_bear</span>
        <span><i class="swatch" style="background:#7570b3"></i>wide_range</span>
        <span><i class="swatch" style="background:#80b1d3"></i>narrow_range</span>
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
      <div class="pane-title">Price with online regime background and hindsight top band</div>
      <div id="price-chart" class="chart"></div>
      <canvas id="regime-overlay"></canvas>
    </section>
    <section class="chart-panel">
      <div class="pane-title">trend_score</div>
      <div id="trend-chart" class="chart"></div>
    </section>
    <section class="chart-panel">
      <div class="pane-title">width_score + trend_quality</div>
      <div id="width-chart" class="chart"></div>
    </section>
    <section class="chart-panel">
      <div class="pane-title">online_confidence</div>
      <div id="confidence-chart" class="chart"></div>
    </section>
  </main>
  <script>
{bundle}
  </script>
  <script>
    const fullCandleData = {json.dumps(candles, separators=(",", ":"))};
    const fullTrendData = {json.dumps(trend, separators=(",", ":"))};
    const fullWidthData = {json.dumps(width, separators=(",", ":"))};
    const fullQualityData = {json.dumps(quality, separators=(",", ":"))};
    const fullConfidenceData = {json.dumps(confidence, separators=(",", ":"))};
    const overviewCandleData = {json.dumps(overview_candles, separators=(",", ":"))};
    const overviewTrendData = {json.dumps(overview_trend, separators=(",", ":"))};
    const overviewWidthData = {json.dumps(overview_width, separators=(",", ":"))};
    const overviewQualityData = {json.dumps(overview_quality, separators=(",", ":"))};
    const overviewConfidenceData = {json.dumps(overview_confidence, separators=(",", ":"))};
    const onlineSegments = {json.dumps(online_segments, separators=(",", ":"))};
    const hindsightSegments = {json.dumps(hindsight_segments, separators=(",", ":"))};

    function makeChart(container, options = {{}}) {{
      return LightweightCharts.createChart(container, {{
        autoSize: true,
        layout: {{
          background: {{ type: "solid", color: "#ffffff" }},
          textColor: "#162033",
          fontFamily: "Arial, Helvetica, sans-serif",
        }},
        grid: {{
          vertLines: {{ color: "#edf1f6" }},
          horzLines: {{ color: "#edf1f6" }},
        }},
        rightPriceScale: {{
          borderColor: "#d7deea",
          scaleMargins: {{ top: 0.14, bottom: 0.12 }},
        }},
        timeScale: {{
          borderColor: "#d7deea",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 8,
          barSpacing: options.barSpacing ?? 6,
        }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        localization: options.localization ?? {{}},
      }});
    }}

    const priceChart = makeChart(document.getElementById("price-chart"), {{
      localization: {{ priceFormatter: price => price.toFixed(5) }},
    }});
    const trendChart = makeChart(document.getElementById("trend-chart"), {{ barSpacing: 6 }});
    const widthChart = makeChart(document.getElementById("width-chart"), {{ barSpacing: 6 }});
    const confidenceChart = makeChart(document.getElementById("confidence-chart"), {{ barSpacing: 6 }});
    const syncedCharts = [trendChart, widthChart, confidenceChart];

    const candleSeries = priceChart.addSeries(LightweightCharts.CandlestickSeries, {{
      upColor: "#178f45",
      downColor: "#c83232",
      borderUpColor: "#178f45",
      borderDownColor: "#c83232",
      wickUpColor: "#178f45",
      wickDownColor: "#c83232",
      priceFormat: {{ type: "price", precision: 5, minMove: 0.00001 }},
    }});

    const trendSeries = trendChart.addSeries(LightweightCharts.LineSeries, {{
      color: "#1b9e77",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    }});
    const zeroSeries = trendChart.addSeries(LightweightCharts.LineSeries, {{
      color: "rgba(22,32,51,0.28)",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    }});

    const widthSeries = widthChart.addSeries(LightweightCharts.LineSeries, {{
      color: "#7570b3",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    }});
    const qualitySeries = widthChart.addSeries(LightweightCharts.LineSeries, {{
      color: "#1b9e77",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
    }});

    const confidenceSeries = confidenceChart.addSeries(LightweightCharts.AreaSeries, {{
      topColor: "rgba(27,158,119,0.32)",
      bottomColor: "rgba(27,158,119,0.02)",
      lineColor: "#1b9e77",
      lineWidth: 2,
      priceLineVisible: false,
    }});

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

    function setChartData(dataSet, range) {{
      candleSeries.setData(dataSet.candles);
      trendSeries.setData(dataSet.trend);
      zeroSeries.setData(dataSet.candles.map(point => ({{ time: point.time, value: 0 }})));
      widthSeries.setData(dataSet.width);
      qualitySeries.setData(dataSet.quality);
      confidenceSeries.setData(dataSet.confidence);
      priceChart.timeScale().setVisibleRange(range);
      requestAnimationFrame(() => {{
        scheduleSyncAndOverlay(range);
        drawRegimeOverlay();
      }});
    }}

    function fullRange() {{
      return {{
        from: fullCandleData[0].time,
        to: fullCandleData[fullCandleData.length - 1].time,
      }};
    }}

    function loadFineRange(from, to) {{
      const paddedFrom = Math.max(fullCandleData[0].time, from - 7 * 24 * 60 * 60);
      const paddedTo = Math.min(fullCandleData[fullCandleData.length - 1].time, to + 7 * 24 * 60 * 60);
      setChartData(
        {{
          candles: sliceByTime(fullCandleData, paddedFrom, paddedTo),
          trend: sliceByTime(fullTrendData, paddedFrom, paddedTo),
          width: sliceByTime(fullWidthData, paddedFrom, paddedTo),
          quality: sliceByTime(fullQualityData, paddedFrom, paddedTo),
          confidence: sliceByTime(fullConfidenceData, paddedFrom, paddedTo),
        }},
        {{ from, to }},
      );
    }}

    function setRecentMonths(months) {{
      if (!fullCandleData.length) return;
      const last = fullCandleData[fullCandleData.length - 1].time;
      const first = Math.max(fullCandleData[0].time, last - months * 30 * 24 * 60 * 60);
      loadFineRange(first, last);
    }}

    function setYear(year) {{
      const from = Math.floor(Date.UTC(year, 0, 1) / 1000);
      const to = Math.floor(Date.UTC(year + 1, 0, 1) / 1000);
      loadFineRange(Math.max(fullRange().from, from), Math.min(fullRange().to, to));
    }}

    function setOverviewAll() {{
      if (!overviewCandleData.length) return;
      const range = {{
        from: overviewCandleData[0].time,
        to: overviewCandleData[overviewCandleData.length - 1].time,
      }};
      setChartData(
        {{
          candles: overviewCandleData,
          trend: overviewTrendData,
          width: overviewWidthData,
          quality: overviewQualityData,
          confidence: overviewConfidenceData,
        }},
        range,
      );
    }}

    let syncFrame = 0;
    let latestRange = null;
    function rangesEqual(left, right) {{
      if (!left || !right) return false;
      return left.from === right.from && left.to === right.to;
    }}
    function scheduleSyncAndOverlay(range) {{
      if (!range) return;
      latestRange = range;
      if (syncFrame) return;
      syncFrame = requestAnimationFrame(() => {{
        syncFrame = 0;
        for (const chart of syncedCharts) {{
          const current = chart.timeScale().getVisibleRange();
          if (!rangesEqual(current, latestRange)) {{
            chart.timeScale().setVisibleRange(latestRange);
          }}
        }}
        drawRegimeOverlay();
      }});
    }}
    priceChart.timeScale().subscribeVisibleTimeRangeChange(scheduleSyncAndOverlay);

    document.getElementById("range-3m").addEventListener("click", () => setRecentMonths(3));
    document.getElementById("range-6m").addEventListener("click", () => setRecentMonths(6));
    document.getElementById("range-1y").addEventListener("click", () => setRecentMonths(12));
    document.getElementById("range-2023").addEventListener("click", () => setYear(2023));
    document.getElementById("range-2024").addEventListener("click", () => setYear(2024));
    document.getElementById("range-2025").addEventListener("click", () => setYear(2025));
    document.getElementById("range-all").addEventListener("click", setOverviewAll);

    const overlay = document.getElementById("regime-overlay");
    const overlayContext = overlay.getContext("2d");
    let overlayRatio = window.devicePixelRatio || 1;
    function resizeOverlay() {{
      const rect = overlay.getBoundingClientRect();
      overlayRatio = window.devicePixelRatio || 1;
      const targetWidth = Math.max(1, Math.floor(rect.width * overlayRatio));
      const targetHeight = Math.max(1, Math.floor(rect.height * overlayRatio));
      if (overlay.width !== targetWidth || overlay.height !== targetHeight) {{
        overlay.width = targetWidth;
        overlay.height = targetHeight;
      }}
      overlayContext.setTransform(overlayRatio, 0, 0, overlayRatio, 0, 0);
      return rect;
    }}

    function coordinateFor(time, range, width) {{
      const direct = priceChart.timeScale().timeToCoordinate(time);
      if (direct !== null && direct !== undefined) return direct;
      if (!range || range.to === range.from) return null;
      return ((time - range.from) / (range.to - range.from)) * width;
    }}

    function firstVisibleSegmentIndex(segments, range) {{
      let low = 0;
      let high = segments.length;
      while (low < high) {{
        const mid = (low + high) >> 1;
        if (segments[mid].to < range.from) low = mid + 1;
        else high = mid;
      }}
      return low;
    }}

    function labelText(regime) {{
      return String(regime || "unknown");
    }}

    function textColorFor(regime) {{
      if (regime === "wide_bull" || regime === "wide_bear" || regime === "wide_range") {{
        return "#ffffff";
      }}
      return "#162033";
    }}

    function drawSegmentLabel(segment, x1, x2, y, options) {{
      const segmentWidth = x2 - x1;
      if (segmentWidth < options.minWidth) return;
      const text = labelText(segment.regime);
      overlayContext.font = options.font;
      const textWidth = overlayContext.measureText(text).width;
      if (segmentWidth < textWidth + options.padding * 2) return;
      const x = Math.floor(x1 + (segmentWidth - textWidth) / 2);
      overlayContext.globalAlpha = options.alpha;
      overlayContext.fillStyle = options.color === "auto" ? textColorFor(segment.regime) : options.color;
      if (options.stroke) {{
        overlayContext.lineWidth = options.strokeWidth;
        overlayContext.strokeStyle = options.stroke;
        overlayContext.strokeText(text, x, y);
      }}
      overlayContext.fillText(text, x, y);
      overlayContext.globalAlpha = 1;
    }}

    function drawSegments(segments, range, width, y, height, alpha, labelOptions = null) {{
      for (let index = firstVisibleSegmentIndex(segments, range); index < segments.length; index++) {{
        const segment = segments[index];
        if (segment.from > range.to) break;
        const x1 = coordinateFor(Math.max(segment.from, range.from), range, width);
        const x2 = coordinateFor(Math.min(segment.to, range.to), range, width);
        if (x1 === null || x2 === null) continue;
        overlayContext.globalAlpha = alpha;
        overlayContext.fillStyle = segment.color;
        overlayContext.fillRect(Math.floor(x1), y, Math.ceil(x2 - x1 + 1), height);
        if (labelOptions) {{
          drawSegmentLabel(segment, x1, x2, labelOptions.y, labelOptions);
        }}
      }}
      overlayContext.globalAlpha = 1;
    }}

    function drawRegimeOverlay() {{
      const rect = resizeOverlay();
      overlayContext.clearRect(0, 0, rect.width, rect.height);
      const range = priceChart.timeScale().getVisibleRange();
      if (!range) return;
      const hindsightBandHeight = 18;
      drawSegments(onlineSegments, range, rect.width, 0, rect.height, 0.10, {{
        y: Math.max(hindsightBandHeight + 18, rect.height - 10),
        minWidth: 72,
        padding: 6,
        font: "10px Arial, Helvetica, sans-serif",
        color: "#162033",
        alpha: 0.72,
        stroke: "rgba(255,255,255,0.92)",
        strokeWidth: 3,
      }});
      drawSegments(hindsightSegments, range, rect.width, 0, hindsightBandHeight, 0.85, {{
        y: 13,
        minWidth: 58,
        padding: 4,
        font: "9px Arial, Helvetica, sans-serif",
        color: "auto",
        alpha: 0.92,
        stroke: null,
        strokeWidth: 0,
      }});
      overlayContext.fillStyle = "rgba(22,32,51,0.28)";
      overlayContext.fillRect(0, hindsightBandHeight, rect.width, 1);
    }}

    const resizeObserver = new ResizeObserver(() => requestAnimationFrame(() => {{
      resizeOverlay();
      drawRegimeOverlay();
    }}));
    resizeObserver.observe(document.getElementById("price-chart"));

    setRecentMonths({initial_months});
    requestAnimationFrame(drawRegimeOverlay);
  </script>
</body>
</html>
"""


def write_outputs(args: argparse.Namespace) -> tuple[Path, Path]:
    bars = load_bars(args.catalog, args.bar_type, args.start, args.end)
    params = MarketRegimeParams()
    diagnostics = compute_market_regimes(bars, params)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / f"{args.output_stem}.csv"
    html_path = args.output_dir / f"{args.output_stem}.html"
    diagnostics.to_csv(csv_path, index=False)

    online_segments = regime_segments(diagnostics, "online_confirmed_regime")
    hindsight_segments = regime_segments(diagnostics, "hindsight_regime")
    stats = {
        "bar_count": len(bars),
        "start": str(bars["timestamp"].min()),
        "end": str(bars["timestamp"].max()),
        "online_counts": regime_counts(diagnostics, "online_confirmed_regime"),
        "hindsight_counts": regime_counts(diagnostics, "hindsight_regime"),
    }
    html_path.write_text(
        render_html(
            title=f"{args.bar_type} market regimes",
            bars=bars,
            diagnostics=diagnostics,
            online_segments=online_segments,
            hindsight_segments=hindsight_segments,
            initial_months=args.months,
            stats=stats,
        ),
        encoding="utf-8",
    )
    summary_path = args.output_dir / f"{args.output_stem}_summary.json"
    summary_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return csv_path, html_path


def main() -> None:
    args = parse_args()
    csv_path, html_path = write_outputs(args)
    print(f"csv:  {csv_path}")
    print(f"html: {html_path}")


if __name__ == "__main__":
    main()
