"""Mean reverter: fades the future's deviation from its own rolling average.

It buys when the price is well below the rolling mean and sells when it is well
above, then flattens once the gap closes. This is the mirror image of the
momentum bot, and it is exactly the strategy that a sustained one-way market
punishes hardest.
"""
import asyncio
import collections
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side


ACTION_INTERVAL = 40
ENTRY_THRESHOLD_IN_CENTS = 200
EXIT_THRESHOLD_IN_CENTS = 50
LOT_SIZE = 10
POSITION_CAP = 50
WINDOW = 600


class AutoTrader(BaseAutoTrader):
    """A rolling-mean reversion trader."""

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.position = 0
        self.etf_bid = self.etf_ask = 0
        self.mids = collections.deque(maxlen=WINDOW)
        self.mid_sum = 0.0
        self.updates_seen = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error."""
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Update the ETF touch, or the rolling mean when the future moves."""
        if instrument == Instrument.ETF:
            self.etf_bid = bid_prices[0]
            self.etf_ask = ask_prices[0]
            return

        if bid_prices[0] == 0 or ask_prices[0] == 0:
            return

        mid = (bid_prices[0] + ask_prices[0]) / 2
        if len(self.mids) == WINDOW:
            self.mid_sum -= self.mids[0]
        self.mids.append(mid)
        self.mid_sum += mid

        self.updates_seen += 1
        if len(self.mids) == WINDOW and self.updates_seen % ACTION_INTERVAL == 0:
            self.fade_the_move(mid - self.mid_sum / WINDOW)

    def fade_the_move(self, deviation: float) -> None:
        """Buy what is cheap versus the rolling mean, sell what is dear, flatten in between."""
        if abs(deviation) < EXIT_THRESHOLD_IN_CENTS:
            if self.position > 0:
                self.take(Side.SELL, min(LOT_SIZE, self.position))
            elif self.position < 0:
                self.take(Side.BUY, min(LOT_SIZE, -self.position))
        elif deviation <= -ENTRY_THRESHOLD_IN_CENTS and self.position + LOT_SIZE <= POSITION_CAP:
            self.take(Side.BUY, LOT_SIZE)
        elif deviation >= ENTRY_THRESHOLD_IN_CENTS and self.position - LOT_SIZE >= -POSITION_CAP:
            self.take(Side.SELL, LOT_SIZE)

    def take(self, side: Side, volume: int) -> None:
        """Cross the ETF spread with a fill-and-kill order."""
        price = self.etf_ask if side == Side.BUY else self.etf_bid
        if price == 0 or volume < 1:
            return

        order_id = next(self.order_ids)
        (self.bids if side == Side.BUY else self.asks).add(order_id)
        self.send_insert_order(order_id, side, price, volume, Lifespan.FILL_AND_KILL)

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
