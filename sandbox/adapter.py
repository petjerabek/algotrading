"""SandboxAdapter: our own market (lob.Book + market.py participants) behind
the same interface the competition adapter will implement.

Each poll() is one tick: the fair value moves, the makers and takers act,
then we get a State. Our actions from execute() hit the book immediately,
before the next tick. Same ordering as market.py's MarketMaker.
"""

from arena.core import Adapter, Cancel, Fill, OpenOrder, State, Submit
from sandbox.lob import Book
from sandbox.market import FairValue, Maker, Taker, Trader, dispatch


class SandboxAdapter(Adapter):
    def __init__(self, seed, ticks=2000, n_makers=20, n_takers=20,
                 start=10000, volatility=1, depth=5, infer_sides=False,
                 maker_kw=None, taker_kw=None):
        # Fixed seeding scheme: same seed -> same market, every time.
        self.fv = FairValue(start, volatility, seed=seed)
        self.book = Book()
        self.makers = [Maker(seed=1000 * seed + i, **(maker_kw or {}))
                       for i in range(n_makers)]
        self.takers = [Taker(seed=2000 * seed + i, **(taker_kw or {}))
                       for i in range(n_takers)]
        self.me = Trader(name="us")
        self.ticks = ticks
        self.depth = depth
        self.infer_sides = infer_sides
        self.t = 0
        self._seen = 0          # trades already reported

    def poll(self):
        if self.t >= self.ticks:
            return None
        b = self.book
        v = self.fv.step()
        for m in self.makers:
            m.act(b, v)
        for k in self.takers:
            k.act(b)

        new, self._seen = b.trades[self._seen:], len(b.trades)
        trades, fills = [], []
        for tr in new:
            incoming, resting = b.orders[tr.incoming_id], b.orders[tr.resting_id]
            trades.append((tr.price, tr.qty, None if self.infer_sides else incoming.side))
            for o in (incoming, resting):
                if o.owner is self.me:
                    fills.append(Fill(o.id, o.side, tr.price, tr.qty))

        bids, asks = b.l2(depth=self.depth)
        state = State(
            t=self.t, bids=bids, asks=asks, trades=trades, fills=fills,
            position=self.me.position, cash=self.me.cash,
            open_orders={o.id: OpenOrder(o.id, o.side, o.price, o.remaining)
                         for o in b.live_orders() if o.owner is self.me},
            truth=v,
        )
        self.t += 1
        return state

    def execute(self, actions):
        for a in actions:
            if isinstance(a, Cancel):
                self.book.cancel(a.order_id)
            elif isinstance(a, Submit):
                _, trades = self.book.submit(a.side, a.price, a.qty, owner=self.me)
                dispatch(self.book, trades)

    def traders(self):
        """Everyone in the market, us included. PnL across all of them sums to 0."""
        return self.makers + self.takers + [self.me]
