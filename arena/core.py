"""Our side of the boundary. Nothing here knows any exchange.

    their API  ->  adapter (one file, written on the day)  ->  State  ->  Strategy
    Strategy   ->  [Submit / Cancel]  ->  Safety  ->  adapter  ->  their API

An adapter has one job: turn whatever the exchange sends into a `State`, and
turn our `Submit`/`Cancel` actions into whatever the exchange expects.
Everything else — features, logging, bake-off, safety, strategy — sits behind
this boundary and never changes.

Two shapes of competition API, both covered:
  - We call them (pull):  `Runner.run(adapter)` loops poll() -> step() -> execute().
  - They call us (push):  their per-tick callback builds a State and calls
                          `runner.step(state)`, then sends the returned actions.
"""

from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# Actions: the only things a strategy can ask for
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Submit:
    side: str       # "buy" / "sell"
    price: int      # ticks
    qty: int


@dataclass(frozen=True)
class Cancel:
    order_id: object


# ----------------------------------------------------------------------
# What the strategy sees
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class OpenOrder:
    """One of OUR orders still resting on the exchange."""
    id: object
    side: str
    price: int
    qty: int        # remaining


@dataclass(frozen=True)
class Fill:
    """One execution of one of OUR orders."""
    order_id: object
    side: str       # our side: "buy" means we bought
    price: int
    qty: int


@dataclass
class State:
    """Everything the bot knows at one moment.

    trades: EVERY trade in the market since the last State (ours included),
            as (price, qty, aggressor_side_or_None).
    fills:  only OUR executions since the last State.
    x:      feature vector, filled in by the Runner — adapters leave it None.
    truth:  hidden fair value. Sandbox only; always None on the day.
    """
    t: float
    bids: list
    asks: list
    trades: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    position: int = 0
    cash: float = 0.0
    open_orders: dict = field(default_factory=dict)   # id -> OpenOrder
    x: list = None
    truth: float = None

    @property
    def mid(self):
        if not self.bids or not self.asks:
            return None
        return (self.bids[0][0] + self.asks[0][0]) / 2

    def pnl(self, mark=None):
        """Mark-to-market PnL, marked at mid unless told otherwise."""
        mark = self.mid if mark is None else mark
        if mark is None:                  # one-sided book: can't value a position
            return None if self.position else self.cash
        return self.cash + self.position * mark


# ----------------------------------------------------------------------
# What a strategy and an adapter must provide
# ----------------------------------------------------------------------

class Strategy:
    """Subclass this. The base class does nothing, which is a valid strategy:
    it lets you log a market without taking part in it."""

    def on_fill(self, fill):
        pass

    def on_tick(self, state):
        return []


class Adapter:
    """What the day's adapter must implement (pull style)."""

    def poll(self):
        """Next State, or None when the session is over."""
        raise NotImplementedError

    def execute(self, actions):
        """Send Submit/Cancel actions to the exchange."""
        raise NotImplementedError
