from __future__ import annotations

from collections import deque
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


class RollingZScoreFxConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    lookback: PositiveInt = 96
    entry_z: PositiveFloat = 1.5
    exit_z: float = 0.2
    stop_z: float = 0.0
    max_position_bars: int = 0
    close_positions_on_stop: bool = True


class RollingZScoreFxStrategy(Strategy):
    def __init__(self, config: RollingZScoreFxConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.window: deque[float] = deque(maxlen=config.lookback)
        self.position_bars: int = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        close = bar.close.as_double()
        self.window.append(close)

        if len(self.window) < self.config.lookback:
            return

        values = np.asarray(self.window, dtype=np.float64)
        std = values.std()
        z_score = (close - values.mean()) / std if std > 0.0 else 0.0

        instrument_id = self.config.instrument_id
        if self.portfolio.is_flat(instrument_id):
            self.position_bars = 0
            if z_score < -self.config.entry_z:
                self.buy()
            elif z_score > self.config.entry_z:
                self.sell()
        elif self.portfolio.is_net_long(instrument_id):
            self.position_bars += 1
            if z_score > -self.config.exit_z:
                self.close_all_positions(instrument_id)
            elif self.config.stop_z > 0.0 and z_score < -self.config.stop_z:
                self.close_all_positions(instrument_id)
            elif self.config.max_position_bars > 0 and self.position_bars >= self.config.max_position_bars:
                self.close_all_positions(instrument_id)
        elif self.portfolio.is_net_short(instrument_id):
            self.position_bars += 1
            if z_score < self.config.exit_z:
                self.close_all_positions(instrument_id)
            elif self.config.stop_z > 0.0 and z_score > self.config.stop_z:
                self.close_all_positions(instrument_id)
            elif self.config.max_position_bars > 0 and self.position_bars >= self.config.max_position_bars:
                self.close_all_positions(instrument_id)

    def buy(self) -> None:
        self.submit_order(self._market_order(OrderSide.BUY))

    def sell(self) -> None:
        self.submit_order(self._market_order(OrderSide.SELL))

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
        self.window.clear()
        self.position_bars = 0
