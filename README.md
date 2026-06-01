# FX Oneway Test

Core strategy: `regime-adaptive-fx-timing`

This repo is now trimmed around the current EUR/USD 15-minute regime-adaptive timing strategy. Latest parameters, validation numbers, and research notes live in:

```text
docs/regime_adaptive_fx_timing.md
```

## Core Files

```text
src/fx_factor/strategies/regime_adaptive_fx.py  # Nautilus strategy
src/fx_factor/regime_backtest_runner.py         # shared backtest runner/defaults
scripts/02_regime_backtest.py                   # single backtest
scripts/03e_regime_balanced_scan.py             # balanced long/short scan
scripts/04_regime_validate_oos.py               # 9-split OOS validation
scripts/07_regime_attribution.py                # regime/session/hour/month attribution
mt5/RegimeAdaptiveFxTimingEA.mq5                # MT5 EA mirror
```

Data helpers are kept only for rebuilding local data:

```text
download_dukascopy_fx_monthly.js
scripts/01_ingest_dukascopy_daily.py
scripts/01b_build_bars.py
```

## Reproduce

Run a continuous 2023-2025 backtest:

```powershell
.\.venv\Scripts\python.exe scripts\02_regime_backtest.py --start 2023-01-01T00:00:00Z --end 2026-01-01T00:00:00Z
```

Run the 9-split OOS validation:

```powershell
.\.venv\Scripts\python.exe scripts\04_regime_validate_oos.py
```

Run the balanced parameter scan:

```powershell
.\.venv\Scripts\python.exe scripts\03e_regime_balanced_scan.py --strict-long-filter false
```

Generated market data and reports are intentionally ignored by git:

```text
data/raw/
data/catalog/
reports/
```
