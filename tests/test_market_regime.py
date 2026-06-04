from __future__ import annotations

import unittest
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.market_regime import MarketRegimeParams
from fx_factor.market_regime import REGIME_NARROW_BEAR
from fx_factor.market_regime import REGIME_NARROW_BULL
from fx_factor.market_regime import REGIME_NARROW_RANGE
from fx_factor.market_regime import REGIME_WIDE_BEAR
from fx_factor.market_regime import REGIME_WIDE_BULL
from fx_factor.market_regime import REGIME_WIDE_RANGE
from fx_factor.market_regime import assign_online_regimes
from fx_factor.market_regime import compute_hindsight_segments
from fx_factor.market_regime import compute_market_regimes
from fx_factor.market_regime import compute_online_regimes


def bars_from_close(close: np.ndarray) -> pd.DataFrame:
    close = np.asarray(close, dtype=np.float64)
    timestamps = pd.date_range("2024-01-01", periods=close.size, freq="15min", tz="UTC")
    spread = np.maximum(0.00003, np.abs(np.diff(close, prepend=close[0])) * 0.35)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        },
    )


def dominant_regime(result: pd.DataFrame, warmup: int) -> str:
    tail = result.iloc[warmup + 80 :]
    counts = tail["online_confirmed_regime"].value_counts()
    return str(counts.index[0])


def feature_frame(
    trend: list[float],
    width: list[float],
    quality: list[float] | None = None,
    confidence: list[float] | None = None,
) -> pd.DataFrame:
    size = len(trend)
    return pd.DataFrame(
        {
            "trend_score": trend,
            "width_score": width,
            "trend_quality": quality if quality is not None else [0.9] * size,
            "online_confidence": confidence if confidence is not None else [1.0] * size,
        },
    )


class MarketRegimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.params = MarketRegimeParams(
            atr_lookback=48,
            trend_windows=(32, 96, 288),
            donchian_lookback=96,
            volatility_lookback=96,
            percentile_lookback=288,
            ema_fast=32,
            ema_slow=96,
            momentum_lookback=32,
            direction_balance_lookback=48,
            quality_lookback=96,
            boundary_ewma_span=96,
            hindsight_smooth_bars=32,
            hindsight_min_segment_bars=12,
            trend_enter_threshold=0.26,
            trend_exit_threshold=0.16,
            min_trend_quality=0.12,
            wide_width_threshold=0.55,
        )
        self.rng = np.random.default_rng(42)

    def classify(self, close: np.ndarray) -> tuple[str, pd.DataFrame]:
        result = compute_online_regimes(bars_from_close(close), self.params)
        return dominant_regime(result, self.params.warmup_bars), result

    def test_online_output_columns_and_legacy_aliases(self) -> None:
        close = 1.08 + np.arange(700) * 0.000018 + self.rng.normal(0.0, 0.000008, 700)
        result = compute_online_regimes(bars_from_close(close), self.params)
        expected_columns = {
            "online_raw_regime",
            "online_pending_regime",
            "online_confirmed_regime",
            "online_segment_id",
            "online_segment_age",
            "trend_score",
            "width_score",
            "trend_quality",
            "online_confidence",
        }
        self.assertTrue(expected_columns.issubset(result.columns))
        self.assertTrue(result["confirmed_regime"].equals(result["online_confirmed_regime"]))
        self.assertTrue(result["confidence"].equals(result["online_confidence"]))

    def test_narrow_bull(self) -> None:
        close = 1.08 + np.arange(700) * 0.000018 + self.rng.normal(0.0, 0.000008, 700)
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_NARROW_BULL)

    def test_wide_bull(self) -> None:
        close = (
            1.08
            + np.arange(700) * 0.000045
            + np.sin(np.arange(700) / 8.0) * 0.00045
            + self.rng.normal(0.0, 0.00004, 700)
        )
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_WIDE_BULL)

    def test_narrow_bear(self) -> None:
        close = 1.12 - np.arange(700) * 0.000018 + self.rng.normal(0.0, 0.000008, 700)
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_NARROW_BEAR)

    def test_wide_bear(self) -> None:
        close = (
            1.12
            - np.arange(700) * 0.000045
            + np.sin(np.arange(700) / 8.0) * 0.00045
            + self.rng.normal(0.0, 0.00004, 700)
        )
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_WIDE_BEAR)

    def test_wide_range(self) -> None:
        close = (
            1.10
            + np.sin(np.arange(700) / 11.0) * 0.00135
            + self.rng.normal(0.0, 0.00004, 700)
        )
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_WIDE_RANGE)

    def test_narrow_range(self) -> None:
        close = (
            1.10
            + np.sin(np.arange(700) / 17.0) * 0.00012
            + self.rng.normal(0.0, 0.000006, 700)
        )
        regime, _ = self.classify(close)
        self.assertEqual(regime, REGIME_NARROW_RANGE)

    def test_hindsight_segments_align_with_online_frame(self) -> None:
        close = 1.08 + np.arange(700) * 0.000018 + self.rng.normal(0.0, 0.000008, 700)
        bars = bars_from_close(close)
        online = compute_online_regimes(bars, self.params)
        hindsight = compute_hindsight_segments(bars, self.params, online=online)
        self.assertEqual(len(hindsight), len(online))
        self.assertIn("hindsight_regime", hindsight.columns)
        self.assertIn("hindsight_confidence", hindsight.columns)

    def test_compute_market_regimes_combines_online_and_hindsight(self) -> None:
        close = 1.08 + np.arange(700) * 0.000018 + self.rng.normal(0.0, 0.000008, 700)
        result = compute_market_regimes(bars_from_close(close), self.params)
        self.assertIn("online_confirmed_regime", result.columns)
        self.assertIn("hindsight_regime", result.columns)

    def test_online_outputs_do_not_change_when_future_is_removed(self) -> None:
        close = (
            1.09
            + np.sin(np.arange(780) / 19.0) * 0.00034
            + np.arange(780) * 0.000006
            + self.rng.normal(0.0, 0.000015, 780)
        )
        bars = bars_from_close(close)
        full = compute_online_regimes(bars, self.params)
        string_columns = [
            "online_raw_regime",
            "online_pending_regime",
            "online_confirmed_regime",
        ]
        numeric_columns = [
            "trend_score",
            "width_score",
            "trend_quality",
            "online_confidence",
            "online_segment_id",
            "online_segment_age",
        ]
        for cutoff in [360, 520, 779]:
            truncated = compute_online_regimes(bars.iloc[: cutoff + 1], self.params)
            for column in string_columns:
                self.assertTrue(
                    full.loc[:cutoff, column].reset_index(drop=True).equals(truncated[column]),
                    msg=f"{column} changed after truncating at {cutoff}",
                )
            np.testing.assert_allclose(
                full.loc[:cutoff, numeric_columns].to_numpy(dtype=np.float64),
                truncated[numeric_columns].to_numpy(dtype=np.float64),
                equal_nan=True,
            )

    def test_state_machine_requires_consecutive_confirmation(self) -> None:
        params = MarketRegimeParams(
            confirm_bars=3,
            trend_enter_threshold=0.30,
            trend_exit_threshold=0.20,
            min_trend_quality=0.20,
            wide_width_threshold=0.50,
        )
        features = feature_frame(
            trend=[0.0, 0.0, 0.0, 0.55, 0.55],
            width=[0.20, 0.20, 0.20, 0.20, 0.20],
        )
        labels = assign_online_regimes(features, params=params)
        self.assertEqual(labels.loc[2, "online_confirmed_regime"], REGIME_NARROW_RANGE)
        self.assertEqual(labels.loc[4, "online_pending_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[4, "online_confirmed_regime"], REGIME_NARROW_RANGE)

    def test_low_confidence_holds_then_degrades_to_range(self) -> None:
        params = MarketRegimeParams(
            confirm_bars=3,
            uncertain_hold_bars=2,
            trend_enter_threshold=0.30,
            trend_exit_threshold=0.20,
            min_trend_quality=0.20,
            min_confidence=0.30,
            wide_width_threshold=0.50,
        )
        features = feature_frame(
            trend=[0.55, 0.55, 0.55, 0.55, 0.55, 0.55],
            width=[0.20, 0.20, 0.20, 0.70, 0.70, 0.70],
            confidence=[1.0, 1.0, 1.0, 0.05, 0.05, 0.05],
        )
        labels = assign_online_regimes(features, params=params)
        self.assertEqual(labels.loc[2, "online_confirmed_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[3, "online_confirmed_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[4, "online_confirmed_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[5, "online_confirmed_regime"], REGIME_WIDE_RANGE)

    def test_hysteresis_keeps_existing_trend_near_boundary(self) -> None:
        params = MarketRegimeParams(
            confirm_bars=3,
            trend_enter_threshold=0.30,
            trend_exit_threshold=0.20,
            min_trend_quality=0.20,
            wide_width_threshold=0.50,
        )
        features = feature_frame(
            trend=[0.55, 0.55, 0.55, 0.24, 0.24, 0.24, 0.19, 0.19, 0.19],
            width=[0.20] * 9,
        )
        labels = assign_online_regimes(features, params=params)
        self.assertEqual(labels.loc[2, "online_confirmed_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[5, "online_confirmed_regime"], REGIME_NARROW_BULL)
        self.assertEqual(labels.loc[8, "online_confirmed_regime"], REGIME_NARROW_RANGE)


if __name__ == "__main__":
    unittest.main()
