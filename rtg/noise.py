"""Noise trader: buys and sells at random, with a gentle pull back towards flat.

It has no view on price at all. It exists to put uninformed two-way flow into the
market, which is what the other strategies are trying to earn from. The random
seed is fixed so that a match can be re-run and compared.
"""
import asyncio
import itertools
import random

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side


ACTION_INTERVAL = 30
FLATTEN_BIAS = 0.75
POSITION_CAP = 60
SEED = 20260917
SOFT_INVENTORY = 30
TRADE_PROBABILITY = 0.25


class AutoTrader(BaseAutoTrader):
    """A random, uninformed taker."""

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.position = 0
        self.rng = random.Random(SEED)
        self.updates_seen = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error."""
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Every so often, flip a coin and trade on it."""
        if instrument != Instrument.ETF or bid_prices[0] == 0 or ask_prices[0] == 0:
            return

        self.updates_seen += 1
        if self.updates_seen % ACTION_INTERVAL != 0 or self.rng.random() > TRADE_PROBABILITY:
            return

        side = self.pick_side()
        volume = self.rng.randint(1, 3) * 5

        if side == Side.BUY:
            if self.position + volume > POSITION_CAP:
                return
            price = ask_prices[0]
        else:
            if self.position - volume < -POSITION_CAP:
                return
            price = bid_prices[0]

        order_id = next(self.order_ids)
        (self.bids if side == Side.BUY else self.asks).add(order_id)
        self.send_insert_order(order_id, side, price, volume, Lifespan.FILL_AND_KILL)

    def pick_side(self) -> Side:
        """Pick a side at random, leaning towards whichever one reduces the position."""
        if self.position > SOFT_INVENTORY:
            return Side.SELL if self.rng.random() < FLATTEN_BIAS else Side.BUY
        if self.position < -SOFT_INVENTORY:
            return Side.BUY if self.rng.random() < FLATTEN_BIAS else Side.SELL
        return Side.BUY if self.rng.random() < 0.5 else Side.SELL

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
