from __future__ import annotations

from collections import deque
from datetime import UTC
from datetime import datetime
from decimal import Decimal

import numpy as np
from nautilus_trader.config import PositiveFloat
from nautilus_trader.config import PositiveInt
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy


REGIME_NARROW_BULL = "narrow_bull"
REGIME_WIDE_BULL = "wide_bull"
REGIME_WIDE_RANGE = "wide_range"
REGIME_WIDE_BEAR = "wide_bear"
REGIME_NARROW_BEAR = "narrow_bear"

LONG_ENTRY_REGIMES = frozenset({REGIME_NARROW_BULL, REGIME_WIDE_BULL, REGIME_WIDE_RANGE})
SHORT_ENTRY_REGIMES = frozenset({REGIME_WIDE_RANGE, REGIME_WIDE_BEAR, REGIME_NARROW_BEAR})
ALL_ENTRY_REGIMES = LONG_ENTRY_REGIMES | SHORT_ENTRY_REGIMES


class RegimeAdaptiveFxConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    channel_lookback: PositiveInt = 96
    atr_lookback: PositiveInt = 48
    trend_lookback: PositiveInt = 96
    momentum_lookback: PositiveInt = 24
    breakout_lookback: PositiveInt = 48
    short_ma_lookback: PositiveInt = 16
    confirm_bars: PositiveInt = 4
    long_confirm_bars: int = 4
    short_confirm_bars: int = 4
    bull_threshold: PositiveFloat = 0.03
    bear_threshold: PositiveFloat = 0.03
    flat_threshold: PositiveFloat = 0.01
    narrow_width_threshold: PositiveFloat = 8.0
    wide_range_threshold: PositiveFloat = 10.0
    momentum_entry: PositiveFloat = 0.0003
    pullback_buy_zone: PositiveFloat = 0.33
    pullback_sell_zone: PositiveFloat = 0.67
    lower_third: PositiveFloat = 0.33
    upper_third: PositiveFloat = 0.67
    stop_atr_mult: PositiveFloat = 2.0
    long_stop_atr_mult: float = 1.5
    short_stop_atr_mult: float = 1.5
    max_position_bars: int = 96
    max_spread_points: int = 0
    cooldown_bars: int = 4
    min_regime_bars: int = 2
    min_target_atr_mult: float = 0.8
    long_min_target_atr_mult: float = 0.8
    short_min_target_atr_mult: float = 0.8
    min_target_spread_mult: float = 0.0
    assumed_spread_points: int = 10
    trade_direction: str = "long_short"
    strict_long_filter: bool = False
    strict_long_trend_mult: float = 1.5
    disable_wide_range_longs: bool = False
    range_breakout_buffer_atr: float = 0.1
    range_structure_filter: bool = True
    range_structure_lookback: int = 3
    disabled_entry_regimes: str = ""
    entry_hours_utc: str = ""
    long_enabled_regimes: str = "wide_bull"
    short_enabled_regimes: str = "wide_bear"
    long_entry_hours_utc: str = "6-8"
    short_entry_hours_utc: str = "13-15"
    close_positions_on_stop: bool = True


class RegimeAdaptiveFxStrategy(Strategy):
    def __init__(self, config: RegimeAdaptiveFxConfig) -> None:
        super().__init__(config)
        max_window = max(
            config.channel_lookback,
            config.atr_lookback + 1,
            config.trend_lookback,
            config.momentum_lookback + 1,
            config.breakout_lookback + 1,
            config.short_ma_lookback,
        )
        self.instrument: Instrument | None = None
        self.highs: deque[float] = deque(maxlen=max_window)
        self.lows: deque[float] = deque(maxlen=max_window)
        self.closes: deque[float] = deque(maxlen=max_window)
        self.true_ranges: deque[float] = deque(maxlen=config.atr_lookback)
        self.confirmed_regime: str | None = None
        self.pending_regime: str | None = None
        self.pending_regime_count: int = 0
        self.position_bars: int = 0
        self.entry_price: float | None = None
        self.entry_atr: float | None = None
        self.cooldown_bars_remaining: int = 0
        self.confirmed_regime_age: int = 0
        self.confirmed_regime_observed_count: int = 0
        self.disabled_entry_regimes = self._parse_disabled_regimes(config.disabled_entry_regimes)
        self.long_enabled_regimes = self._parse_enabled_regimes(config.long_enabled_regimes)
        self.short_enabled_regimes = self._parse_enabled_regimes(config.short_enabled_regimes)
        legacy_entry_hours_utc = self._parse_entry_hours(config.entry_hours_utc)
        self.long_entry_hours_utc = self._resolve_entry_hours(config.long_entry_hours_utc, legacy_entry_hours_utc)
        self.short_entry_hours_utc = self._resolve_entry_hours(config.short_entry_hours_utc, legacy_entry_hours_utc)
        self.min_confirm_bars = min(self._confirm_bars_for(1), self._confirm_bars_for(-1))

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        high = bar.high.as_double()
        low = bar.low.as_double()
        close = bar.close.as_double()
        prev_close = self.closes[-1] if self.closes else close

        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

        if not self._has_warmup():
            return

        factors = self._factors()
        raw_regime = self._classify_regime(factors["trend_slope"], factors["width_score"])
        regime_changed = self._confirm_regime(raw_regime)

        instrument_id = self.config.instrument_id
        if self.portfolio.is_flat(instrument_id):
            self.position_bars = 0
            self.entry_price = None
            self.entry_atr = None
            if self.cooldown_bars_remaining > 0:
                self.cooldown_bars_remaining -= 1
                return
            if regime_changed:
                return
            self._maybe_enter(close, factors, bar.ts_event)
            return

        self.position_bars += 1
        if regime_changed:
            self._close_positions("regime_change", force=True)
            return

        if self.portfolio.is_net_long(instrument_id):
            if self._should_exit_long(close, factors):
                self._close_positions("long_exit", force=True)
        elif self.portfolio.is_net_short(instrument_id):
            if self._should_exit_short(close, factors):
                self._close_positions("short_exit", force=True)

    def _has_warmup(self) -> bool:
        return (
            len(self.closes) >= self.config.channel_lookback
            and len(self.closes) >= self.config.trend_lookback
            and len(self.closes) > self.config.momentum_lookback
            and len(self.closes) > self.config.breakout_lookback
            and len(self.true_ranges) >= self.config.atr_lookback
        )

    def _factors(self) -> dict[str, float]:
        channel_highs = list(self.highs)[-self.config.channel_lookback :]
        channel_lows = list(self.lows)[-self.config.channel_lookback :]
        closes = list(self.closes)
        close = closes[-1]
        rolling_high = max(channel_highs)
        rolling_low = min(channel_lows)
        channel_width = rolling_high - rolling_low
        atr = float(np.mean(np.asarray(self.true_ranges, dtype=np.float64)))
        width_score = channel_width / max(atr, 1e-12)
        range_pos = (close - rolling_low) / max(channel_width, 1e-12)
        trend_slope = self._linear_regression_slope(closes[-self.config.trend_lookback :]) / max(atr, 1e-12)
        momentum = close / closes[-self.config.momentum_lookback - 1] - 1.0
        short_ma = float(np.mean(np.asarray(closes[-self.config.short_ma_lookback :], dtype=np.float64)))
        prev_high = max(list(self.highs)[-self.config.breakout_lookback - 1 : -1])
        prev_low = min(list(self.lows)[-self.config.breakout_lookback - 1 : -1])
        return {
            "close": close,
            "rolling_high": rolling_high,
            "rolling_low": rolling_low,
            "channel_width": channel_width,
            "atr": atr,
            "width_score": width_score,
            "range_pos": range_pos,
            "trend_slope": trend_slope,
            "momentum": momentum,
            "short_ma": short_ma,
            "prev_high": prev_high,
            "prev_low": prev_low,
        }

    @staticmethod
    def _linear_regression_slope(values: list[float]) -> float:
        y = np.asarray(values, dtype=np.float64)
        x = np.arange(len(y), dtype=np.float64)
        x_mean = float(x.mean())
        y_mean = float(y.mean())
        denom = float(np.sum((x - x_mean) ** 2))
        if denom <= 0.0:
            return 0.0
        return float(np.sum((x - x_mean) * (y - y_mean)) / denom)

    def _classify_regime(self, trend_slope: float, width_score: float) -> str | None:
        if trend_slope > self.config.bull_threshold:
            if width_score <= self.config.narrow_width_threshold:
                return REGIME_NARROW_BULL
            return REGIME_WIDE_BULL
        if abs(trend_slope) <= self.config.flat_threshold and width_score >= self.config.wide_range_threshold:
            return REGIME_WIDE_RANGE
        if trend_slope < -self.config.bear_threshold:
            if width_score <= self.config.narrow_width_threshold:
                return REGIME_NARROW_BEAR
            return REGIME_WIDE_BEAR
        return None

    def _confirm_regime(self, raw_regime: str | None) -> bool:
        if raw_regime is None:
            self.pending_regime = None
            self.pending_regime_count = 0
            return False

        if raw_regime == self.pending_regime:
            self.pending_regime_count += 1
        else:
            self.pending_regime = raw_regime
            self.pending_regime_count = 1

        if (
            self.pending_regime_count >= self.min_confirm_bars
            and raw_regime != self.confirmed_regime
        ):
            self.confirmed_regime = raw_regime
            self.confirmed_regime_age = 0
            self.confirmed_regime_observed_count = self.pending_regime_count
            return True
        if raw_regime == self.confirmed_regime:
            self.confirmed_regime_age += 1
            self.confirmed_regime_observed_count = max(
                self.confirmed_regime_observed_count,
                self.pending_regime_count,
            )
        return False

    def _maybe_enter(self, close: float, factors: dict[str, float], ts_event: int) -> None:
        regime = self.confirmed_regime
        if regime is None or not self._entry_environment_ok():
            return
        if self.confirmed_regime_age < self.config.min_regime_bars:
            return

        if (
            regime == REGIME_NARROW_BULL
            and self._entry_filters_ok(1, regime, ts_event)
            and self._can_enter_long(factors)
            and (
            factors["momentum"] > self.config.momentum_entry or close > factors["prev_high"]
            )
        ):
            if self._target_space_ok(1, factors):
                self._enter_long(close, factors["atr"])
        elif (
            regime == REGIME_WIDE_BULL
            and self._entry_filters_ok(1, regime, ts_event)
            and self._can_enter_long(factors)
            and (
            factors["range_pos"] <= self.config.pullback_buy_zone
            and factors["trend_slope"] > self.config.flat_threshold
            )
        ):
            if self._target_space_ok(1, factors):
                self._enter_long(close, factors["atr"])
        elif (
            regime == REGIME_WIDE_RANGE
            and self._entry_filters_ok(1, regime, ts_event)
            and self._can_enter_long(factors)
            and (
            factors["range_pos"] <= self.config.lower_third
            and self._range_long_ok(factors)
            )
        ):
            if self._target_space_ok(1, factors):
                self._enter_long(close, factors["atr"])
        elif (
            regime == REGIME_NARROW_BEAR
            and self._entry_filters_ok(-1, regime, ts_event)
            and self._direction_allowed(-1)
            and (
            factors["momentum"] < -self.config.momentum_entry or close < factors["prev_low"]
            )
        ):
            if self._target_space_ok(-1, factors):
                self._enter_short(close, factors["atr"])
        elif (
            regime == REGIME_WIDE_BEAR
            and self._entry_filters_ok(-1, regime, ts_event)
            and self._direction_allowed(-1)
            and (
            factors["range_pos"] >= self.config.pullback_sell_zone
            and factors["trend_slope"] < -self.config.flat_threshold
            )
        ):
            if self._target_space_ok(-1, factors):
                self._enter_short(close, factors["atr"])
        elif (
            regime == REGIME_WIDE_RANGE
            and self._entry_filters_ok(-1, regime, ts_event)
            and self._direction_allowed(-1)
            and (
            factors["range_pos"] >= self.config.upper_third
            and self._range_short_ok(factors)
            )
        ):
            if self._target_space_ok(-1, factors):
                self._enter_short(close, factors["atr"])

    def _should_exit_long(self, close: float, factors: dict[str, float]) -> bool:
        if self._hit_long_stop(close):
            return True
        if self._hit_max_position_bars():
            return True

        regime = self.confirmed_regime
        if regime == REGIME_NARROW_BULL:
            return factors["momentum"] <= 0.0 or close < factors["short_ma"]
        if regime == REGIME_WIDE_BULL:
            return factors["range_pos"] >= self.config.upper_third or factors["trend_slope"] <= 0.0
        if regime == REGIME_WIDE_RANGE:
            return factors["range_pos"] >= self.config.upper_third or close < factors["prev_low"]
        return regime in {REGIME_WIDE_BEAR, REGIME_NARROW_BEAR}

    def _should_exit_short(self, close: float, factors: dict[str, float]) -> bool:
        if self._hit_short_stop(close):
            return True
        if self._hit_max_position_bars():
            return True

        regime = self.confirmed_regime
        if regime == REGIME_NARROW_BEAR:
            return factors["momentum"] >= 0.0 or close > factors["short_ma"]
        if regime == REGIME_WIDE_BEAR:
            return factors["range_pos"] <= self.config.lower_third or factors["trend_slope"] >= 0.0
        if regime == REGIME_WIDE_RANGE:
            return factors["range_pos"] <= self.config.lower_third or close > factors["prev_high"]
        return regime in {REGIME_WIDE_BULL, REGIME_NARROW_BULL}

    def _hit_long_stop(self, close: float) -> bool:
        if self.entry_price is None or self.entry_atr is None:
            return False
        return close <= self.entry_price - self._stop_atr_mult(1) * self.entry_atr

    def _hit_short_stop(self, close: float) -> bool:
        if self.entry_price is None or self.entry_atr is None:
            return False
        return close >= self.entry_price + self._stop_atr_mult(-1) * self.entry_atr

    def _hit_max_position_bars(self) -> bool:
        return self.config.max_position_bars > 0 and self.position_bars >= self.config.max_position_bars

    def _direction_allowed(self, direction: int) -> bool:
        mode = self.config.trade_direction.lower()
        if mode in {"long_short", "both", "all"}:
            return True
        if mode == "long_only":
            return direction > 0
        if mode == "short_only":
            return direction < 0
        self.log.error(f"Unsupported trade_direction={self.config.trade_direction!r}")
        return False

    @staticmethod
    def _parse_disabled_regimes(value: str) -> set[str]:
        if not value:
            return set()
        regimes = {part.strip().lower() for part in value.split(",") if part.strip()}
        if "none" in regimes or "all_enabled" in regimes:
            return set()
        return regimes

    @staticmethod
    def _parse_enabled_regimes(value: str) -> set[str] | None:
        text = value.strip().lower()
        if not text or text in {"all", "any", "all_enabled"}:
            return None
        if text == "none":
            return set()

        regimes = {part.strip().lower() for part in text.split(",") if part.strip()}
        invalid_regimes = sorted(regimes - ALL_ENTRY_REGIMES)
        if invalid_regimes:
            raise ValueError(f"enabled_regimes contains unsupported regimes: {invalid_regimes}")
        return regimes

    @staticmethod
    def _parse_entry_hours(value: str) -> set[int] | None:
        text = value.strip().lower()
        if not text or text in {"all", "any", "none"}:
            return None

        hours: set[int] = set()
        for part in text.split(","):
            item = part.strip()
            if not item:
                continue
            if "-" in item:
                start_text, end_text = item.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                if start <= end:
                    hours.update(range(start, end + 1))
                else:
                    hours.update(range(start, 24))
                    hours.update(range(0, end + 1))
            else:
                hours.add(int(item))
        invalid_hours = [hour for hour in hours if hour < 0 or hour > 23]
        if invalid_hours:
            raise ValueError(f"entry_hours_utc contains invalid hours: {invalid_hours}")
        return hours

    def _resolve_entry_hours(self, value: str, fallback: set[int] | None) -> set[int] | None:
        if value.strip():
            return self._parse_entry_hours(value)
        return fallback

    def _entry_time_ok(self, direction: int, ts_event: int) -> bool:
        allowed_hours = self.long_entry_hours_utc if direction > 0 else self.short_entry_hours_utc
        if allowed_hours is None:
            return True
        hour = datetime.fromtimestamp(ts_event // 1_000_000_000, UTC).hour
        return hour in allowed_hours

    def _entry_filters_ok(self, direction: int, regime: str, ts_event: int) -> bool:
        return (
            self._regime_entry_enabled(direction, regime)
            and self._entry_time_ok(direction, ts_event)
            and self._side_confirm_ok(direction)
        )

    def _regime_entry_enabled(self, direction: int, regime: str) -> bool:
        if direction > 0:
            enabled_regimes = self.long_enabled_regimes
            fallback_regimes = LONG_ENTRY_REGIMES
        else:
            enabled_regimes = self.short_enabled_regimes
            fallback_regimes = SHORT_ENTRY_REGIMES

        if enabled_regimes is not None:
            return regime in enabled_regimes
        return regime in fallback_regimes and regime not in self.disabled_entry_regimes

    def _side_confirm_ok(self, direction: int) -> bool:
        if self.confirmed_regime is None:
            return False
        return self.confirmed_regime_observed_count >= self._confirm_bars_for(direction)

    def _confirm_bars_for(self, direction: int) -> int:
        configured = self.config.long_confirm_bars if direction > 0 else self.config.short_confirm_bars
        if configured > 0:
            return configured
        return max(int(self.config.confirm_bars), 1)

    def _stop_atr_mult(self, direction: int) -> float:
        configured = self.config.long_stop_atr_mult if direction > 0 else self.config.short_stop_atr_mult
        if configured > 0.0:
            return configured
        return self.config.stop_atr_mult

    def _min_target_atr_mult(self, direction: int) -> float:
        configured = self.config.long_min_target_atr_mult if direction > 0 else self.config.short_min_target_atr_mult
        if configured >= 0.0:
            return configured
        return self.config.min_target_atr_mult

    def _can_enter_long(self, factors: dict[str, float]) -> bool:
        if not self._direction_allowed(1):
            return False
        if self.config.disable_wide_range_longs and self.confirmed_regime == REGIME_WIDE_RANGE:
            return False
        if not self.config.strict_long_filter:
            return True
        return (
            factors["trend_slope"] > self.config.bull_threshold * self.config.strict_long_trend_mult
            and factors["close"] > factors["short_ma"]
            and factors["momentum"] > 0.0
        )

    def _range_long_ok(self, factors: dict[str, float]) -> bool:
        if factors["close"] <= factors["prev_low"] + self.config.range_breakout_buffer_atr * factors["atr"]:
            return False
        if self.config.range_structure_filter and self._recent_lower_lows():
            return False
        return True

    def _range_short_ok(self, factors: dict[str, float]) -> bool:
        if factors["close"] >= factors["prev_high"] - self.config.range_breakout_buffer_atr * factors["atr"]:
            return False
        if self.config.range_structure_filter and self._recent_higher_highs():
            return False
        return True

    def _recent_lower_lows(self) -> bool:
        lookback = max(self.config.range_structure_lookback, 0)
        if lookback < 2 or len(self.lows) < lookback:
            return False
        lows = list(self.lows)[-lookback:]
        return all(lows[i] < lows[i - 1] for i in range(1, len(lows)))

    def _recent_higher_highs(self) -> bool:
        lookback = max(self.config.range_structure_lookback, 0)
        if lookback < 2 or len(self.highs) < lookback:
            return False
        highs = list(self.highs)[-lookback:]
        return all(highs[i] > highs[i - 1] for i in range(1, len(highs)))

    def _target_space_ok(self, direction: int, factors: dict[str, float]) -> bool:
        min_target_atr_mult = self._min_target_atr_mult(direction)
        if min_target_atr_mult <= 0.0 and self.config.min_target_spread_mult <= 0.0:
            return True

        target_distance = self._target_distance(direction, factors)
        min_distance = max(
            min_target_atr_mult * factors["atr"],
            self.config.min_target_spread_mult * self._spread_price_estimate(),
        )
        return target_distance >= min_distance

    def _target_distance(self, direction: int, factors: dict[str, float]) -> float:
        regime = self.confirmed_regime
        close = factors["close"]
        if direction > 0:
            if regime == REGIME_NARROW_BULL:
                target = factors["rolling_high"]
            else:
                target = factors["rolling_low"] + self.config.upper_third * factors["channel_width"]
            return max(target - close, 0.0)

        if regime == REGIME_NARROW_BEAR:
            target = factors["rolling_low"]
        else:
            target = factors["rolling_low"] + self.config.lower_third * factors["channel_width"]
        return max(close - target, 0.0)

    def _spread_price_estimate(self) -> float:
        assert self.instrument is not None
        point = self.instrument.price_increment.as_double()
        if point <= 0.0:
            return 0.0

        quote = self.cache.quote_tick(self.config.instrument_id)
        if quote is not None:
            return max(quote.ask_price.as_double() - quote.bid_price.as_double(), 0.0)

        spread_points = max(self.config.assumed_spread_points, 0)
        return spread_points * point

    def _enter_long(self, close: float, atr: float) -> None:
        self.entry_price = close
        self.entry_atr = atr
        self.position_bars = 0
        self.submit_order(self._market_order(OrderSide.BUY))

    def _enter_short(self, close: float, atr: float) -> None:
        self.entry_price = close
        self.entry_atr = atr
        self.position_bars = 0
        self.submit_order(self._market_order(OrderSide.SELL))

    def _close_positions(self, reason: str, *, force: bool = False) -> None:
        if not force and not self._entry_environment_ok():
            return
        self.log.debug(f"Closing positions for {self.config.instrument_id}: {reason}")
        self.close_all_positions(self.config.instrument_id)
        self.cooldown_bars_remaining = max(self.config.cooldown_bars, 0)

    def _entry_environment_ok(self) -> bool:
        if self.config.max_spread_points <= 0:
            return True
        assert self.instrument is not None

        quote = self.cache.quote_tick(self.config.instrument_id)
        if quote is None:
            return False

        point = self.instrument.price_increment.as_double()
        if point <= 0.0:
            return False

        spread_points = (quote.ask_price.as_double() - quote.bid_price.as_double()) / point
        return spread_points <= self.config.max_spread_points

    def _market_order(self, side: OrderSide) -> MarketOrder:
        assert self.instrument is not None
        return self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.highs.clear()
        self.lows.clear()
        self.closes.clear()
        self.true_ranges.clear()
        self.confirmed_regime = None
        self.pending_regime = None
        self.pending_regime_count = 0
        self.position_bars = 0
        self.entry_price = None
        self.entry_atr = None
        self.cooldown_bars_remaining = 0
        self.confirmed_regime_age = 0
        self.confirmed_regime_observed_count = 0
