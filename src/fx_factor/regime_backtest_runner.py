from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from math import inf
from math import nan
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import BacktestRunConfig
from nautilus_trader.config import BacktestVenueConfig
from nautilus_trader.config import FixedFeeModelConfig
from nautilus_trader.config import FillModelConfig
from nautilus_trader.config import ImportableFeeModelConfig
from nautilus_trader.config import ImportableFillModelConfig
from nautilus_trader.config import ImportableLatencyModelConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LatencyModelConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import RiskEngineConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog


REGIME_STRATEGY_ID = "regime-adaptive-fx-timing"


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def bar_type_for(instrument_id: str, bar_minutes: int, price_type: str = "MID") -> str:
    if bar_minutes % 60 == 0:
        spec = f"{bar_minutes // 60}-HOUR-{price_type}"
    else:
        spec = f"{bar_minutes}-MINUTE-{price_type}"
    return f"{instrument_id}-{spec}-EXTERNAL"


def default_params() -> dict[str, Any]:
    return {
        "channel_lookback": 96,
        "atr_lookback": 48,
        "trend_lookback": 96,
        "momentum_lookback": 24,
        "breakout_lookback": 48,
        "short_ma_lookback": 16,
        "confirm_bars": 4,
        "long_confirm_bars": 4,
        "short_confirm_bars": 4,
        "bull_threshold": 0.03,
        "bear_threshold": 0.03,
        "flat_threshold": 0.01,
        "narrow_width_threshold": 8.0,
        "wide_range_threshold": 10.0,
        "momentum_entry": 0.0003,
        "pullback_buy_zone": 0.33,
        "pullback_sell_zone": 0.67,
        "lower_third": 0.33,
        "upper_third": 0.67,
        "stop_atr_mult": 2.0,
        "long_stop_atr_mult": 1.5,
        "short_stop_atr_mult": 1.5,
        "max_position_bars": 96,
        "max_spread_points": 0,
        "cooldown_bars": 4,
        "min_regime_bars": 2,
        "min_target_atr_mult": 0.8,
        "long_min_target_atr_mult": 0.8,
        "short_min_target_atr_mult": 0.8,
        "min_target_spread_mult": 0.0,
        "assumed_spread_points": 10,
        "trade_direction": "long_short",
        "strict_long_filter": False,
        "strict_long_trend_mult": 1.5,
        "disable_wide_range_longs": False,
        "range_breakout_buffer_atr": 0.1,
        "range_structure_filter": True,
        "range_structure_lookback": 3,
        "disabled_entry_regimes": "",
        "entry_hours_utc": "",
        "long_enabled_regimes": "wide_bull",
        "short_enabled_regimes": "wide_bear",
        "long_entry_hours_utc": "6-8",
        "short_entry_hours_utc": "13-15",
    }


def pnl_to_float(value: object) -> float:
    text = str(value).replace("USD", "").strip()
    return float(text) if text else 0.0


def profit_factor(values: pd.Series) -> float:
    gross_profit = float(values[values > 0.0].sum())
    gross_loss = float(-values[values < 0.0].sum())
    if gross_loss == 0.0:
        return inf if gross_profit > 0.0 else nan
    return gross_profit / gross_loss


def max_drawdown(values: pd.Series) -> float:
    if values.empty:
        return nan
    equity = pd.concat([pd.Series([0.0]), values.reset_index(drop=True).cumsum()], ignore_index=True)
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def side_position_metrics(positions: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    empty = {
        "trades": 0,
        "pnl_usd": 0.0,
        "gross_profit_usd": 0.0,
        "gross_loss_usd": 0.0,
        "win_rate": nan,
        "profit_factor": nan,
        "max_drawdown_usd": nan,
    }
    metrics = {
        "long": dict(empty),
        "short": dict(empty),
    }
    if positions is None or positions.empty:
        return metrics

    frame = positions.copy()
    frame["pnl_usd"] = frame["realized_pnl"].map(pnl_to_float)
    frame["ts_closed"] = pd.to_datetime(frame["ts_closed"], utc=True)
    for side_name, entry in {"long": "BUY", "short": "SELL"}.items():
        side = frame[frame["entry"].str.upper() == entry].sort_values("ts_closed")
        if side.empty:
            continue
        values = side["pnl_usd"]
        metrics[side_name] = {
            "trades": int(len(side)),
            "pnl_usd": float(values.sum()),
            "gross_profit_usd": float(values[values > 0.0].sum()),
            "gross_loss_usd": float(-values[values < 0.0].sum()),
            "win_rate": float((values > 0.0).mean()),
            "profit_factor": profit_factor(values),
            "max_drawdown_usd": max_drawdown(values),
        }
    return metrics


def build_run_config(
    *,
    catalog: Path,
    report_dir: Path,
    instrument_id: str,
    bar_type: str,
    start: str,
    end: str,
    params: dict[str, Any],
    trade_size: Decimal,
    starting_balance: str,
    chunk_size: int,
    log_level: str,
    default_leverage: float = 30.0,
    price_protection_points: int = 0,
    liquidity_consumption: bool = False,
    include_quote_ticks: bool = False,
    prob_slippage: float = 0.0,
    random_seed: int | None = None,
    commission: str | None = None,
    base_latency_nanos: int = 0,
    report: bool = True,
) -> BacktestRunConfig:
    strategy_params = {**default_params(), **params}
    strategy = ImportableStrategyConfig(
        strategy_path="fx_factor.strategies.regime_adaptive_fx:RegimeAdaptiveFxStrategy",
        config_path="fx_factor.strategies.regime_adaptive_fx:RegimeAdaptiveFxConfig",
        config={
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "trade_size": str(trade_size),
            "close_positions_on_stop": True,
            **strategy_params,
        },
    )

    engine = BacktestEngineConfig(
        trader_id="BACKTESTER-001",
        logging=LoggingConfig(
            log_level=log_level,
            log_level_file="INFO" if report else None,
            log_directory=str(report_dir) if report else None,
            log_file_name="nautilus_backtest.log" if report else None,
            clear_log_file=True,
            bypass_logging=not report and log_level.upper() == "OFF",
        ),
        risk_engine=RiskEngineConfig(bypass=True),
        strategies=[strategy],
        run_analysis=True,
    )

    fill_model = None
    if prob_slippage > 0.0:
        fill_model = ImportableFillModelConfig(
            fill_model_path="nautilus_trader.backtest.models:FillModel",
            config_path="nautilus_trader.config:FillModelConfig",
            config=FillModelConfig(
                prob_slippage=prob_slippage,
                random_seed=random_seed,
            ).dict(),
        )

    fee_model = None
    if commission is not None:
        fee_model = ImportableFeeModelConfig(
            fee_model_path="nautilus_trader.backtest.models:FixedFeeModel",
            config_path="nautilus_trader.config:FixedFeeModelConfig",
            config=FixedFeeModelConfig(commission=commission).dict(),
        )

    latency_model = None
    if base_latency_nanos > 0:
        latency_model = ImportableLatencyModelConfig(
            latency_model_path="nautilus_trader.backtest.models:LatencyModel",
            config_path="nautilus_trader.config:LatencyModelConfig",
            config=LatencyModelConfig(base_latency_nanos=base_latency_nanos).dict(),
        )

    venue = BacktestVenueConfig(
        name="SIM",
        oms_type="NETTING",
        account_type="MARGIN",
        base_currency="USD",
        starting_balances=[starting_balance],
        default_leverage=default_leverage,
        fill_model=fill_model,
        fee_model=fee_model,
        latency_model=latency_model,
        price_protection_points=price_protection_points,
        liquidity_consumption=liquidity_consumption,
    )

    data: list[BacktestDataConfig] = [
        BacktestDataConfig(
            catalog_path=catalog.resolve().as_posix(),
            data_cls=Bar.fully_qualified_name(),
            bar_types=[bar_type],
            start_time=start,
            end_time=end,
            optimize_file_loading=True,
        ),
    ]
    if include_quote_ticks:
        data.append(
            BacktestDataConfig(
                catalog_path=catalog.resolve().as_posix(),
                data_cls=QuoteTick.fully_qualified_name(),
                instrument_id=instrument_id,
                start_time=start,
                end_time=end,
                optimize_file_loading=True,
            ),
        )

    return BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=engine,
        chunk_size=chunk_size,
        raise_exception=True,
        dispose_on_completion=False,
        start=start,
        end=end,
    )


def write_report(df: pd.DataFrame | None, path: Path) -> None:
    if df is not None and not df.empty:
        df.to_csv(path)
    else:
        path.write_text("", encoding="utf-8")


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


def aggregate_fills(fills: pd.DataFrame | None, instrument_id: str) -> pd.DataFrame:
    if fills is None or fills.empty:
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


def write_bars_with_fills_chart(
    *,
    catalog: Path,
    report_dir: Path,
    instrument_id: str,
    bar_type: str,
    start: str,
    end: str,
    fills: pd.DataFrame | None,
) -> str:
    bars = bars_frame_from_catalog(catalog, bar_type, start, end)
    fill_points = aggregate_fills(fills, instrument_id)
    output_path = report_dir / "bars_with_fills.html"

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=bars["ts_init"],
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            name="15m bars",
            increasing_line_color="#1f7a3a",
            decreasing_line_color="#c7362f",
        ),
    )

    marker_specs = {
        "BUY": ("Buy fills", "triangle-up", "#2ca24d"),
        "SELL": ("Sell fills", "triangle-down", "#d6423a"),
    }
    for side, (name, symbol, color) in marker_specs.items():
        side_points = fill_points[fill_points["order_side"].str.upper() == side]
        if side_points.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=side_points["ts_init"],
                y=side_points["last_px"],
                mode="markers",
                name=name,
                marker={
                    "symbol": symbol,
                    "size": 10,
                    "color": color,
                    "line": {"color": "#111111", "width": 1.5},
                },
                customdata=side_points[["last_qty"]],
                hovertemplate=(
                    "%{x}<br>"
                    f"{side} @ " + "%{y:.5f}<br>"
                    "qty=%{customdata[0]:,.0f}<extra></extra>"
                ),
            ),
        )

    fig.update_layout(
        title=f"{bar_type} - Full Catalog Bars with Fills",
        template="plotly_white",
        height=900,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        margin={"l": 40, "r": 25, "t": 80, "b": 40},
        xaxis={"rangeslider": {"visible": True}, "title": "Time"},
        yaxis={"title": "Price", "fixedrange": False},
    )
    fig.write_html(str(output_path))
    return str(output_path)


def lightweight_charts_bundle() -> str:
    project_root = Path(__file__).resolve().parents[2]
    bundle_path = (
        project_root
        / "node_modules"
        / "lightweight-charts"
        / "dist"
        / "lightweight-charts.standalone.production.js"
    )
    if not bundle_path.exists():
        raise RuntimeError(
            "Lightweight Charts bundle not found. Run `npm install` from the project root.",
        )
    return bundle_path.read_text(encoding="utf-8")


def write_lightweight_bars_with_fills_chart(
    *,
    catalog: Path,
    report_dir: Path,
    instrument_id: str,
    bar_type: str,
    start: str,
    end: str,
    fills: pd.DataFrame | None,
) -> str:
    bars = bars_frame_from_catalog(catalog, bar_type, start, end)
    fill_points = aggregate_fills(fills, instrument_id)
    output_path = report_dir / "bars_with_fills_lwc.html"

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
        "barCount": len(candle_data),
        "fillCount": len(marker_data),
        "start": str(bars["ts_init"].min()),
        "end": str(bars["ts_init"].max()),
    }
    bundle = lightweight_charts_bundle()

    html = f"""<!doctype html>
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
      <div class="meta">{stats["barCount"]:,} bars, {stats["fillCount"]:,} fills | {stats["start"]} to {stats["end"]}</div>
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

    visibleRange(6);
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)


def write_visualizations(
    engine: Any,
    report_dir: Path,
    catalog: Path,
    instrument_id: str,
    bar_type: str,
    start: str,
    end: str,
    fills: pd.DataFrame | None,
) -> dict[str, str]:
    tearsheet_path = report_dir / "tearsheet.html"

    create_tearsheet(
        engine=engine,
        output_path=str(tearsheet_path),
        title="Regime Adaptive FX Backtest",
    )
    bars_with_fills_path = write_bars_with_fills_chart(
        catalog=catalog,
        report_dir=report_dir,
        instrument_id=instrument_id,
        bar_type=bar_type,
        start=start,
        end=end,
        fills=fills,
    )
    lightweight_bars_with_fills_path = write_lightweight_bars_with_fills_chart(
        catalog=catalog,
        report_dir=report_dir,
        instrument_id=instrument_id,
        bar_type=bar_type,
        start=start,
        end=end,
        fills=fills,
    )

    return {
        "tearsheet": str(tearsheet_path),
        "bars_with_fills": bars_with_fills_path,
        "bars_with_fills_lwc": lightweight_bars_with_fills_path,
    }


def run_regime_backtest(
    *,
    catalog: Path,
    reports_root: Path,
    instrument_id: str,
    bar_type: str,
    start: str,
    end: str,
    params: dict[str, Any] | None = None,
    trade_size: Decimal = Decimal("100000"),
    starting_balance: str = "1000000 USD",
    chunk_size: int = 100_000,
    log_level: str = "ERROR",
    report_prefix: str = "backtest",
    write_reports: bool = True,
    default_leverage: float = 30.0,
    price_protection_points: int = 0,
    liquidity_consumption: bool = False,
    include_quote_ticks: bool = False,
    prob_slippage: float = 0.0,
    random_seed: int | None = None,
    commission: str | None = None,
    base_latency_nanos: int = 0,
) -> dict[str, Any]:
    report_dir = reports_root.resolve() / REGIME_STRATEGY_ID / f"{report_prefix}_{utc_stamp()}"
    if write_reports:
        report_dir.mkdir(parents=True, exist_ok=True)

    merged_params = {**default_params(), **(params or {})}
    run_config = build_run_config(
        catalog=catalog,
        report_dir=report_dir,
        instrument_id=instrument_id,
        bar_type=bar_type,
        start=start,
        end=end,
        params=merged_params,
        trade_size=trade_size,
        starting_balance=starting_balance,
        chunk_size=chunk_size,
        log_level=log_level,
        default_leverage=default_leverage,
        price_protection_points=price_protection_points,
        liquidity_consumption=liquidity_consumption,
        include_quote_ticks=include_quote_ticks,
        prob_slippage=prob_slippage,
        random_seed=random_seed,
        commission=commission,
        base_latency_nanos=base_latency_nanos,
        report=write_reports,
    )
    node = BacktestNode(configs=[run_config])
    results = node.run()
    if not results:
        raise RuntimeError("BacktestNode returned no results")

    result = results[0]
    engine = node.get_engine(result.run_config_id)
    if engine is None:
        raise RuntimeError("Backtest engine was not available after run")

    orders = engine.trader.generate_orders_report()
    fills = engine.trader.generate_fills_report()
    positions = engine.trader.generate_positions_report()
    account = engine.trader.generate_account_report(venue=Venue("SIM"))

    result_dict = asdict(result)
    pnl_stats = result_dict.get("stats_pnls", {}).get("USD", {})
    return_stats = result_dict.get("stats_returns", {})
    summary: dict[str, Any] = {
        "strategy_id": REGIME_STRATEGY_ID,
        "config": {
            "catalog": catalog.resolve().as_posix(),
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "start": start,
            "end": end,
            "params": merged_params,
            "trade_size": str(trade_size),
            "starting_balance": starting_balance,
            "default_leverage": default_leverage,
            "price_protection_points": price_protection_points,
            "liquidity_consumption": liquidity_consumption,
            "include_quote_ticks": include_quote_ticks,
            "prob_slippage": prob_slippage,
            "random_seed": random_seed,
            "commission": commission,
            "base_latency_nanos": base_latency_nanos,
        },
        "result": result_dict,
        "metrics": {
            "pnl_usd": pnl_stats.get("PnL (total)", 0.0),
            "pnl_pct": pnl_stats.get("PnL% (total)", 0.0),
            "expectancy": pnl_stats.get("Expectancy", 0.0),
            "win_rate": pnl_stats.get("Win Rate", 0.0),
            "sharpe": return_stats.get("Sharpe Ratio (252 days)", 0.0),
            "sortino": return_stats.get("Sortino Ratio (252 days)", 0.0),
            "profit_factor": return_stats.get("Profit Factor", 0.0),
            "orders": result.total_orders,
            "positions": result.total_positions,
            "iterations": result.iterations,
        },
        "side_metrics": side_position_metrics(positions),
        "reports": {
            "dir": str(report_dir),
            "orders": str(report_dir / "orders.csv"),
            "fills": str(report_dir / "fills.csv"),
            "positions": str(report_dir / "positions.csv"),
            "account": str(report_dir / "account.csv"),
            "log": str(report_dir / "nautilus_backtest.log"),
            "tearsheet": str(report_dir / "tearsheet.html"),
            "bars_with_fills": str(report_dir / "bars_with_fills.html"),
            "bars_with_fills_lwc": str(report_dir / "bars_with_fills_lwc.html"),
        },
    }

    if write_reports:
        write_report(orders, report_dir / "orders.csv")
        write_report(fills, report_dir / "fills.csv")
        write_report(positions, report_dir / "positions.csv")
        write_report(account, report_dir / "account.csv")
        try:
            summary["reports"].update(
                write_visualizations(
                    engine=engine,
                    report_dir=report_dir,
                    catalog=catalog,
                    instrument_id=instrument_id,
                    bar_type=bar_type,
                    start=start,
                    end=end,
                    fills=fills,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            summary["visualization_error"] = str(exc)
        (report_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=True),
            encoding="utf-8",
        )

    engine.dispose()
    return summary
