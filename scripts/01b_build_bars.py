from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"
PRICE_TYPES = ("BID", "ASK", "MID")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build external time bars from catalog QuoteTick data.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--instrument-id", default="EUR/USD.SIM")
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--bar-minutes", type=int, default=15)
    parser.add_argument("--price-types", nargs="+", choices=PRICE_TYPES, default=list(PRICE_TYPES))
    return parser.parse_args()


def iter_dates(start: date, end: date) -> pd.DatetimeIndex:
    if start >= end:
        raise ValueError(f"--from-date must be before --to-date, got {start} >= {end}")

    return pd.date_range(start=start, end=end, freq="D", inclusive="left", tz="UTC")


def bar_spec_text(bar_minutes: int, price_type: str) -> str:
    if bar_minutes % 60 == 0:
        hours = bar_minutes // 60
        return f"{hours}-HOUR-{price_type}"
    return f"{bar_minutes}-MINUTE-{price_type}"


def tick_frame(ticks: list[QuoteTick]) -> pd.DataFrame:
    index = pd.to_datetime([tick.ts_event for tick in ticks], unit="ns", utc=True)
    return pd.DataFrame(
        {
            "bid": [tick.bid_price.as_double() for tick in ticks],
            "ask": [tick.ask_price.as_double() for tick in ticks],
            "bid_size": [tick.bid_size.as_double() for tick in ticks],
            "ask_size": [tick.ask_size.as_double() for tick in ticks],
        },
        index=index,
    )


def aggregate_ohlcv(frame: pd.DataFrame, price_type: str, bar_minutes: int) -> pd.DataFrame:
    if price_type == "BID":
        price = frame["bid"]
    elif price_type == "ASK":
        price = frame["ask"]
    elif price_type == "MID":
        price = (frame["bid"] + frame["ask"]) / 2.0
    else:
        raise ValueError(f"Unsupported price type: {price_type}")

    rule = f"{bar_minutes}min"
    bars = price.resample(rule, label="right", closed="right").ohlc()
    # Dukascopy FX volumes were scaled to base units for QuoteTick sizes; summing them over
    # 15m bars can exceed Nautilus Quantity limits. The first strategy only uses OHLC, so store
    # per-bar update counts as a stable, bounded activity proxy.
    bars["volume"] = price.resample(rule, label="right", closed="right").count()
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    bars = bars[bars["volume"] > 0]
    bars.index.name = "timestamp"
    return bars[["open", "high", "low", "close", "volume"]]


def catalog_has_interval(
    catalog: ParquetDataCatalog,
    bar_type: BarType,
    start_ns: int,
    end_ns: int,
) -> bool:
    intervals = catalog.get_intervals(Bar, str(bar_type))
    return (start_ns, end_ns) in intervals


def main() -> None:
    args = parse_args()
    catalog_path = args.catalog.resolve()
    catalog = ParquetDataCatalog(path=catalog_path.as_posix())
    instruments = catalog.instruments(instrument_ids=[args.instrument_id])
    if not instruments:
        raise RuntimeError(f"Instrument not found in catalog: {args.instrument_id}")
    instrument = instruments[0]

    bar_types = {
        price_type: BarType.from_str(
            f"{args.instrument_id}-{bar_spec_text(args.bar_minutes, price_type)}-EXTERNAL",
        )
        for price_type in args.price_types
    }
    wranglers = {
        price_type: BarDataWrangler(bar_type=bar_type, instrument=instrument)
        for price_type, bar_type in bar_types.items()
    }

    total_bars = {price_type: 0 for price_type in args.price_types}
    days_empty = 0
    days_existing = 0
    days_written = 0

    for day in iter_dates(args.from_date, args.to_date):
        start = day.isoformat().replace("+00:00", "Z")
        end_ts = day + pd.Timedelta(days=1)
        end = end_ts.isoformat().replace("+00:00", "Z")
        ticks = catalog.quote_ticks([args.instrument_id], start=start, end=end)
        if not ticks:
            days_empty += 1
            print(f"empty   {day.date()}")
            continue

        frame = tick_frame(ticks)
        wrote_any = False
        existed_all = True
        for price_type, bar_type in bar_types.items():
            ohlcv = aggregate_ohlcv(frame, price_type, args.bar_minutes)
            ohlcv = ohlcv[ohlcv.index > day]
            if ohlcv.empty:
                continue

            bars = wranglers[price_type].process(ohlcv)
            start_ns = bars[0].ts_init
            end_ns = bars[-1].ts_init
            if catalog_has_interval(catalog, bar_type, start_ns, end_ns):
                continue

            catalog.write_data(bars)
            wrote_any = True
            existed_all = False
            total_bars[price_type] += len(bars)

        if wrote_any:
            days_written += 1
            print(
                f"wrote   {day.date()}: "
                + ", ".join(f"{pt}={total_bars[pt]:,}" for pt in args.price_types),
            )
        elif existed_all:
            days_existing += 1
            print(f"exists  {day.date()}")

    print()
    print(f"catalog:       {catalog_path}")
    print(f"instrument:    {args.instrument_id}")
    print(f"date range:    {args.from_date} <= day < {args.to_date}")
    print(f"days empty:    {days_empty:,}")
    print(f"days existing: {days_existing:,}")
    print(f"days written:  {days_written:,}")
    for price_type in args.price_types:
        print(f"{price_type.lower()} bars:      {total_bars[price_type]:,}")


if __name__ == "__main__":
    main()
