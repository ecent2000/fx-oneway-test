from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "eurusd"
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog"


@dataclass(frozen=True)
class IngestStats:
    files_seen: int = 0
    files_missing: int = 0
    files_empty: int = 0
    files_existing: int = 0
    files_written: int = 0
    rows_read: int = 0
    ticks_written: int = 0

    def add(self, **updates: int) -> IngestStats:
        values = self.__dict__.copy()
        for key, value in updates.items():
            values[key] += value
        return IngestStats(**values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Dukascopy daily EUR/USD quote tick CSV files into a NautilusTrader "
            "ParquetDataCatalog."
        ),
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2024, 10, 1))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date(2024, 11, 1))
    parser.add_argument(
        "--volume-multiplier",
        type=float,
        default=1_000_000.0,
        help="Multiplier applied to Dukascopy askVolume/bidVolume before writing QuoteTick sizes.",
    )
    parser.add_argument(
        "--ts-init-delta",
        type=int,
        default=0,
        help="Nanoseconds added to ts_event for Nautilus ts_init.",
    )
    return parser.parse_args()


def iter_dates(start: date, end: date) -> pd.DatetimeIndex:
    if start >= end:
        raise ValueError(f"--from-date must be before --to-date, got {start} >= {end}")

    return pd.date_range(start=start, end=end, freq="D", inclusive="left")


def daily_csv_path(raw_root: Path, day: pd.Timestamp) -> Path:
    day_str = day.strftime("%Y-%m-%d")
    month = day.strftime("%Y-%m")
    return raw_root / month / f"eurusd_tick_{day_str}.csv"


def read_dukascopy_csv(path: Path, volume_multiplier: float) -> pd.DataFrame:
    data = pd.read_csv(
        path,
        usecols=["timestamp", "askPrice", "bidPrice", "askVolume", "bidVolume"],
        dtype={
            "timestamp": "int64",
            "askPrice": "float64",
            "bidPrice": "float64",
            "askVolume": "float64",
            "bidVolume": "float64",
        },
    )

    if data.empty:
        return pd.DataFrame(columns=["bid_price", "ask_price", "bid_size", "ask_size"])

    data = data.rename(
        columns={
            "bidPrice": "bid_price",
            "askPrice": "ask_price",
            "bidVolume": "bid_size",
            "askVolume": "ask_size",
        },
    )
    data["bid_size"] *= volume_multiplier
    data["ask_size"] *= volume_multiplier
    data.index = pd.to_datetime(data.pop("timestamp"), unit="ms", utc=True)
    data.index.name = "timestamp"

    data = data[["bid_price", "ask_price", "bid_size", "ask_size"]]
    data = data.dropna()
    data = data[data["bid_price"] <= data["ask_price"]]
    return data.sort_index(kind="stable")


def ingest() -> IngestStats:
    args = parse_args()
    raw_root = args.raw_root.resolve()
    catalog_path = args.catalog.resolve()
    catalog_path.mkdir(parents=True, exist_ok=True)

    instrument = TestInstrumentProvider.default_fx_ccy(args.symbol)
    wrangler = QuoteTickDataWrangler(instrument=instrument)
    catalog = ParquetDataCatalog(path=catalog_path.as_posix())

    existing_instruments = catalog.instruments(instrument_ids=[instrument.id.value])
    if not existing_instruments:
        catalog.write_data([instrument])

    stats = IngestStats()
    for day in iter_dates(args.from_date, args.to_date):
        path = daily_csv_path(raw_root, day)
        stats = stats.add(files_seen=1)

        if not path.exists():
            print(f"missing {path}")
            stats = stats.add(files_missing=1)
            continue

        frame = read_dukascopy_csv(path, volume_multiplier=args.volume_multiplier)
        if frame.empty:
            print(f"empty   {path}")
            stats = stats.add(files_empty=1)
            continue

        ticks = wrangler.process(frame, ts_init_delta=args.ts_init_delta)
        ticks.sort(key=lambda tick: tick.ts_init)

        interval = (ticks[0].ts_init, ticks[-1].ts_init)
        existing_intervals = catalog.get_intervals(QuoteTick, instrument.id.value)
        if interval in existing_intervals:
            print(f"exists  {path.name}: {len(ticks):,} QuoteTick")
            stats = stats.add(
                files_existing=1,
                rows_read=len(frame),
            )
            continue

        catalog.write_data(ticks)

        stats = stats.add(
            files_written=1,
            rows_read=len(frame),
            ticks_written=len(ticks),
        )
        print(
            f"wrote   {path.name}: {len(ticks):,} QuoteTick "
            f"({ticks[0].ts_event} -> {ticks[-1].ts_event})",
        )

    print()
    print(f"catalog:       {catalog_path}")
    print(f"instrument:    {instrument.id}")
    print(f"date range:    {args.from_date} <= day < {args.to_date}")
    print(f"files seen:    {stats.files_seen:,}")
    print(f"files missing: {stats.files_missing:,}")
    print(f"files empty:   {stats.files_empty:,}")
    print(f"files existing:{stats.files_existing:,}")
    print(f"files written: {stats.files_written:,}")
    print(f"rows read:     {stats.rows_read:,}")
    print(f"ticks written: {stats.ticks_written:,}")

    return stats


if __name__ == "__main__":
    ingest()
