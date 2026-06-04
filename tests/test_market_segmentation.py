from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fx_factor.market_segmentation import MarketSegmentationParams
from fx_factor.market_segmentation import compute_market_segmentation
from fx_factor.market_segmentation import compute_online_market_segmentation
from fx_factor.market_segmentation import compute_segmentation_features
from fx_factor.market_segmentation_eval import OnlineSegmentationAccuracyParams
from fx_factor.market_segmentation_eval import SegmentationEvalParams
from fx_factor.market_segmentation_eval import evaluate_market_segmentation
from fx_factor.market_segmentation_eval import evaluate_market_segmentations
from fx_factor.market_segmentation_eval import evaluate_online_segmentation_accuracy
from fx_factor.market_segmentation_eval import segment_summary


def synthetic_bars() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    parts = [
        1.0800 + np.arange(260) * 0.000020 + rng.normal(0.0, 0.000010, 260),
        1.0852 + np.sin(np.arange(260) / 7.0) * 0.00055 + rng.normal(0.0, 0.000045, 260),
        1.0852 - np.arange(260) * 0.000023 + rng.normal(0.0, 0.000012, 260),
    ]
    close = np.concatenate(parts)
    timestamps = pd.date_range("2024-01-01", periods=close.size, freq="15min", tz="UTC")
    open_ = np.r_[close[0], close[:-1]]
    spread = np.maximum(0.00003, np.abs(close - open_) * 0.45)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": np.maximum(open_, close) + spread,
            "low": np.minimum(open_, close) - spread,
            "close": close,
        },
    )


class MarketSegmentationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = synthetic_bars()
        self.params = MarketSegmentationParams(
            atr_lookback=24,
            rolling_vol_lookback=32,
            trend_lookback=48,
            donchian_lookback=48,
            efficiency_lookback=48,
            adx_lookback=48,
            min_segment_bars=80,
        )

    def test_features_include_requested_market_structure_columns(self) -> None:
        features = compute_segmentation_features(self.bars, self.params)
        expected = {
            "log_return",
            "rolling_vol",
            "atr_close",
            "trend_slope_atr",
            "trend_r2",
            "efficiency_ratio",
            "adx_like",
            "donchian_width",
            "range_position",
        }
        self.assertTrue(expected.issubset(features.columns))
        self.assertEqual(len(features), len(self.bars))

    def test_zigzag_segmentation_outputs_unified_columns(self) -> None:
        segmentation = compute_market_segmentation(self.bars, params=self.params)
        expected = {
            "segment_id",
            "is_boundary",
            "segment_start",
            "segment_end",
            "segment_age",
            "boundary_score",
        }
        self.assertTrue(expected.issubset(segmentation.columns))
        self.assertEqual(len(segmentation), len(self.bars))
        self.assertEqual(set(segmentation["segment_method"]), {"zigzag"})
        self.assertGreaterEqual(segmentation["segment_id"].nunique(), 2)
        self.assertGreaterEqual(int(segmentation["is_boundary"].sum()), 1)
        self.assertEqual(int(segmentation.groupby("segment_id")["segment_age"].first().max()), 0)

    def test_evaluation_scores_zigzag_segmentation(self) -> None:
        segmentation = compute_market_segmentation(self.bars, params=self.params)
        metrics = evaluate_market_segmentation(
            segmentation,
            SegmentationEvalParams(min_segment_bars=self.params.min_segment_bars),
        )
        evaluation = evaluate_market_segmentations({"zigzag": segmentation})
        self.assertEqual(metrics["method"], "zigzag")
        self.assertIn("quality_score", metrics)
        self.assertTrue(np.isfinite(float(metrics["quality_score"])))
        self.assertEqual(list(evaluation["method"]), ["zigzag"])

    def test_segment_summary_is_one_row_per_segment(self) -> None:
        segmentation = compute_market_segmentation(self.bars, params=self.params)
        summary = segment_summary(segmentation)
        metrics = evaluate_market_segmentation(
            segmentation,
            SegmentationEvalParams(min_segment_bars=self.params.min_segment_bars),
        )
        self.assertEqual(len(summary), int(metrics["segment_count"]))
        self.assertIn("log_return_sum", summary.columns)

    def test_online_zigzag_segmentation_outputs_causal_columns(self) -> None:
        online = compute_online_market_segmentation(self.bars, params=self.params)
        expected = {
            "online_segment_id",
            "online_is_boundary",
            "online_is_confirmed_boundary",
            "online_boundary_index",
            "online_confirm_index",
            "online_boundary_time",
            "online_confirm_time",
            "online_confirmation_lag_bars",
            "online_segment_age",
            "online_segment_length_so_far",
        }
        self.assertTrue(expected.issubset(online.columns))
        self.assertEqual(len(online), len(self.bars))
        self.assertEqual(set(online["segment_method"]), {"online_zigzag"})
        self.assertGreaterEqual(int(online["online_is_boundary"].sum()), 1)
        confirmed = online.loc[online["online_is_confirmed_boundary"]]
        self.assertFalse(confirmed.empty)
        self.assertTrue((confirmed["online_confirm_index"] >= confirmed["online_boundary_index"]).all())
        self.assertTrue((confirmed["online_confirmation_lag_bars"] >= 0.0).all())

    def test_online_accuracy_matches_hindsight_pivot_boundaries(self) -> None:
        hindsight = compute_market_segmentation(self.bars, params=self.params)
        online = compute_online_market_segmentation(self.bars, params=self.params)
        accuracy = evaluate_online_segmentation_accuracy(
            hindsight=hindsight,
            online=online,
            params=OnlineSegmentationAccuracyParams(
                min_segment_bars=self.params.min_segment_bars,
                tolerance_windows=(0, 1, 6),
            ),
        )
        row = accuracy.loc[accuracy["tolerance_bars"] == 0].iloc[0]
        self.assertEqual(int(row["matched_boundary_count"]), int(hindsight["is_boundary"].sum()))
        self.assertAlmostEqual(float(row["precision"]), 1.0)
        self.assertAlmostEqual(float(row["recall"]), 1.0)
        self.assertAlmostEqual(float(row["f1"]), 1.0)
        self.assertGreaterEqual(float(row["median_confirmation_lag_bars"]), 0.0)

    def test_online_outputs_do_not_change_when_future_is_removed(self) -> None:
        full = compute_online_market_segmentation(self.bars, params=self.params)
        columns = [
            "online_segment_id",
            "online_is_boundary",
            "online_is_confirmed_boundary",
            "online_boundary_index",
            "online_confirm_index",
            "online_confirmation_lag_bars",
        ]
        for cutoff in (260, 390, 520, 650):
            truncated = compute_online_market_segmentation(self.bars.iloc[: cutoff + 1], params=self.params)
            confirmed_positions = np.flatnonzero(truncated["online_is_confirmed_boundary"].to_numpy(dtype=bool))
            if confirmed_positions.size == 0:
                continue
            last_confirmed = int(confirmed_positions[-1])
            pd.testing.assert_frame_equal(
                full.loc[:last_confirmed, columns].reset_index(drop=True),
                truncated.loc[:last_confirmed, columns].reset_index(drop=True),
                check_dtype=False,
            )


if __name__ == "__main__":
    unittest.main()
