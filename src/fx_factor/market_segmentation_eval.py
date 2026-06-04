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
    """Evaluate several methods and return a quality-ranked table."""

    rows = [evaluate_market_segmentation(frame, params) for frame in segmentations.values()]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["quality_score", "between_segment_difference"], ascending=False).reset_index(drop=True)


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
