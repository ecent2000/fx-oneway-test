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
        "channel_lookback": 48,
        "atr_lookback": 24,
        "trend_lookback": 48,
        "momentum_lookback": 12,
        "breakout_lookback": 24,
        "short_ma_lookback": 8,
        "confirm_bars": 3,
        "long_confirm_bars": 3,
        "short_confirm_bars": 3,
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
        },
    }

    if write_reports:
        write_report(orders, report_dir / "orders.csv")
        write_report(fills, report_dir / "fills.csv")
        write_report(positions, report_dir / "positions.csv")
        write_report(account, report_dir / "account.csv")
        (report_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=True),
            encoding="utf-8",
        )

    engine.dispose()
    return summary
