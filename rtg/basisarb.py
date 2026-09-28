"""Basis arbitrageur: trades the ETF against the future whenever the two disagree.

When the ETF is offered below the future's mid it lifts the offer, when the ETF
is bid above it hits the bid, and every ETF fill is immediately hedged with the
opposite trade in the future. It aims to hold no directional view at all - the
edge is the basis, not the market direction.
"""
import asyncio
import itertools

from typing import Dict, List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, Side


ACTION_INTERVAL = 4
EDGE_IN_CENTS = 100
HEDGE_TOLERANCE_IN_TICKS = 3
LOT_SIZE = 10
POSITION_CAP = 70
RECONCILE_INTERVAL = 100
TICK_SIZE_IN_CENTS = 100


class AutoTrader(BaseAutoTrader):
    """An ETF-versus-future basis arbitrageur."""

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.hedge_sides: Dict[int, Side] = dict()
        self.etf_position = self.future_position = 0
        self.future_bid = self.future_ask = 0
        self.updates_seen = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error."""
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0:
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Track the future's touch, and hunt for basis edge whenever the ETF moves."""
        if instrument == Instrument.FUTURE:
            self.future_bid = bid_prices[0]
            self.future_ask = ask_prices[0]
            return

        if self.future_bid == 0 or self.future_ask == 0:
            return

        self.updates_seen += 1
        if self.updates_seen % RECONCILE_INTERVAL == 0:
            self.reconcile_hedge()
        if self.updates_seen % ACTION_INTERVAL != 0:
            return

        fair = (self.future_bid + self.future_ask) // 2

        if (ask_prices[0] != 0 and ask_prices[0] <= fair - EDGE_IN_CENTS
                and self.etf_position + LOT_SIZE <= POSITION_CAP):
            volume = min(LOT_SIZE, ask_volumes[0])
            order_id = next(self.order_ids)
            self.bids.add(order_id)
            self.send_insert_order(order_id, Side.BUY, ask_prices[0], volume, Lifespan.FILL_AND_KILL)
        elif (bid_prices[0] != 0 and bid_prices[0] >= fair + EDGE_IN_CENTS
                and self.etf_position - LOT_SIZE >= -POSITION_CAP):
            volume = min(LOT_SIZE, bid_volumes[0])
            order_id = next(self.order_ids)
            self.asks.add(order_id)
            self.send_insert_order(order_id, Side.SELL, bid_prices[0], volume, Lifespan.FILL_AND_KILL)

    def hedge(self, side: Side, volume: int) -> None:
        """Send an aggressively priced hedge order into the future."""
        if volume < 1:
            return

        tolerance = HEDGE_TOLERANCE_IN_TICKS * TICK_SIZE_IN_CENTS
        if side == Side.BID:
            if self.future_ask == 0:
                return
            price = self.future_ask + tolerance
        else:
            if self.future_bid == 0:
                return
            price = max(TICK_SIZE_IN_CENTS, self.future_bid - tolerance)

        order_id = next(self.order_ids)
        self.hedge_sides[order_id] = side
        self.send_hedge_order(order_id, side, price, volume)

    def reconcile_hedge(self) -> None:
        """Top up the hedge if earlier hedge orders did not fill in full."""
        shortfall = -self.etf_position - self.future_position
        if shortfall > 0:
            self.hedge(Side.BID, shortfall)
        elif shortfall < 0:
            self.hedge(Side.ASK, -shortfall)

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Hedge every ETF fill straight away."""
        if client_order_id in self.bids:
            self.etf_position += volume
            self.hedge(Side.ASK, volume)
        elif client_order_id in self.asks:
            self.etf_position -= volume
            self.hedge(Side.BID, volume)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Track how much of the hedge actually traded."""
        side = self.hedge_sides.pop(client_order_id, None)
        if side is not None and volume > 0:
            self.future_position += volume if side == Side.BID else -volume

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Forget fill-and-kill orders once they are done."""
        if remaining_volume == 0:
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)
