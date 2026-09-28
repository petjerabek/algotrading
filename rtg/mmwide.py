"""Passive market maker quoting a fixed spread either side of the future's mid price.

The deliberate contrast with the skewbot: this one never skews its quotes with
inventory. It quotes symmetrically until it hits a hard inventory cap, then stops
quoting that side entirely. In a trending market it repeatedly gets filled on the
losing side and ends up parked at its cap.
"""
import asyncio
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side


LOT_SIZE = 10
POSITION_CAP = 80
SPREAD_IN_TICKS = 2
TICK_SIZE_IN_CENTS = 100


class AutoTrader(BaseAutoTrader):
    """A symmetric, non-skewing market maker."""

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error."""
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Requote around the future's mid price whenever it moves."""
        if instrument != Instrument.FUTURE or bid_prices[0] == 0 or ask_prices[0] == 0:
            return

        mid = (bid_prices[0] + ask_prices[0]) // 2
        fair = round(mid / TICK_SIZE_IN_CENTS) * TICK_SIZE_IN_CENTS
        new_bid_price = fair - SPREAD_IN_TICKS * TICK_SIZE_IN_CENTS
        new_ask_price = fair + SPREAD_IN_TICKS * TICK_SIZE_IN_CENTS

        if self.bid_id != 0 and new_bid_price != self.bid_price:
            self.send_cancel_order(self.bid_id)
            self.bid_id = 0
        if self.ask_id != 0 and new_ask_price != self.ask_price:
            self.send_cancel_order(self.ask_id)
            self.ask_id = 0

        if self.bid_id == 0 and self.position + LOT_SIZE <= POSITION_CAP:
            self.bid_id = next(self.order_ids)
            self.bid_price = new_bid_price
            self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
            self.bids.add(self.bid_id)

        if self.ask_id == 0 and self.position - LOT_SIZE >= -POSITION_CAP:
            self.ask_id = next(self.order_ids)
            self.ask_price = new_ask_price
            self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
            self.asks.add(self.ask_id)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Track the position; this trader runs unhedged."""
        if client_order_id in self.bids:
            self.position += volume
        elif client_order_id in self.asks:
            self.position -= volume

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Forget orders that are completely filled or cancelled."""
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)
