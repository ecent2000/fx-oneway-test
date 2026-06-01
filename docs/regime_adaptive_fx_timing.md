# Regime Adaptive FX Timing

Strategy ID: `regime-adaptive-fx-timing`

This repository keeps the core code for a 15-minute EUR/USD regime-adaptive timing strategy. The current default is a long/short balanced configuration selected from direction baselines, attribution, and a constrained multi-objective scan over 2023-2025.

## Current Defaults

```text
instrument_id = EUR/USD.SIM
bar_type = EUR/USD.SIM-15-MINUTE-MID-EXTERNAL
trade_direction = long_short
strict_long_filter = false
confirm_bars = 4
long_confirm_bars = 4
short_confirm_bars = 4
cooldown_bars = 4
min_regime_bars = 2
long_enabled_regimes = wide_bull
short_enabled_regimes = wide_bear
long_entry_hours_utc = 6-8
short_entry_hours_utc = 13-15
long_min_target_atr_mult = 0.8
short_min_target_atr_mult = 0.8
long_stop_atr_mult = 1.5
short_stop_atr_mult = 1.5
range_structure_filter = true
disabled_entry_regimes =
entry_hours_utc =
```

The legacy `disabled_entry_regimes`, `entry_hours_utc`, `min_target_atr_mult`, `stop_atr_mult`, and `confirm_bars` parameters remain supported as fallbacks when side-specific settings are left blank or non-positive. Entry filters only block new entries. Existing positions still exit through the normal stop, regime-change, max-hold, and rule-based exit logic.

## Parameter Interpretation

Core factor windows:

```text
channel_lookback = 96
atr_lookback = 48
trend_lookback = 96
momentum_lookback = 24
breakout_lookback = 48
short_ma_lookback = 16
```

These are calibrated for 15-minute bars. `channel_lookback` and `trend_lookback` cover roughly one trading day, while `atr_lookback` and `breakout_lookback` cover about half a day.

Regime thresholds:

```text
bull_threshold = 0.03
bear_threshold = 0.03
flat_threshold = 0.01
narrow_width_threshold = 8.0
wide_range_threshold = 10.0
```

Trend slope is normalized by ATR. Width score is channel width divided by ATR.

Entry and target controls:

```text
momentum_entry = 0.0003
pullback_buy_zone = 0.33
pullback_sell_zone = 0.67
lower_third = 0.33
upper_third = 0.67
max_position_bars = 96
```

The new default uses only `wide_bull` longs during 06:00-08:59 UTC and only `wide_bear` shorts during 13:00-15:59 UTC. The side-specific target-space filter requires at least `0.8 * ATR` of room to the implied target before entry, and both sides use a `1.5 * ATR` stop.

## Research Notes

Default direction baseline before this change:

```text
2023-2025 long_only:   0 trades, PnL +0.00
2023-2025 short_only: 115 trades, PnL +4143.36, win_rate 54.78%, PF 1.62
```

The old defaults were effectively short-only because the strict long filter plus global `12-15` UTC entry window produced no long trades.

Long diagnostics:

```text
strict long filter, no global filters:  59 trades, PnL -73.45, win_rate 35.59%, PF 0.94
non-strict long filter, no global filters: 490 trades, PnL +883.09, win_rate 40.00%, PF 1.04
```

Attribution showed no broad long edge, but `wide_bull` during 06:00-08:59 UTC produced enough quality after constrained scanning to include as the long sleeve.

Short diagnostics:

```text
wide_bear shorts:   77 trades, PnL +4225.64, win_rate 62.34%, PF 2.07
wide_range shorts:  38 trades, PnL -82.28,  win_rate 39.47%, PF 0.97
```

The new default disables `wide_range` shorts and keeps `wide_bear` shorts only. This avoids carrying a weak range-short sleeve into the balanced version.

## Latest Validation Snapshot

Latest 9 split validation for the current defaults:

```text
2023_train_opt:     +1663.74, 16 trades, WR 81.25%, PF 3.871
2023_validation:     +439.38, 11 trades, WR 45.45%, PF 1.935
2023_final_test:     +477.56,  8 trades, WR 75.00%, PF 3.674
2024_train_opt:      -369.97, 19 trades, WR 42.11%, PF 0.686
2024_validation:     +465.21, 10 trades, WR 50.00%, PF 2.885
2024_final_test:     +485.20, 11 trades, WR 63.64%, PF 2.086
2025_train_opt:       -23.25, 14 trades, WR 57.14%, PF 0.961
2025_validation:     -380.77, 10 trades, WR 40.00%, PF 0.581
2025_final_test:    +1174.80, 11 trades, WR 54.55%, PF 5.161
```

Aggregate 9 split OOS:

```text
total_pnl_usd = +3931.90
positions = 110
long_trades = 46
short_trades = 64
long_share = 41.82%
long_pnl_usd = +1307.01
short_pnl_usd = +2624.89
long_win_rate = 54.35%
short_win_rate = 57.81%
long_profit_factor = 1.737
short_profit_factor = 1.840
long_worst_split_drawdown_usd = -493.06
short_worst_split_drawdown_usd = -623.28
```

Continuous 2023-2025 default backtest without split resets:

```text
positions = 111
pnl_usd = +3945.54
win_rate = 56.76%
profit_factor = 1.807
long_trades = 46, long_pnl = +1307.01, long_win_rate = 54.35%, long_pf = 1.737
short_trades = 65, short_pnl = +2638.53, short_win_rate = 58.46%, short_pf = 1.844
```

## Known Weaknesses

1. The long sleeve is narrow: it relies on `wide_bull` during 07:00-09:59 UTC, not a broad long edge.
2. 2024H1 remains weak at -369.97.
3. 2025Q3 remains weak, mainly from the long sleeve: long -493.06, short +112.29.
4. The short sleeve still contributes most PnL, although trade count is now within the target 40/60 to 60/40 band.

## Repro Commands

Single 2023-2025 continuous backtest:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\02_regime_backtest.py --start 2023-01-01T00:00:00Z --end 2026-01-01T00:00:00Z
```

9 split validation:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\04_regime_validate_oos.py
```

Balanced scan:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\03e_regime_balanced_scan.py --strict-long-filter false
```

Attribution:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\07_regime_attribution.py --start 2023-01-01T00:00:00Z --end 2026-01-01T00:00:00Z
```
