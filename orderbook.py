"""A limit order book with price-time priority.

Everything here is plumbing EXCEPT three methods:
    OrderBook.submit_limit()
    OrderBook.cancel()
    OrderBook.l2()

Those are yours. Read the docstrings, then run `pytest -x` and make it green.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from sortedcontainers import SortedDict


class Side(Enum):
    BUY = 1
    SELL = -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


@dataclass
class Order:
    """A live order. `remaining` shrinks as it fills; 0 means fully filled."""

    id: int
    side: Side
    price: int  # in TICKS, always an integer. Never use floats for prices.
    qty: int  # original quantity
    remaining: int  # unfilled quantity
    seq: int  # monotonic arrival counter, used for FIFO within a price level


@dataclass(frozen=True)
class Trade:
    price: int
    qty: int
    maker_id: int  # the order that was already resting in the book
    taker_id: int  # the incoming order that caused the match


class OrderBook:
    """Single-instrument limit order book.

    Internal state (already wired up for you):

        self._bids   SortedDict[int, deque[Order]]   buy side,  keys ascending
        self._asks   SortedDict[int, deque[Order]]   sell side, keys ascending
        self._orders dict[int, Order]                id -> live order, for O(1) cancel

    Because SortedDict keeps keys in ascending order:
        best bid  = LAST  key of self._bids   (highest price someone will pay)
        best ask  = FIRST key of self._asks   (lowest price someone will accept)

    Within a price level the deque is FIFO: append on the right, pop from the
    left. The leftmost order is the oldest and therefore fills first.
    """

    def __init__(self) -> None:
        self._bids: SortedDict = SortedDict()
        self._asks: SortedDict = SortedDict()
        self._orders: dict[int, Order] = {}
        self._next_id = 1
        self._next_seq = 0

    # ------------------------------------------------------------------
    # Plumbing — given to you, no need to change any of this
    # ------------------------------------------------------------------

    def _book(self, side: Side) -> SortedDict:
        return self._bids if side is Side.BUY else self._asks

    def _new_order(self, side: Side, price: int, qty: int) -> Order:
        order = Order(
            id=self._next_id,
            side=side,
            price=price,
            qty=qty,
            remaining=qty,
            seq=self._next_seq,
        )
        self._next_id += 1
        self._next_seq += 1
        return order

    def _rest(self, order: Order) -> None:
        """Put an order into the book at its price level (creating it if needed)."""
        book = self._book(order.side)
        if order.price not in book:
            book[order.price] = deque()
        book[order.price].append(order)
        self._orders[order.id] = order

    def best_bid(self) -> int | None:
        return self._bids.peekitem(-1)[0] if self._bids else None

    def best_ask(self) -> int | None:
        return self._asks.peekitem(0)[0] if self._asks else None

    def spread(self) -> int | None:
        bid, ask = self.best_bid(), self.best_ask()
        return None if bid is None or ask is None else ask - bid

    def mid(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        return None if bid is None or ask is None else (bid + ask) / 2

    def get(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    def check_invariant(self) -> None:
        """The book must never be crossed. Call this after every event."""
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None and bid >= ask:
            raise AssertionError(f"crossed book: bid {bid} >= ask {ask}")
        for side_name, book in (("bid", self._bids), ("ask", self._asks)):
            for price, queue in book.items():
                if not queue:
                    raise AssertionError(f"empty {side_name} level left at {price}")

    def __repr__(self) -> str:
        lines = []
        for price in reversed(self._asks.keys()):
            total = sum(o.remaining for o in self._asks[price])
            lines.append(f"       {price:>6} | {total}")
        lines.append("       ------ +")
        for price in reversed(self._bids.keys()):
            total = sum(o.remaining for o in self._bids[price])
            lines.append(f"{total:>6} | {price:>6}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # YOURS TO WRITE
    # ------------------------------------------------------------------

    def submit_limit(self, side: Side, price: int, qty: int) -> tuple[int, list[Trade]]:
        """Submit a limit order. This is the heart of the engine.

        Behaviour required:

        1. Match greedily against the opposite side, best price first, while
           the incoming order still has quantity left AND the best opposite
           price satisfies the limit:
               a BUY  at price P can trade against asks priced <= P
               a SELL at price P can trade against bids priced >= P

        2. Within a price level, fill the OLDEST resting order first (FIFO).

        3. Each match trades min(incoming.remaining, resting.remaining) units
           AT THE RESTING ORDER'S PRICE — not the incoming order's price.
           A buy at 105 hitting an ask resting at 100 trades at 100.
           This is the single most commonly botched line in the whole file.

        4. Fully filled resting orders leave the book (and self._orders).
           Partially filled ones stay, with `remaining` reduced.

        5. A price level with an empty queue must be deleted from the
           SortedDict, or best_bid()/best_ask() will return a phantom price.

        6. Whatever quantity is left over after matching rests in the book.
           If nothing is left over, the order does NOT rest.

        Returns:
            (order_id, trades) where trades are in execution order.

        Helpers you already have: self._new_order(), self._rest(), self._book().
        """
        raise NotImplementedError

    def cancel(self, order_id: int) -> bool:
        """Remove a live order from the book.

        Returns True if an order was actually removed, False if the id is
        unknown or already fully filled.

        Remember to delete the price level if its queue becomes empty.
        """
        raise NotImplementedError

    def l2(self, depth: int = 5) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        """Aggregated market data snapshot — what a trading bot actually sees.

        Returns (bids, asks), each a list of (price, total_remaining_quantity)
        for the top `depth` price levels, BEST FIRST. So bids are ordered
        highest price first, asks lowest price first.

        Note what is NOT in here: individual order ids, and your own queue
        position. Real exchanges usually only publish this much. Not being
        able to see your queue position is a large part of why market making
        is hard.
        """
        raise NotImplementedError
