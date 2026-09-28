"""Strategies. The only file whose decisions are the strategy owner's.

QuoteAroundMid is a PLACEHOLDER to prove the plumbing works. It is
deliberately dumb: no inventory skew, no fair-value model, fixed size.
Port your own MarketMaker here (skew, fair = mid + model.predict(state.x), …).
"""

from arena.core import Cancel, Strategy, Submit


class QuoteAroundMid(Strategy):
    """One bid and one ask at round(mid) ± half_spread.

    Requotes only when a target price changes, instead of cancel-and-replace
    every tick — the habit that survives an exchange message cap.
    """

    def __init__(self, half_spread=2, size=10):
        self.half_spread = half_spread
        self.size = size

    def on_tick(self, s):
        if s.mid is None:
            return []
        fair = round(s.mid)
        want = {"buy": fair - self.half_spread, "sell": fair + self.half_spread}

        actions, kept = [], set()
        for oid, o in s.open_orders.items():
            if want[o.side] == o.price and o.side not in kept:
                kept.add(o.side)               # already where we want it
            else:
                actions.append(Cancel(oid))
        for side, price in want.items():
            if side not in kept:
                actions.append(Submit(side, price, self.size))
        return actions
