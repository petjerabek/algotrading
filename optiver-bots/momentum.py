"""Trend follower: trades the crossover of a fast and a slow EMA of the future's mid price.

It is a pure taker - it crosses the ETF spread with fill-and-kill orders to get
immediate exposure in the direction of the trend, and pays the taker fee for the
privilege. It wins when the market keeps going and bleeds fees when it chops.
"""
import asyncio
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side


ACTION_INTERVAL = 40
FAST_ALPHA = 0.02
LOT_SIZE = 10
POSITION_CAP = 60
SIGNAL_THRESHOLD_IN_CENTS = 100
SLOW_ALPHA = 0.002


class AutoTrader(BaseAutoTrader):
    """An EMA-crossover trend follower."""

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.position = 0
        self.etf_bid = self.etf_ask = 0
        self.fast_ema = self.slow_ema = 0.0
        self.updates_seen = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error."""
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Update the ETF touch, or the trend signal when the future moves."""
        if instrument == Instrument.ETF:
            self.etf_bid = bid_prices[0]
            self.etf_ask = ask_prices[0]
            return

        if bid_prices[0] == 0 or ask_prices[0] == 0:
            return

        mid = (bid_prices[0] + ask_prices[0]) / 2
        if self.slow_ema == 0.0:
            self.fast_ema = self.slow_ema = mid
        else:
            self.fast_ema += FAST_ALPHA * (mid - self.fast_ema)
            self.slow_ema += SLOW_ALPHA * (mid - self.slow_ema)

        self.updates_seen += 1
        if self.updates_seen % ACTION_INTERVAL == 0:
            self.trade_the_trend()

    def trade_the_trend(self) -> None:
        """Take liquidity in the direction of the fast-versus-slow EMA spread."""
        signal = self.fast_ema - self.slow_ema

        if signal > SIGNAL_THRESHOLD_IN_CENTS and self.etf_ask != 0 and self.position + LOT_SIZE <= POSITION_CAP:
            order_id = next(self.order_ids)
            self.bids.add(order_id)
            self.send_insert_order(order_id, Side.BUY, self.etf_ask, LOT_SIZE, Lifespan.FILL_AND_KILL)
        elif signal < -SIGNAL_THRESHOLD_IN_CENTS and self.etf_bid != 0 and self.position - LOT_SIZE >= -POSITION_CAP:
            order_id = next(self.order_ids)
            self.asks.add(order_id)
            self.send_insert_order(order_id, Side.SELL, self.etf_bid, LOT_SIZE, Lifespan.FILL_AND_KILL)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Track the position; this trader runs unhedged."""
        if client_order_id in self.bids:
            self.position += volume
        elif client_order_id in self.asks:
            self.position -= volume

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Forget fill-and-kill orders once they are done."""
        if remaining_volume == 0:
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)
