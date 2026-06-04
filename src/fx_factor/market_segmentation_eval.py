from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fx_factor.market_segmentation import DEFAULT_FEATURE_COLUMNS


@dataclass(frozen=True)
class SegmentationEvalParams:
    min_segment_bars: int = 12
    boundary_window: int = 12
    forward_trend_window: int = 16
    feature_columns: tuple[str, ...] = DEFAULT_FEATURE_COLUMNS


@dataclass(frozen=True)
class OnlineSegmentationAccuracyParams:
    min_segment_bars: int = 12
    tolerance_windows: tuple[int, ...] = (0, 1, 3, 6, 12, 24)
    confirmation_mode: str = "pivot"


def evaluate_market_segmentation(
    segmentation: pd.DataFrame,
    params: SegmentationEvalParams | None = None,
) -> dict[str, float | int | str]:
    """Score a hindsight segmentation without using bull/bear/range labels."""

    config = params or SegmentationEvalParams()
    required = {"segment_id", "is_boundary", "close"}
    missing = required - set(segmentation.columns)
    if missing:
        raise ValueError(f"segmentation is missing columns: {sorted(missing)}")

    frame = segmentation.copy()
    method = str(frame["segment_method"].iloc[0]) if "segment_method" in frame.columns and not frame.empty else "unknown"
    segment_lengths = frame.groupby("segment_id", sort=True).size().to_numpy(dtype=np.float64)
    segment_count = int(segment_lengths.size)
    boundaries = np.flatnonzero(frame["is_boundary"].fillna(False).to_numpy(dtype=bool))

    metrics: dict[str, float | int | str] = {
        "method": method,
        "bar_count": int(len(frame)),
        "segment_count": segment_count,
        "boundary_count": int(len(boundaries)),
        "average_segment_length": _safe_mean(segment_lengths),
        "median_segment_length": _safe_median(segment_lengths),
        "too_short_segment_ratio": _safe_mean(segment_lengths < config.min_segment_bars),
    }
    metrics["within_segment_stability"] = _within_segment_stability(frame, config.feature_columns)
    metrics["between_segment_difference"] = _between_segment_difference(frame, config.feature_columns)
    metrics["boundary_return_shift"] = _boundary_shift(frame, boundaries, "log_return", config.boundary_window)
    metrics["boundary_vol_shift"] = _boundary_shift(frame, boundaries, "rolling_vol", config.boundary_window)
    metrics["forward_trend_clarity"] = _forward_trend_clarity(frame, config.forward_trend_window)
    metrics["quality_score"] = _quality_score(metrics)
    return metrics


def evaluate_market_segmentations(
    segmentations: dict[str, pd.DataFrame],
    params: SegmentationEvalParams | None = None,
) -> pd.DataFrame:
    """Evaluate one or more segmentation outputs and return a quality-ranked table."""

    rows = [evaluate_market_segmentation(frame, params) for frame in segmentations.values()]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["quality_score", "between_segment_difference"], ascending=False).reset_index(drop=True)


def evaluate_online_segmentation_accuracy(
    *,
    hindsight: pd.DataFrame,
    online: pd.DataFrame,
    params: OnlineSegmentationAccuracyParams | None = None,
) -> pd.DataFrame:
    """Compare causal online boundaries against hindsight boundaries."""

    config = params or OnlineSegmentationAccuracyParams()
    if config.confirmation_mode != "pivot":
        raise ValueError("only pivot confirmation_mode is supported")
    required_hindsight = {"is_boundary"}
    required_online = {"online_is_boundary", "online_confirmation_lag_bars", "online_segment_id"}
    missing_hindsight = required_hindsight - set(hindsight.columns)
    missing_online = required_online - set(online.columns)
    if missing_hindsight:
        raise ValueError(f"hindsight segmentation is missing columns: {sorted(missing_hindsight)}")
    if missing_online:
        raise ValueError(f"online segmentation is missing columns: {sorted(missing_online)}")
    if len(hindsight) != len(online):
        raise ValueError("hindsight and online segmentations must have the same length")

    truth = np.flatnonzero(hindsight["is_boundary"].fillna(False).to_numpy(dtype=bool))
    predicted = np.flatnonzero(online["online_is_boundary"].fillna(False).to_numpy(dtype=bool))
    lags = pd.to_numeric(online["online_confirmation_lag_bars"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    segment_lengths = online.groupby("online_segment_id", sort=True).size().to_numpy(dtype=np.float64)
    online_avg = _safe_mean(segment_lengths)
    hindsight_lengths = hindsight.groupby("segment_id", sort=True).size().to_numpy(dtype=np.float64)
    hindsight_avg = _safe_mean(hindsight_lengths)
    avg_diff_ratio = abs(online_avg - hindsight_avg) / hindsight_avg if hindsight_avg > 0.0 else 0.0

    rows = []
    for tolerance in config.tolerance_windows:
        matched = _match_boundary_count(predicted, truth, int(tolerance))
        precision = matched / len(predicted) if len(predicted) else 0.0
        recall = matched / len(truth) if len(truth) else 0.0
        f1 = 2.0 * precision * recall / (precision + recall) if precision + recall > 0.0 else 0.0
        rows.append(
            {
                "confirmation_mode": config.confirmation_mode,
                "tolerance_bars": int(tolerance),
                "hindsight_boundary_count": int(len(truth)),
                "online_boundary_count": int(len(predicted)),
                "matched_boundary_count": int(matched),
                "false_positive_count": int(len(predicted) - matched),
                "false_negative_count": int(len(truth) - matched),
                "precision": float(precision),
                "recall": float(recall),
                "f1": float(f1),
                "mean_confirmation_lag_bars": _safe_mean(lags),
                "median_confirmation_lag_bars": _safe_median(lags),
                "p90_confirmation_lag_bars": _safe_percentile(lags, 90.0),
                "lag_le_3_bars_ratio": _safe_mean(lags <= 3.0),
                "lag_le_6_bars_ratio": _safe_mean(lags <= 6.0),
                "lag_le_12_bars_ratio": _safe_mean(lags <= 12.0),
                "online_segment_count": int(segment_lengths.size),
                "online_average_segment_length": online_avg,
                "online_median_segment_length": _safe_median(segment_lengths),
                "hindsight_average_segment_length": hindsight_avg,
                "average_segment_length_diff_ratio": float(avg_diff_ratio),
                "online_too_short_segment_ratio": _safe_mean(segment_lengths < config.min_segment_bars),
            },
        )
    return pd.DataFrame(rows)


def segment_summary(segmentation: pd.DataFrame) -> pd.DataFrame:
    """Return one row per segment for diagnostics and visualization tables."""

    required = {"segment_id", "timestamp", "close"}
    missing = required - set(segmentation.columns)
    if missing:
        raise ValueError(f"segmentation is missing columns: {sorted(missing)}")

    rows: list[dict[str, float | int | str]] = []
    for segment_id, group in segmentation.groupby("segment_id", sort=True):
        close = pd.to_numeric(group["close"], errors="coerce")
        log_return = np.log(close.mask(close <= 0.0)).diff()
        rows.append(
            {
                "segment_id": int(segment_id),
                "start": str(group["timestamp"].iloc[0]),
                "end": str(group["timestamp"].iloc[-1]),
                "bars": int(len(group)),
                "close_start": float(close.iloc[0]),
                "close_end": float(close.iloc[-1]),
                "log_return_sum": float(log_return.sum(skipna=True)),
                "return_vol": float(log_return.std(skipna=True)),
                "mean_boundary_score": float(pd.to_numeric(group.get("boundary_score", 0.0), errors="coerce").mean()),
            },
        )
    return pd.DataFrame(rows)


def _within_segment_stability(frame: pd.DataFrame, feature_columns: tuple[str, ...]) -> float:
    columns = [column for column in feature_columns if column in frame.columns]
    if not columns or frame.empty:
        return 0.0
    values = frame.loc[:, columns].replace([np.inf, -np.inf], np.nan)
    global_scale = values.std(skipna=True).replace(0.0, np.nan)
    segment_stds: list[float] = []
    weights: list[int] = []
    for _, group in frame.groupby("segment_id", sort=True):
        std = group.loc[:, columns].replace([np.inf, -np.inf], np.nan).std(skipna=True)
        normalized = (std / global_scale).replace([np.inf, -np.inf], np.nan)
        segment_stds.append(float(normalized.mean(skipna=True)))
        weights.append(int(len(group)))
    if not segment_stds or np.nansum(weights) <= 0:
        return 0.0
    weighted_std = float(np.average(np.nan_to_num(segment_stds, nan=0.0), weights=weights))
    return float(1.0 / (1.0 + max(weighted_std, 0.0)))


def _between_segment_difference(frame: pd.DataFrame, feature_columns: tuple[str, ...]) -> float:
    columns = [column for column in feature_columns if column in frame.columns]
    if not columns or frame["segment_id"].nunique() <= 1:
        return 0.0
    values = frame.loc[:, columns].replace([np.inf, -np.inf], np.nan)
    global_scale = values.std(skipna=True).replace(0.0, np.nan)
    means = []
    for _, group in frame.groupby("segment_id", sort=True):
        means.append(group.loc[:, columns].replace([np.inf, -np.inf], np.nan).mean(skipna=True))
    diffs: list[float] = []
    for left, right in zip(means, means[1:]):
        normalized = ((right - left).abs() / global_scale).replace([np.inf, -np.inf], np.nan)
        diffs.append(float(normalized.mean(skipna=True)))
    return _safe_mean(np.asarray(diffs, dtype=np.float64))


def _boundary_shift(frame: pd.DataFrame, boundaries: np.ndarray, column: str, window: int) -> float:
    if column not in frame.columns or boundaries.size == 0:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan).to_numpy(dtype=np.float64)
    shifts: list[float] = []
    for boundary in boundaries:
        left = values[max(0, int(boundary) - window) : int(boundary)]
        right = values[int(boundary) : min(len(values), int(boundary) + window)]
        left = left[np.isfinite(left)]
        right = right[np.isfinite(right)]
        if left.size < 2 or right.size < 2:
            continue
        scale = float(np.nanstd(np.r_[left, right]))
        scale = scale if scale > 1e-12 else 1.0
        shifts.append(abs(float(np.nanmean(right) - np.nanmean(left))) / scale)
    return _safe_mean(np.asarray(shifts, dtype=np.float64))


def _forward_trend_clarity(frame: pd.DataFrame, window: int) -> float:
    close = pd.to_numeric(frame["close"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if len(close) < max(window, 2):
        return 0.0
    future_return = np.log(close.shift(-window) / close.mask(close <= 0.0)).replace([np.inf, -np.inf], np.nan)
    clarity: list[float] = []
    weights: list[int] = []
    for _, group in frame.assign(_future_return=future_return).groupby("segment_id", sort=True):
        values = group["_future_return"].dropna().to_numpy(dtype=np.float64)
        if values.size < 3:
            continue
        mean_abs = abs(float(np.mean(values)))
        noise = float(np.std(values))
        clarity.append(mean_abs / (noise + 1e-12))
        weights.append(int(values.size))
    if not clarity:
        return 0.0
    return float(np.average(np.asarray(clarity, dtype=np.float64), weights=np.asarray(weights, dtype=np.float64)))


def _quality_score(metrics: dict[str, float | int | str]) -> float:
    stability = float(metrics.get("within_segment_stability", 0.0))
    difference = float(metrics.get("between_segment_difference", 0.0))
    return_shift = float(metrics.get("boundary_return_shift", 0.0))
    vol_shift = float(metrics.get("boundary_vol_shift", 0.0))
    clarity = float(metrics.get("forward_trend_clarity", 0.0))
    short_penalty = float(metrics.get("too_short_segment_ratio", 0.0))
    count = float(metrics.get("segment_count", 0.0))
    count_penalty = 0.0 if 12.0 <= count <= 180.0 else min(abs(count - 72.0) / 180.0, 1.0)
    score = (
        0.24 * stability
        + 0.28 * _squash(difference)
        + 0.14 * _squash(return_shift)
        + 0.14 * _squash(vol_shift)
        + 0.20 * _squash(clarity)
    )
    score *= max(0.0, 1.0 - 0.60 * short_penalty)
    score *= max(0.0, 1.0 - 0.25 * count_penalty)
    return float(score)


def _squash(value: float) -> float:
    value = max(float(value), 0.0)
    return float(value / (1.0 + value))


def _safe_mean(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    return float(values.mean())


def _safe_median(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    return float(np.median(values))


def _safe_percentile(values: np.ndarray, percentile: float) -> float:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    return float(np.percentile(values, percentile))


def _match_boundary_count(predicted: np.ndarray, truth: np.ndarray, tolerance: int) -> int:
    used = np.zeros(len(truth), dtype=bool)
    matched = 0
    for boundary in predicted:
        left = np.searchsorted(truth, int(boundary) - tolerance)
        right = np.searchsorted(truth, int(boundary) + tolerance, side="right")
        candidates = [index for index in range(left, right) if not used[index]]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda index: abs(int(truth[index]) - int(boundary)))
        used[nearest] = True
        matched += 1
    return matched
