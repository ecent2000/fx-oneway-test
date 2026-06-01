# Regime Adaptive FX Timing

Strategy ID: `regime-adaptive-fx-timing`

This repository now keeps the core code for a 15-minute EUR/USD regime-adaptive timing strategy. The latest default is a conservative, short-biased configuration selected after 2023 failure attribution and 2023-2025 split validation.

## Current Defaults

```text
instrument_id = EUR/USD.SIM
bar_type = EUR/USD.SIM-15-MINUTE-MID-EXTERNAL
trade_direction = long_short
strict_long_filter = true
confirm_bars = 4
cooldown_bars = 4
min_regime_bars = 2
min_target_atr_mult = 0.8
range_structure_filter = true
disabled_entry_regimes = narrow_bear
entry_hours_utc = 12-15
```

The entry filters only block new entries. Existing positions still exit through the normal stop, regime-change, max-hold, and rule-based exit logic.

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
stop_atr_mult = 2.0
max_position_bars = 96
```

`min_target_atr_mult = 0.8` requires enough distance to the strategy's implied target before entry.

Quality filters:

```text
strict_long_filter = true
strict_long_trend_mult = 1.5
disabled_entry_regimes = narrow_bear
entry_hours_utc = 12-15
```

The current strategy remains configured as `long_short`, but the active opportunity set is strongly short-biased.

## Latest Validation Snapshot

The latest 9 split validation for the current defaults:

```text
2023_train_opt:      +836.37, 14 trades, PF 1.965
2023_validation:     +951.23,  8 trades, PF 5.229
2023_final_test:     -829.49, 12 trades, PF 0.249
2024_train_opt:      -877.12, 26 trades, PF 0.506
2024_validation:      -91.58,  7 trades, PF 0.768
2024_final_test:    +2083.03, 12 trades, PF 12.258
2025_train_opt:      +132.56, 15 trades, PF 1.103
2025_validation:     +491.30, 10 trades, PF 2.062
2025_final_test:    +1210.10,  9 trades, PF 6.128
```

Aggregate:

```text
total_pnl_usd = +3906.40
positions = 113
pnl_2023 = +958.11
pnl_2024 = +1114.33
pnl_2025 = +1833.96
median_win_rate = 57.14%
```

Latest 2024Q4 single backtest:

```text
start = 2024-10-01T00:00:00Z
end = 2025-01-01T00:00:00Z
positions = 12
pnl_usd = +2083.03
win_rate = 91.67%
profit_factor = 12.258
sharpe = 3.740
```

Stress snapshot for 2024Q4:

```text
baseline = +2083.03
prob_slippage_50 = +2070.03
fixed_fee_2usd = +2086.13
```

## Known Weaknesses

1. Trade count is much lower after filtering: 113 trades across the 9 validation splits.
2. 2023Q4 and 2024H1 remain weak segments.
3. After the session/regime filter, 2023 attribution still shows weak `wide_range` shorts:

```text
wide_range = -1057.75, 15 trades, winrate 26.67%
wide_bear = +2029.50, 20 trades, winrate 70.00%
```

The next research step should focus on whether `wide_range` needs stricter target-space or volatility conditions.

## Repro Commands

Single 2024Q4 backtest:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\02_regime_backtest.py --start 2024-10-01T00:00:00Z --end 2025-01-01T00:00:00Z
```

9 split validation:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\04_regime_validate_oos.py
```

2024Q4 stress test:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\06_regime_stress_test.py --start 2024-10-01T00:00:00Z --end 2025-01-01T00:00:00Z
```

Attribution:

```powershell
D:\fx-oneway-test\.venv\Scripts\python.exe scripts\07_regime_attribution.py --start 2023-01-01T00:00:00Z --end 2024-01-01T00:00:00Z
```
