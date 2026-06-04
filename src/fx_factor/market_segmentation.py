from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


SegmentationMethod = Literal["pelt", "binseg", "cusum", "zigzag", "hmm"]


DEFAULT_FEATURE_COLUMNS = (
    "log_return",
    "rolling_vol",
    "atr_close",
    "trend_slope_atr",
    "trend_r2",
    "efficiency_ratio",
    "adx_like",
    "donchian_width",
    "range_position",
)


@dataclass(frozen=True)
class MarketSegmentationParams:
    atr_lookback: int = 16
    rolling_vol_lookback: int = 24
    trend_lookback: int = 32
    donchian_lookback: int = 32
    efficiency_lookback: int = 24
    adx_lookback: int = 24
    feature_smooth_bars: int = 3
    min_segment_bars: int = 12
    pelt_penalty: float = 6.0
    pelt_jump: int = 2
    pelt_max_points: int = 6000
    binseg_max_segments: int = 96
    binseg_min_gain: float = 2.5
    cusum_threshold: float = 2.0
    cusum_window: int = 24
    zigzag_atr_multiple: float = 1.3
    zigzag_min_return: float = 0.0005
    hmm_states: int = 4
    boundary_score_window: int = 12

    @property
    def warmup_bars(self) -> int:
        return max(
            self.atr_lookback,
            self.rolling_vol_lookback,
            self.trend_lookback,
            self.donchian_lookback,
            self.efficiency_lookback,
            self.adx_lookback,
        )


def compute_segmentation_features(
    bars: pd.DataFrame,
    params: MarketSegmentationParams | None = None,
) -> pd.DataFrame:
    """Compute non-causal segmentation features from OHLC bars.

    The features describe market structure only. They intentionally do not assign
    bull/bear/range labels, so the output can be used as a hindsight target for
    later regime classifiers.
    """

    config = params or MarketSegmentationParams()
    frame = _normalize_bars(bars)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    log_close = np.log(close.mask(close <= 0.0))
    log_return = log_close.diff().replace([np.inf, -np.inf], np.nan)

    prev_close = close.shift(1).fillna(close)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(config.atr_lookback, min_periods=config.atr_lookback).mean()
    atr_safe = atr.mask(atr <= 0.0)
    rolling_vol = log_return.rolling(
        config.rolling_vol_lookback,
        min_periods=config.rolling_vol_lookback,
    ).std()

    trend_slope = close.rolling(
        config.trend_lookback,
        min_periods=config.trend_lookback,
    ).apply(_regression_slope, raw=True)
    trend_r2 = close.rolling(
        config.trend_lookback,
        min_periods=config.trend_lookback,
    ).apply(_regression_r2, raw=True)
    path = close.diff().abs().rolling(
        config.efficiency_lookback,
        min_periods=config.efficiency_lookback,
    ).sum()
    displacement = (close - close.shift(config.efficiency_lookback)).abs()
    efficiency_ratio = (displacement / path.mask(path <= 0.0)).clip(0.0, 1.0)

    rolling_high = high.rolling(
        config.donchian_lookback,
        min_periods=config.donchian_lookback,
    ).max()
    rolling_low = low.rolling(
        config.donchian_lookback,
        min_periods=config.donchian_lookback,
    ).min()
    donchian_range = rolling_high - rolling_low
    range_position = ((close - rolling_low) / donchian_range.mask(donchian_range <= 0.0)).clip(0.0, 1.0)

    features = pd.DataFrame(
        {
            "timestamp": frame["timestamp"],
            "open": frame["open"],
            "high": frame["high"],
            "low": frame["low"],
            "close": frame["close"],
            "log_return": log_return,
            "rolling_vol": rolling_vol,
            "atr": atr,
            "atr_close": atr / close.mask(close <= 0.0),
            "trend_slope_atr": trend_slope / atr_safe,
            "trend_r2": trend_r2,
            "efficiency_ratio": efficiency_ratio,
            "adx_like": _adx_like(high, low, true_range, config.adx_lookback),
            "donchian_width": donchian_range / close.mask(close <= 0.0),
            "range_position": range_position,
        },
    )
    if config.feature_smooth_bars > 1:
        smooth_columns = [column for column in DEFAULT_FEATURE_COLUMNS if column != "log_return"]
        features.loc[:, smooth_columns] = features.loc[:, smooth_columns].rolling(
            config.feature_smooth_bars,
            min_periods=1,
            center=True,
        ).median()
    return features


def compute_market_segmentation(
    bars: pd.DataFrame,
    method: SegmentationMethod = "pelt",
    params: MarketSegmentationParams | None = None,
    feature_columns: tuple[str, ...] = DEFAULT_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Return one offline market-structure segmentation for OHLC bars."""

    config = params or MarketSegmentationParams()
    features = compute_segmentation_features(bars, config)
    matrix, valid_positions = _feature_matrix(features, feature_columns)
    n = len(features)
    if n == 0:
        return _attach_segments(features, [], np.zeros(0, dtype=float), method, config)
    if len(valid_positions) < max(config.min_segment_bars * 2, 8):
        return _attach_segments(features, [], np.zeros(n, dtype=float), method, config)

    if method == "pelt":
        local_boundaries = _pelt_boundaries(matrix, config)
    elif method == "binseg":
        local_boundaries = _binseg_boundaries(matrix, config)
    elif method == "cusum":
        local_boundaries = _cusum_boundaries(matrix, config)
    elif method == "zigzag":
        local_boundaries = _zigzag_boundaries(features.iloc[valid_positions].reset_index(drop=True), config)
    elif method == "hmm":
        local_boundaries = _hmm_boundaries(matrix, config)
    else:
        raise ValueError(f"unknown segmentation method: {method}")

    boundaries = [int(valid_positions[index]) for index in local_boundaries if 0 < index < len(valid_positions)]
    boundaries = _filter_min_gap(sorted(set(boundaries)), n, config.min_segment_bars)
    scores = _boundary_scores(features, boundaries, feature_columns, config)
    return _attach_segments(features, boundaries, scores, method, config)


def compute_all_market_segmentations(
    bars: pd.DataFrame,
    params: MarketSegmentationParams | None = None,
    methods: tuple[SegmentationMethod, ...] = ("pelt", "binseg", "cusum", "zigzag", "hmm"),
    feature_columns: tuple[str, ...] = DEFAULT_FEATURE_COLUMNS,
) -> dict[str, pd.DataFrame]:
    """Compute comparable segmentations for several boundary detectors."""

    return {
        method: compute_market_segmentation(
            bars,
            method=method,
            params=params,
            feature_columns=feature_columns,
        )
        for method in methods
    }


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    if "timestamp" not in frame.columns:
        if "ts_event" in frame.columns:
            frame = frame.rename(columns={"ts_event": "timestamp"})
        elif "ts_init" in frame.columns:
            frame = frame.rename(columns={"ts_init": "timestamp"})

    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"bars are missing columns: {sorted(missing)}")

    frame = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)


def _feature_matrix(
    features: pd.DataFrame,
    feature_columns: tuple[str, ...],
) -> tuple[np.ndarray, np.ndarray]:
    missing = set(feature_columns) - set(features.columns)
    if missing:
        raise ValueError(f"features are missing columns: {sorted(missing)}")
    raw = features.loc[:, feature_columns].replace([np.inf, -np.inf], np.nan)
    valid = raw.notna().all(axis=1).to_numpy()
    positions = np.flatnonzero(valid)
    matrix = raw.loc[valid].to_numpy(dtype=np.float64)
    if matrix.size == 0:
        return matrix.reshape(0, len(feature_columns)), positions
    center = np.nanmedian(matrix, axis=0)
    spread = np.nanpercentile(matrix, 75, axis=0) - np.nanpercentile(matrix, 25, axis=0)
    spread = np.where(spread <= 1e-12, np.nanstd(matrix, axis=0), spread)
    spread = np.where(spread <= 1e-12, 1.0, spread)
    matrix = (matrix - center) / spread
    return np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0), positions


def _pelt_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    try:
        import ruptures as rpt  # type: ignore[import-not-found]
    except ImportError:
        return _fallback_pelt_boundaries(matrix, params)

    if len(matrix) < params.min_segment_bars * 2:
        return []
    model = rpt.Pelt(model="l2", min_size=params.min_segment_bars, jump=max(1, params.pelt_jump))
    result = model.fit(matrix).predict(pen=max(float(params.pelt_penalty), 1e-6))
    return [int(index) for index in result[:-1]]


def _fallback_pelt_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    n = len(matrix)
    min_size = max(int(params.min_segment_bars), 2)
    if n < min_size * 2:
        return []

    jump = max(int(np.ceil(n / params.pelt_max_points)), int(params.pelt_jump), 1)
    endpoints = np.arange(0, n + 1, jump, dtype=int)
    if endpoints[-1] != n:
        endpoints = np.r_[endpoints, n]
    endpoints = np.unique(endpoints)
    m = len(endpoints)
    prefix, prefix_sq = _prefix_sums(matrix)
    penalty = max(float(params.pelt_penalty), 1e-6)

    best = np.full(m, np.inf, dtype=np.float64)
    previous = np.full(m, -1, dtype=int)
    best[0] = -penalty
    candidates: list[int] = [0]

    for t_index in range(1, m):
        t = int(endpoints[t_index])
        feasible = [candidate for candidate in candidates if t - int(endpoints[candidate]) >= min_size]
        if not feasible:
            if t >= min_size:
                candidates.append(t_index)
            continue

        costs = np.asarray(
            [
                best[candidate]
                + _segment_l2_cost(prefix, prefix_sq, int(endpoints[candidate]), t)
                + penalty
                for candidate in feasible
            ],
            dtype=np.float64,
        )
        winner_offset = int(np.argmin(costs))
        winner = feasible[winner_offset]
        best[t_index] = float(costs[winner_offset])
        previous[t_index] = winner

        candidates = [
            candidate
            for candidate in candidates
            if t - int(endpoints[candidate]) < min_size
            or best[candidate] + _segment_l2_cost(prefix, prefix_sq, int(endpoints[candidate]), t) <= best[t_index] + penalty
        ]
        candidates.append(t_index)

    if not np.isfinite(best[-1]):
        return []
    boundary_indices: list[int] = []
    index = m - 1
    while previous[index] > 0:
        boundary_indices.append(int(endpoints[previous[index]]))
        index = int(previous[index])
    return sorted(boundary_indices)


def _binseg_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    try:
        import ruptures as rpt  # type: ignore[import-not-found]
    except ImportError:
        return _fallback_binseg_boundaries(matrix, params)

    if len(matrix) < params.min_segment_bars * 2:
        return []
    model = rpt.Binseg(model="l2", min_size=params.min_segment_bars, jump=max(1, params.pelt_jump))
    result = model.fit(matrix).predict(n_bkps=max(0, params.binseg_max_segments - 1))
    return [int(index) for index in result[:-1]]


def _fallback_binseg_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    n = len(matrix)
    min_size = max(int(params.min_segment_bars), 2)
    if n < min_size * 2:
        return []
    prefix, prefix_sq = _prefix_sums(matrix)
    segments = [(0, n)]
    boundaries: list[int] = []
    max_boundaries = max(int(params.binseg_max_segments) - 1, 0)

    while len(boundaries) < max_boundaries:
        best_gain = -np.inf
        best_split: tuple[int, int, int] | None = None
        for segment_index, (start, end) in enumerate(segments):
            split, gain = _best_split(prefix, prefix_sq, start, end, min_size, max(1, params.pelt_jump))
            if split is not None and gain > best_gain:
                best_gain = gain
                best_split = (segment_index, start, split)
        if best_split is None or best_gain < params.binseg_min_gain:
            break
        segment_index, start, split = best_split
        _, end = segments.pop(segment_index)
        segments.extend([(start, split), (split, end)])
        segments.sort()
        boundaries.append(split)
    return sorted(boundaries)


def _cusum_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    n = len(matrix)
    window = max(int(params.cusum_window), int(params.min_segment_bars), 4)
    if n < window * 2:
        return []
    scores = np.zeros(n, dtype=np.float64)
    for index in range(window, n - window):
        left = matrix[index - window : index].mean(axis=0)
        right = matrix[index : index + window].mean(axis=0)
        scores[index] = float(np.linalg.norm(right - left))
    threshold = max(float(params.cusum_threshold), float(np.nanmedian(scores) + 2.5 * np.nanstd(scores)))
    return _peak_indices(scores, threshold, params.min_segment_bars)


def _zigzag_boundaries(features: pd.DataFrame, params: MarketSegmentationParams) -> list[int]:
    close = features["close"].to_numpy(dtype=np.float64)
    atr = features["atr"].to_numpy(dtype=np.float64)
    if close.size < params.min_segment_bars * 2:
        return []

    boundaries: list[int] = []
    pivot_index = 0
    pivot_price = close[0]
    direction = 0
    extreme_index = 0
    extreme_price = close[0]
    for index in range(1, close.size):
        price = close[index]
        threshold = max(
            float(params.zigzag_min_return) * max(abs(pivot_price), 1e-12),
            float(params.zigzag_atr_multiple) * (atr[index] if np.isfinite(atr[index]) else 0.0),
        )
        if direction >= 0 and price >= extreme_price:
            extreme_price = price
            extreme_index = index
        if direction <= 0 and price <= extreme_price:
            extreme_price = price
            extreme_index = index

        move = price - pivot_price
        if direction == 0 and abs(move) >= threshold:
            direction = 1 if move > 0.0 else -1
            extreme_price = price
            extreme_index = index
            continue
        if direction > 0 and extreme_price - price >= threshold:
            if extreme_index - pivot_index >= params.min_segment_bars:
                boundaries.append(extreme_index)
                pivot_index = extreme_index
                pivot_price = extreme_price
            direction = -1
            extreme_price = price
            extreme_index = index
        elif direction < 0 and price - extreme_price >= threshold:
            if extreme_index - pivot_index >= params.min_segment_bars:
                boundaries.append(extreme_index)
                pivot_index = extreme_index
                pivot_price = extreme_price
            direction = 1
            extreme_price = price
            extreme_index = index
    return boundaries


def _hmm_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    try:
        from hmmlearn.hmm import GaussianHMM  # type: ignore[import-not-found]
    except ImportError:
        return _fallback_hmm_like_boundaries(matrix, params)

    n = len(matrix)
    if n < params.min_segment_bars * 2:
        return []
    states = max(2, int(params.hmm_states))
    model = GaussianHMM(n_components=states, covariance_type="diag", n_iter=200, random_state=7)
    labels = model.fit(matrix).predict(matrix)
    return _label_change_boundaries(labels, params.min_segment_bars)


def _fallback_hmm_like_boundaries(matrix: np.ndarray, params: MarketSegmentationParams) -> list[int]:
    if len(matrix) < params.min_segment_bars * 2:
        return []
    trend = matrix[:, 3] if matrix.shape[1] > 3 else matrix[:, 0]
    vol = matrix[:, 1] if matrix.shape[1] > 1 else np.abs(matrix[:, 0])
    trend_state = np.digitize(trend, np.nanquantile(trend, [0.33, 0.67]))
    vol_state = np.digitize(vol, np.nanquantile(vol, [0.50]))
    labels = trend_state + 3 * vol_state
    return _label_change_boundaries(labels, params.min_segment_bars)


def _label_change_boundaries(labels: np.ndarray, min_gap: int) -> list[int]:
    boundaries = [index for index in range(1, len(labels)) if labels[index] != labels[index - 1]]
    return _filter_min_gap(boundaries, len(labels), min_gap)


def _attach_segments(
    features: pd.DataFrame,
    boundaries: list[int],
    boundary_scores: np.ndarray,
    method: str,
    params: MarketSegmentationParams,
) -> pd.DataFrame:
    n = len(features)
    starts = [0, *boundaries]
    ends = [*boundaries, n]
    segment_id = np.zeros(n, dtype=int)
    segment_start = np.empty(n, dtype=object)
    segment_end = np.empty(n, dtype=object)
    segment_age = np.zeros(n, dtype=int)
    segment_length = np.zeros(n, dtype=int)

    timestamps = features["timestamp"].to_numpy()
    for sid, (start, end) in enumerate(zip(starts, ends), start=1):
        if end <= start:
            continue
        segment_id[start:end] = sid
        segment_start[start:end] = timestamps[start]
        segment_end[start:end] = timestamps[end - 1]
        segment_age[start:end] = np.arange(end - start, dtype=int)
        segment_length[start:end] = end - start

    is_boundary = np.zeros(n, dtype=bool)
    if boundaries:
        is_boundary[np.asarray(boundaries, dtype=int)] = True
    result = features.copy()
    result["segment_method"] = method
    result["segment_id"] = segment_id
    result["is_boundary"] = is_boundary
    result["segment_start"] = segment_start
    result["segment_end"] = segment_end
    result["segment_age"] = segment_age
    result["segment_length"] = segment_length
    result["boundary_score"] = boundary_scores
    result["segmentation_min_segment_bars"] = int(params.min_segment_bars)
    return result


def _boundary_scores(
    features: pd.DataFrame,
    boundaries: list[int],
    feature_columns: tuple[str, ...],
    params: MarketSegmentationParams,
) -> np.ndarray:
    scores = np.zeros(len(features), dtype=np.float64)
    if not boundaries:
        return scores
    matrix, valid_positions = _feature_matrix(features, feature_columns)
    position_to_local = {int(position): index for index, position in enumerate(valid_positions)}
    window = max(int(params.boundary_score_window), 4)
    for boundary in boundaries:
        local = position_to_local.get(int(boundary))
        if local is None:
            continue
        left = matrix[max(0, local - window) : local]
        right = matrix[local : min(len(matrix), local + window)]
        if len(left) < 2 or len(right) < 2:
            continue
        scores[boundary] = float(np.linalg.norm(right.mean(axis=0) - left.mean(axis=0)))
    return scores


def _filter_min_gap(boundaries: list[int], n: int, min_gap: int) -> list[int]:
    filtered: list[int] = []
    for boundary in sorted(boundaries):
        if boundary <= 0 or boundary >= n:
            continue
        if boundary < min_gap or n - boundary < min_gap:
            continue
        if filtered and boundary - filtered[-1] < min_gap:
            continue
        filtered.append(int(boundary))
    return filtered


def _prefix_sums(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    prefix = np.vstack([np.zeros((1, matrix.shape[1])), np.cumsum(matrix, axis=0)])
    prefix_sq = np.vstack([np.zeros((1, matrix.shape[1])), np.cumsum(matrix * matrix, axis=0)])
    return prefix, prefix_sq


def _segment_l2_cost(prefix: np.ndarray, prefix_sq: np.ndarray, start: int, end: int) -> float:
    length = max(end - start, 1)
    sums = prefix[end] - prefix[start]
    sums_sq = prefix_sq[end] - prefix_sq[start]
    return float(np.maximum(sums_sq - (sums * sums) / length, 0.0).sum())


def _best_split(
    prefix: np.ndarray,
    prefix_sq: np.ndarray,
    start: int,
    end: int,
    min_size: int,
    jump: int,
) -> tuple[int | None, float]:
    if end - start < min_size * 2:
        return None, -np.inf
    base_cost = _segment_l2_cost(prefix, prefix_sq, start, end)
    best_index: int | None = None
    best_gain = -np.inf
    for split in range(start + min_size, end - min_size + 1, jump):
        split_cost = _segment_l2_cost(prefix, prefix_sq, start, split)
        split_cost += _segment_l2_cost(prefix, prefix_sq, split, end)
        gain = base_cost - split_cost
        if gain > best_gain:
            best_gain = gain
            best_index = split
    return best_index, float(best_gain)


def _peak_indices(scores: np.ndarray, threshold: float, min_gap: int) -> list[int]:
    peaks: list[int] = []
    last = -min_gap
    for index in np.argsort(scores)[::-1]:
        index = int(index)
        if scores[index] < threshold:
            break
        if any(abs(index - peak) < min_gap for peak in peaks):
            continue
        if index - last < min_gap:
            continue
        peaks.append(index)
        last = index
    return sorted(peaks)


def _regression_slope(values: np.ndarray) -> float:
    y = np.asarray(values, dtype=np.float64)
    if y.size < 2 or np.any(~np.isfinite(y)):
        return np.nan
    x = np.arange(y.size, dtype=np.float64)
    x -= x.mean()
    y = y - y.mean()
    denom = float(np.dot(x, x))
    if denom <= 0.0:
        return np.nan
    return float(np.dot(x, y) / denom)


def _regression_r2(values: np.ndarray) -> float:
    y = np.asarray(values, dtype=np.float64)
    if y.size < 3 or np.any(~np.isfinite(y)):
        return np.nan
    x = np.arange(y.size, dtype=np.float64)
    x_centered = x - float(x.mean())
    y_centered = y - float(y.mean())
    denom = float(np.dot(x_centered, x_centered))
    total = float(np.dot(y_centered, y_centered))
    if denom <= 0.0 or total <= 0.0:
        return 0.0
    slope = float(np.dot(x_centered, y_centered) / denom)
    fitted = float(y.mean()) + slope * x_centered
    residual = y - fitted
    return float(np.clip(1.0 - float(np.dot(residual, residual)) / total, 0.0, 1.0))


def _adx_like(
    high: pd.Series,
    low: pd.Series,
    true_range: pd.Series,
    window: int,
) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=high.index,
    )
    tr_sum = true_range.rolling(window, min_periods=window).sum()
    plus_di = plus_dm.rolling(window, min_periods=window).sum() / tr_sum.mask(tr_sum <= 0.0)
    minus_di = minus_dm.rolling(window, min_periods=window).sum() / tr_sum.mask(tr_sum <= 0.0)
    denom = plus_di + minus_di
    return ((plus_di - minus_di).abs() / denom.mask(denom <= 0.0)).clip(0.0, 1.0)
