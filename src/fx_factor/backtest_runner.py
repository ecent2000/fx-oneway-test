from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import BacktestRunConfig
from nautilus_trader.config import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import RiskEngineConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import Venue


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def bar_type_for(instrument_id: str, bar_minutes: int, price_type: str = "MID") -> str:
    if bar_minutes % 60 == 0:
        spec = f"{bar_minutes // 60}-HOUR-{price_type}"
    else:
        spec = f"{bar_minutes}-MINUTE-{price_type}"
    return f"{instrument_id}-{spec}-EXTERNAL"


def build_run_config(
    *,
    catalog: Path,
    report_dir: Path,
    instrument_id: str,
    bar_type: str,
    data_kind: str,
    start: str,
    end: str,
    lookback: int,
    entry_z: float,
    exit_z: float,
    stop_z: float,
    max_position_bars: int,
    trade_size: Decimal,
    starting_balance: str,
    chunk_size: int,
    log_level: str,
    default_leverage: float = 30.0,
    price_protection_points: int = 0,
    liquidity_consumption: bool = False,
    report: bool = True,
) -> BacktestRunConfig:
    strategy = ImportableStrategyConfig(
        strategy_path="fx_factor.strategies.rolling_zscore_fx:RollingZScoreFxStrategy",
        config_path="fx_factor.strategies.rolling_zscore_fx:RollingZScoreFxConfig",
        config={
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "trade_size": str(trade_size),
            "lookback": lookback,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "stop_z": stop_z,
            "max_position_bars": max_position_bars,
            "close_positions_on_stop": True,
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

    venue = BacktestVenueConfig(
        name="SIM",
        oms_type="NETTING",
        account_type="MARGIN",
        base_currency="USD",
        starting_balances=[starting_balance],
        default_leverage=default_leverage,
        price_protection_points=price_protection_points,
        liquidity_consumption=liquidity_consumption,
    )

    if data_kind == "bars":
        data = BacktestDataConfig(
            catalog_path=catalog.resolve().as_posix(),
            data_cls=Bar.fully_qualified_name(),
            bar_types=[bar_type],
            start_time=start,
            end_time=end,
            optimize_file_loading=True,
        )
    elif data_kind == "ticks":
        data = BacktestDataConfig(
            catalog_path=catalog.resolve().as_posix(),
            data_cls=QuoteTick.fully_qualified_name(),
            instrument_id=instrument_id,
            start_time=start,
            end_time=end,
            optimize_file_loading=True,
        )
    else:
        raise ValueError(f"Unsupported data_kind: {data_kind}")

    return BacktestRunConfig(
        venues=[venue],
        data=[data],
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


def run_backtest(
    *,
    catalog: Path,
    reports_root: Path,
    instrument_id: str,
    bar_type: str,
    data_kind: str,
    start: str,
    end: str,
    lookback: int,
    entry_z: float,
    exit_z: float,
    stop_z: float = 0.0,
    max_position_bars: int = 0,
    trade_size: Decimal = Decimal("100000"),
    starting_balance: str = "1000000 USD",
    chunk_size: int = 100_000,
    log_level: str = "ERROR",
    report_prefix: str = "backtest",
    write_reports: bool = True,
    default_leverage: float = 30.0,
    price_protection_points: int = 0,
    liquidity_consumption: bool = False,
) -> dict[str, Any]:
    report_dir = reports_root.resolve() / f"{report_prefix}_{utc_stamp()}"
    if write_reports:
        report_dir.mkdir(parents=True, exist_ok=True)

    run_config = build_run_config(
        catalog=catalog,
        report_dir=report_dir,
        instrument_id=instrument_id,
        bar_type=bar_type,
        data_kind=data_kind,
        start=start,
        end=end,
        lookback=lookback,
        entry_z=entry_z,
        exit_z=exit_z,
        stop_z=stop_z,
        max_position_bars=max_position_bars,
        trade_size=trade_size,
        starting_balance=starting_balance,
        chunk_size=chunk_size,
        log_level=log_level,
        default_leverage=default_leverage,
        price_protection_points=price_protection_points,
        liquidity_consumption=liquidity_consumption,
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
        "config": {
            "catalog": catalog.resolve().as_posix(),
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "data_kind": data_kind,
            "start": start,
            "end": end,
            "lookback": lookback,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "stop_z": stop_z,
            "max_position_bars": max_position_bars,
            "trade_size": str(trade_size),
            "starting_balance": starting_balance,
            "default_leverage": default_leverage,
            "price_protection_points": price_protection_points,
            "liquidity_consumption": liquidity_consumption,
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
