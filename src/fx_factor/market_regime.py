from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


REGIME_UNKNOWN = "unknown"
REGIME_NARROW_BULL = "narrow_bull"
REGIME_NARROW_BEAR = "narrow_bear"
REGIME_WIDE_BULL = "wide_bull"
REGIME_WIDE_BEAR = "wide_bear"
REGIME_WIDE_RANGE = "wide_range"
REGIME_NARROW_RANGE = "narrow_range"

BULL_REGIMES = frozenset({REGIME_WIDE_BULL, REGIME_NARROW_BULL})
BEAR_REGIMES = frozenset({REGIME_WIDE_BEAR, REGIME_NARROW_BEAR})
ALL_MARKET_REGIMES = frozenset(
    {
        REGIME_UNKNOWN,
        REGIME_NARROW_BULL,
        REGIME_NARROW_BEAR,
        REGIME_WIDE_BULL,
        REGIME_WIDE_BEAR,
        REGIME_WIDE_RANGE,
        REGIME_NARROW_RANGE,
    },
)


@dataclass(frozen=True)
class MarketRegimeParams:
    atr_lookback: int = 48
    trend_windows: tuple[int, int, int] = (32, 96, 288)
    donchian_lookback: int = 96
    volatility_lookback: int = 96
    percentile_lookback: int = 288
    ema_fast: int = 32
    ema_slow: int = 96
    momentum_lookback: int = 32
    direction_balance_lookback: int = 48
    quality_lookback: int = 96
    confirm_bars: int = 4
    uncertain_hold_bars: int = 8
    trend_enter_threshold: float = 0.34
    trend_exit_threshold: float = 0.20
    min_trend_quality: float = 0.20
    min_confidence: float = 0.12
    wide_width_threshold: float = 0.56
    boundary_ewma_span: int = 96
    boundary_cusum_drift: float = 0.08
    boundary_cusum_threshold: float = 0.55
    boundary_min_gap_bars: int = 8
    hindsight_smooth_bars: int = 32
    hindsight_min_segment_bars: int = 12

    @property
    def warmup_bars(self) -> int:
        return max(
            self.atr_lookback,
            max(self.trend_windows),
            self.donchian_lookback,
            self.volatility_lookback,
            self.percentile_lookback,
            self.ema_slow,
            self.momentum_lookback,
            self.direction_balance_lookback,
            self.quality_lookback,
        )


def compute_online_regimes(
    bars: pd.DataFrame,
    params: MarketRegimeParams | None = None,
) -> pd.DataFrame:
    """Compute causal EUR/USD market-regime labels from OHLC bars."""

    config = params or MarketRegimeParams()
    features = compute_causal_features(bars, config)
    boundaries = detect_online_boundaries(features, config)
    labels = assign_online_regimes(features, boundaries, config)
    return pd.concat(
        [
            features.reset_index(drop=True),
            boundaries.reset_index(drop=True),
            labels.reset_index(drop=True),
        ],
        axis=1,
    )


def compute_causal_features(
    bars: pd.DataFrame,
    params: MarketRegimeParams | None = None,
) -> pd.DataFrame:
    """Compute causal regime features using only bars up to each timestamp."""

    config = params or MarketRegimeParams()
    frame = _normalize_bars(bars)
    factors = _compute_factors(frame, config)
    return pd.concat([frame.reset_index(drop=True), factors], axis=1)


def detect_online_boundaries(
    features: pd.DataFrame,
    params: MarketRegimeParams | None = None,
) -> pd.DataFrame:
    """Flag causal EWMA/CUSUM drift events as online regime-boundary candidates."""

    config = params or MarketRegimeParams()
    required = {"trend_score", "width_score", "trend_quality"}
    missing = required - set(features.columns)
    if missing:
        raise ValueError(f"features are missing columns: {sorted(missing)}")

    trend = pd.to_numeric(features["trend_score"], errors="coerce").astype(float)
    width = pd.to_numeric(features["width_score"], errors="coerce").astype(float)
    quality = pd.to_numeric(features["trend_quality"], errors="coerce").astype(float)
    signal = (
        0.62 * trend
        + 0.23 * (width.clip(0.0, 1.0) - 0.5) * 2.0
        + 0.15 * (quality.clip(0.0, 1.0) - 0.5) * 2.0
    )

    span = max(int(config.boundary_ewma_span), 2)
    alpha = 2.0 / (span + 1.0)
    drift_allowance = max(float(config.boundary_cusum_drift), 0.0)
    threshold = max(float(config.boundary_cusum_threshold), 1e-12)
    min_gap = max(int(config.boundary_min_gap_bars), 0)

    baselines: list[float] = []
    drifts: list[float] = []
    scores: list[float] = []
    boundaries: list[bool] = []
    directions: list[int] = []
    positive_cusum = 0.0
    negative_cusum = 0.0
    baseline = np.nan
    last_boundary_index = -min_gap - 1

    for index, value in enumerate(signal.to_numpy(dtype=np.float64)):
        if not np.isfinite(value):
            baselines.append(np.nan)
            drifts.append(np.nan)
            scores.append(0.0)
            boundaries.append(False)
            directions.append(0)
            continue

        if not np.isfinite(baseline):
            baseline = float(value)
            baselines.append(baseline)
            drifts.append(0.0)
            scores.append(0.0)
            boundaries.append(False)
            directions.append(0)
            continue

        drift = float(value - baseline)
        positive_cusum = max(0.0, positive_cusum + drift - drift_allowance)
        negative_cusum = min(0.0, negative_cusum + drift + drift_allowance)
        score = max(positive_cusum, -negative_cusum)
        is_boundary = score >= threshold and index - last_boundary_index >= min_gap
        direction = 0
        if is_boundary:
            direction = 1 if positive_cusum >= -negative_cusum else -1
            positive_cusum = 0.0
            negative_cusum = 0.0
            last_boundary_index = index

        baselines.append(baseline)
        drifts.append(drift)
        scores.append(score)
        boundaries.append(is_boundary)
        directions.append(direction)
        baseline = baseline + alpha * drift

    return pd.DataFrame(
        {
            "online_boundary": boundaries,
            "online_boundary_direction": directions,
            "online_boundary_score": scores,
            "online_signal_baseline": baselines,
            "online_signal_drift": drifts,
        },
    )


def assign_online_regimes(
    features: pd.DataFrame,
    boundaries: pd.DataFrame | None = None,
    params: MarketRegimeParams | None = None,
) -> pd.DataFrame:
    """Assign causal raw, pending, and confirmed online regimes."""

    config = params or MarketRegimeParams()
    feature_frame = features.copy()
    if "online_confidence" not in feature_frame.columns and "confidence" in feature_frame.columns:
        feature_frame["online_confidence"] = feature_frame["confidence"]

    required = {"trend_score", "width_score", "trend_quality", "online_confidence"}
    missing = required - set(feature_frame.columns)
    if missing:
        raise ValueError(f"features are missing columns: {sorted(missing)}")
    boundary_frame = boundaries if boundaries is not None else detect_online_boundaries(feature_frame, config)
    if len(boundary_frame) != len(feature_frame):
        raise ValueError("boundaries length must match features length")
    return _confirm_online_regimes(feature_frame, boundary_frame, config)


def compute_market_regimes(
    bars: pd.DataFrame,
    params: MarketRegimeParams | None = None,
) -> pd.DataFrame:
    """Compute online regimes plus non-causal hindsight reference labels."""

    config = params or MarketRegimeParams()
    online = compute_online_regimes(bars, config)
    hindsight = compute_hindsight_segments(bars, config, online=online)
    return online.merge(hindsight, on="timestamp", how="left")


def compute_hindsight_segments(
    bars: pd.DataFrame,
    params: MarketRegimeParams | None = None,
    online: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute non-causal reference segments for visual calibration only."""

    config = params or MarketRegimeParams()
    online_frame = online if online is not None else compute_online_regimes(bars, config)
    required = {"timestamp", "trend_score", "width_score", "trend_quality"}
    missing = required - set(online_frame.columns)
    if missing:
        raise ValueError(f"online frame is missing columns: {sorted(missing)}")

    smooth = max(int(config.hindsight_smooth_bars), 1)
    trend = (
        online_frame["trend_score"]
        .rolling(smooth, min_periods=max(2, smooth // 3), center=True)
        .median()
    )
    width = (
        online_frame["width_score"]
        .rolling(smooth, min_periods=max(2, smooth // 3), center=True)
        .median()
    )
    quality = (
        online_frame["trend_quality"]
        .rolling(smooth, min_periods=max(2, smooth // 3), center=True)
        .median()
    )

    raw = [
        _classify_from_scores(
            trend_score=trend_value,
            width_score=width_value,
            trend_quality=quality_value,
            threshold=config.trend_exit_threshold,
            min_quality=config.min_trend_quality * 0.85,
            wide_threshold=config.wide_width_threshold,
        )
        for trend_value, width_value, quality_value in zip(trend, width, quality)
    ]
    merged = _merge_short_segments(raw, config.hindsight_min_segment_bars)
    segment_ids = _segment_ids(merged)
    confidence = _hindsight_confidence(
        regimes=merged,
        trend=trend,
        width=width,
        quality=quality,
        params=config,
    )
    return pd.DataFrame(
        {
            "timestamp": online_frame["timestamp"].to_numpy(),
            "hindsight_regime": merged,
            "hindsight_segment_id": segment_ids,
            "hindsight_confidence": confidence,
        },
    )


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    rename_map = {}
    if "timestamp" not in frame.columns:
        if "ts_event" in frame.columns:
            rename_map["ts_event"] = "timestamp"
        elif "ts_init" in frame.columns:
            rename_map["ts_init"] = "timestamp"
    if rename_map:
        frame = frame.rename(columns=rename_map)

    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"bars are missing columns: {sorted(missing)}")

    frame = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    return frame.sort_values("timestamp").reset_index(drop=True)


def _compute_factors(frame: pd.DataFrame, params: MarketRegimeParams) -> pd.DataFrame:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1).fillna(close)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(params.atr_lookback, min_periods=params.atr_lookback).mean()
    atr_safe = atr.mask(atr <= 0.0)

    smoothed_close = close.rolling(3, min_periods=1).median()
    slope_columns: list[pd.Series] = []
    weights = np.asarray([0.45, 0.35, 0.20], dtype=np.float64)
    for window in params.trend_windows:
        slope = smoothed_close.rolling(window, min_periods=window).apply(_regression_slope, raw=True)
        slope_columns.append(np.tanh((slope / atr_safe) / 0.035))
    slope_matrix = pd.concat(slope_columns, axis=1)
    slope_score = (slope_matrix * weights[: len(slope_columns)]).sum(axis=1) / weights[: len(slope_columns)].sum()

    ema_fast = close.ewm(span=params.ema_fast, adjust=False, min_periods=params.ema_fast).mean()
    ema_slow = close.ewm(span=params.ema_slow, adjust=False, min_periods=params.ema_slow).mean()
    ema_score = np.tanh(((ema_fast - ema_slow) / atr_safe) / 3.0)

    momentum = (close - close.shift(params.momentum_lookback)) / atr_safe
    momentum_score = np.tanh(momentum / 2.5)

    direction_balance = (
        np.sign(close.diff())
        .rolling(params.direction_balance_lookback, min_periods=params.direction_balance_lookback)
        .mean()
    )

    trend_score = (
        0.45 * slope_score
        + 0.20 * ema_score
        + 0.20 * momentum_score
        + 0.15 * direction_balance
    )
    trend_score = trend_score.clip(-1.0, 1.0)

    rolling_high = high.rolling(params.donchian_lookback, min_periods=params.donchian_lookback).max()
    rolling_low = low.rolling(params.donchian_lookback, min_periods=params.donchian_lookback).min()
    donchian_width = rolling_high - rolling_low
    range_atr = donchian_width / atr_safe

    returns = close.pct_change()
    realized_vol = returns.rolling(params.volatility_lookback, min_periods=params.volatility_lookback).std()
    residual_std = smoothed_close.rolling(
        params.donchian_lookback,
        min_periods=params.donchian_lookback,
    ).apply(_regression_residual_std, raw=True)
    residual_atr = residual_std / atr_safe
    range_atr_pct = _rolling_percentile_current(range_atr, params.percentile_lookback)
    realized_vol_pct = _rolling_percentile_current(realized_vol, params.percentile_lookback)
    atr_pct = _rolling_percentile_current(atr / close, params.percentile_lookback)
    residual_width_score = _sigmoid((residual_atr - 1.85) / 0.8)
    width_score = (
        0.45 * residual_width_score
        + 0.25 * realized_vol_pct
        + 0.20 * atr_pct
        + 0.10 * range_atr_pct
    ).clip(0.0, 1.0)

    trend_r2 = smoothed_close.rolling(params.quality_lookback, min_periods=params.quality_lookback).apply(
        _regression_r2,
        raw=True,
    )
    path = close.diff().abs().rolling(params.quality_lookback, min_periods=params.quality_lookback).sum()
    displacement = (close - close.shift(params.quality_lookback)).abs()
    efficiency_ratio = (displacement / path.mask(path <= 0.0)).clip(0.0, 1.0)
    adx_like = _adx_like(high, low, true_range, params.quality_lookback)
    trend_quality = (0.40 * trend_r2 + 0.35 * efficiency_ratio + 0.25 * adx_like).clip(0.0, 1.0)

    direction_confidence = (trend_score.abs() / max(params.trend_enter_threshold, 1e-12)).clip(0.0, 1.0)
    trend_confidence = (0.65 * direction_confidence + 0.35 * trend_quality).clip(0.0, 1.0)
    range_confidence = ((params.trend_enter_threshold - trend_score.abs()) / params.trend_enter_threshold).clip(0.0, 1.0)
    confidence = np.where(
        trend_score.abs() >= params.trend_exit_threshold,
        trend_confidence,
        (0.70 * range_confidence + 0.30 * (1.0 - trend_quality)).clip(0.0, 1.0),
    )

    return pd.DataFrame(
        {
            "atr": atr,
            "range_atr": range_atr,
            "residual_atr": residual_atr,
            "realized_vol": realized_vol,
            "trend_score": trend_score,
            "width_score": width_score,
            "trend_quality": trend_quality,
            "online_confidence": confidence,
            "confidence": confidence,
            "rolling_high": rolling_high,
            "rolling_low": rolling_low,
            "efficiency_ratio": efficiency_ratio,
            "adx_like": adx_like,
            "trend_r2": trend_r2,
        },
    )


def _confirm_online_regimes(
    factors: pd.DataFrame,
    boundaries: pd.DataFrame,
    params: MarketRegimeParams,
) -> pd.DataFrame:
    raw_regimes: list[str] = []
    confirmed_regimes: list[str] = []
    pending_regimes: list[str] = []
    pending_counts: list[int] = []
    segment_ids: list[int] = []
    segment_ages: list[int] = []
    regime_changed_flags: list[bool] = []

    pending_regime = REGIME_UNKNOWN
    pending_count = 0
    confirmed_regime = REGIME_UNKNOWN
    segment_id = 0
    segment_age = 0
    uncertain_count = 0

    boundary_values = boundaries["online_boundary"].fillna(False).astype(bool).to_numpy()

    for index, row in enumerate(factors.itertuples(index=False)):
        if _row_has_warmup_gaps(row):
            raw_regime = REGIME_UNKNOWN
            pending_regime = REGIME_UNKNOWN
            pending_count = 0
            regime_changed = False
            segment_age = 0 if confirmed_regime == REGIME_UNKNOWN else segment_age + 1
        else:
            threshold = _threshold_for_confirmed(confirmed_regime, float(row.trend_score), params)
            base_regime = _classify_from_scores(
                trend_score=float(row.trend_score),
                width_score=float(row.width_score),
                trend_quality=float(row.trend_quality),
                threshold=threshold,
                min_quality=params.min_trend_quality,
                wide_threshold=params.wide_width_threshold,
            )

            forced_degrade = False
            regime_changed = False
            if float(row.online_confidence) < params.min_confidence:
                uncertain_count += 1
                if confirmed_regime != REGIME_UNKNOWN and uncertain_count <= params.uncertain_hold_bars:
                    raw_regime = confirmed_regime
                else:
                    raw_regime = _range_regime(float(row.width_score), params.wide_width_threshold)
                    if raw_regime != confirmed_regime:
                        confirmed_regime = raw_regime
                        segment_id += 1
                        segment_age = 0
                        regime_changed = True
                    else:
                        segment_age = 0 if confirmed_regime == REGIME_UNKNOWN else segment_age + 1
                    pending_regime = raw_regime
                    pending_count = max(params.confirm_bars, 1)
                    forced_degrade = True
            else:
                raw_regime = base_regime
                uncertain_count = 0

            if not forced_degrade:
                if raw_regime == pending_regime:
                    pending_count += 1
                else:
                    pending_regime = raw_regime
                    pending_count = 1

                if (
                    boundary_values[index]
                    and raw_regime != confirmed_regime
                    and raw_regime != REGIME_UNKNOWN
                ):
                    pending_count = max(pending_count, 1)

                if pending_count >= params.confirm_bars and raw_regime != confirmed_regime:
                    confirmed_regime = raw_regime
                    segment_id += 1
                    segment_age = 0
                    regime_changed = True
                elif raw_regime == confirmed_regime:
                    segment_age = 0 if confirmed_regime == REGIME_UNKNOWN else segment_age + 1
                else:
                    segment_age = 0 if confirmed_regime == REGIME_UNKNOWN else segment_age + 1

        raw_regimes.append(raw_regime)
        confirmed_regimes.append(confirmed_regime)
        pending_regimes.append(pending_regime)
        pending_counts.append(pending_count)
        segment_ids.append(segment_id)
        segment_ages.append(segment_age)
        regime_changed_flags.append(regime_changed)

    labels = pd.DataFrame(
        {
            "online_raw_regime": raw_regimes,
            "online_pending_regime": pending_regimes,
            "online_confirmed_regime": confirmed_regimes,
            "online_pending_regime_count": pending_counts,
            "online_segment_id": segment_ids,
            "online_segment_age": segment_ages,
            "online_regime_changed": regime_changed_flags,
        },
    )
    labels["raw_regime"] = labels["online_raw_regime"]
    labels["pending_regime"] = labels["online_pending_regime"]
    labels["confirmed_regime"] = labels["online_confirmed_regime"]
    labels["pending_regime_count"] = labels["online_pending_regime_count"]
    labels["confirmed_regime_age"] = labels["online_segment_age"]
    labels["regime_changed"] = labels["online_regime_changed"]
    return labels


def _threshold_for_confirmed(
    confirmed_regime: str,
    trend_score: float,
    params: MarketRegimeParams,
) -> float:
    if trend_score > 0.0 and confirmed_regime in BULL_REGIMES:
        return params.trend_exit_threshold
    if trend_score < 0.0 and confirmed_regime in BEAR_REGIMES:
        return params.trend_exit_threshold
    return params.trend_enter_threshold


def _classify_from_scores(
    *,
    trend_score: float,
    width_score: float,
    trend_quality: float,
    threshold: float,
    min_quality: float,
    wide_threshold: float,
) -> str:
    if not np.isfinite(trend_score) or not np.isfinite(width_score) or not np.isfinite(trend_quality):
        return REGIME_UNKNOWN
    wide = width_score >= wide_threshold
    if trend_quality >= min_quality and trend_score >= threshold:
        return REGIME_WIDE_BULL if wide else REGIME_NARROW_BULL
    if trend_quality >= min_quality and trend_score <= -threshold:
        return REGIME_WIDE_BEAR if wide else REGIME_NARROW_BEAR
    return REGIME_WIDE_RANGE if wide else REGIME_NARROW_RANGE


def _range_regime(width_score: float, wide_threshold: float) -> str:
    if not np.isfinite(width_score):
        return REGIME_UNKNOWN
    return REGIME_WIDE_RANGE if width_score >= wide_threshold else REGIME_NARROW_RANGE


def _hindsight_confidence(
    *,
    regimes: list[str],
    trend: pd.Series,
    width: pd.Series,
    quality: pd.Series,
    params: MarketRegimeParams,
) -> list[float]:
    confidence: list[float] = []
    threshold = max(float(params.trend_exit_threshold), 1e-12)
    for regime, trend_value, width_value, quality_value in zip(regimes, trend, width, quality):
        if (
            regime == REGIME_UNKNOWN
            or not np.isfinite(float(trend_value))
            or not np.isfinite(float(width_value))
            or not np.isfinite(float(quality_value))
        ):
            confidence.append(0.0)
            continue
        width_margin = abs(float(width_value) - params.wide_width_threshold) / max(
            params.wide_width_threshold,
            1.0 - params.wide_width_threshold,
            1e-12,
        )
        if regime in BULL_REGIMES:
            trend_component = max(0.0, float(trend_value)) / threshold
            quality_component = float(quality_value)
        elif regime in BEAR_REGIMES:
            trend_component = max(0.0, -float(trend_value)) / threshold
            quality_component = float(quality_value)
        else:
            trend_component = max(0.0, threshold - abs(float(trend_value))) / threshold
            quality_component = 1.0 - float(quality_value)
        score = 0.55 * min(trend_component, 1.0) + 0.25 * np.clip(width_margin, 0.0, 1.0)
        score += 0.20 * np.clip(quality_component, 0.0, 1.0)
        confidence.append(float(np.clip(score, 0.0, 1.0)))
    return confidence


def _row_has_warmup_gaps(row: object) -> bool:
    return any(
        not np.isfinite(float(getattr(row, column)))
        for column in ["trend_score", "width_score", "trend_quality", "online_confidence"]
    )


def _merge_short_segments(regimes: Iterable[str], min_bars: int) -> list[str]:
    labels = list(regimes)
    if min_bars <= 1 or not labels:
        return labels

    for _ in range(2):
        runs = _runs(labels)
        for index, (start, end, label) in enumerate(runs):
            if label == REGIME_UNKNOWN or end - start >= min_bars:
                continue
            previous_label = _nearest_non_unknown(runs[:index], reverse=True)
            next_label = _nearest_non_unknown(runs[index + 1 :], reverse=False)
            replacement = previous_label or next_label or label
            labels[start:end] = [replacement] * (end - start)
    return labels


def _runs(labels: list[str]) -> list[tuple[int, int, str]]:
    if not labels:
        return []
    runs: list[tuple[int, int, str]] = []
    start = 0
    current = labels[0]
    for index, label in enumerate(labels[1:], start=1):
        if label == current:
            continue
        runs.append((start, index, current))
        start = index
        current = label
    runs.append((start, len(labels), current))
    return runs


def _nearest_non_unknown(
    runs: list[tuple[int, int, str]],
    *,
    reverse: bool,
) -> str | None:
    iterable = reversed(runs) if reverse else runs
    for _, _, label in iterable:
        if label != REGIME_UNKNOWN:
            return label
    return None


def _segment_ids(labels: list[str]) -> list[int]:
    ids: list[int] = []
    current_id = 0
    previous: str | None = None
    for label in labels:
        if label != previous:
            current_id += 1
            previous = label
        ids.append(current_id)
    return ids


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
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    x_centered = x - x_mean
    y_centered = y - y_mean
    denom = float(np.dot(x_centered, x_centered))
    total = float(np.dot(y_centered, y_centered))
    if denom <= 0.0 or total <= 0.0:
        return 0.0
    slope = float(np.dot(x_centered, y_centered) / denom)
    fitted = y_mean + slope * x_centered
    residual = y - fitted
    return float(np.clip(1.0 - float(np.dot(residual, residual)) / total, 0.0, 1.0))


def _regression_residual_std(values: np.ndarray) -> float:
    y = np.asarray(values, dtype=np.float64)
    if y.size < 3 or np.any(~np.isfinite(y)):
        return np.nan
    x = np.arange(y.size, dtype=np.float64)
    x_centered = x - float(x.mean())
    y_mean = float(y.mean())
    y_centered = y - y_mean
    denom = float(np.dot(x_centered, x_centered))
    if denom <= 0.0:
        return np.nan
    slope = float(np.dot(x_centered, y_centered) / denom)
    fitted = y_mean + slope * x_centered
    residual = y - fitted
    return float(np.std(residual))


def _rolling_percentile_current(series: pd.Series, window: int) -> pd.Series:
    min_periods = max(2, min(window, window // 3))
    return series.rolling(window, min_periods=min_periods).apply(_percentile_of_last, raw=True)


def _percentile_of_last(values: np.ndarray) -> float:
    valid = values[np.isfinite(values)]
    if valid.size == 0 or not np.isfinite(values[-1]):
        return np.nan
    return float(np.mean(valid <= values[-1]))


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


def _sigmoid(value: pd.Series | np.ndarray | float) -> pd.Series | np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-value))
