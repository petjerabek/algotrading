# Writing the adapter on the day

Everything behind `arena/core.py` already works: features, logging, bake-off,
safety, strategy. The adapter is the only new code. Target: working in the
first hour, owned by the adapter/infra person.

## 1. Read their spec for these answers (write them down)

| question | why it matters | where it goes |
|---|---|---|
| Do we call them, or do they call us? | pull → `Runner.run(adapter)`; push → call `runner.step(state)` from their callback | adapter shape |
| How does the book arrive? Full snapshot or diffs? Depth? | we need `(price, qty)` lists, best first | `State.bids/asks` |
| Are prices ticks or decimals? Tick size? | we work in integer ticks | convert in adapter |
| Do trades say who the aggressor was? | if not, pass `side=None` and it gets inferred | `State.trades` |
| How are our fills reported? Sync or async? Partial? | strategy needs `Fill(order_id, side, price, qty)` | `State.fills` |
| Who tracks position/cash — them or us? | trust theirs if given | `State.position/cash` |
| Order IDs: ours or theirs? When do we learn them? | `Cancel` needs an id that is live | `State.open_orders` |
| Rate limit, position limit, max order size, max open orders? | one parameter each | `Limits(...)` |
| Fees / rebates? | changes the minimum profitable spread | strategy |
| What ends a round? Is state reset between rounds? | `runner.start()` at every reset | adapter |
| Time unit of their clock? | `msg_window` is in `State.t` units | `State.t` |

## 2. Build it

Pull style (they give us an API to call):

```python
# arena/comp_adapter.py
from arena.core import Adapter, State, OpenOrder, Fill, Submit, Cancel
from arena.runner import Runner
from arena.safety import Limits, Safety

class CompAdapter(Adapter):
    def poll(self):               # return a State, or None when the round ends
        ...
    def execute(self, actions):   # Submit -> their place-order; Cancel -> their cancel
        ...

runner = Runner(MyStrategy(), Safety(Limits(max_position=..., max_msgs=...)))
runner.run(CompAdapter(...))
runner.recorder.save("round1.json")      # -> python -m arena.bakeoff --load round1.json
```

Push style (we submit a class and they call it):

```python
class TheirBotClass:                     # whatever name/signature they require
    def __init__(self):
        self.runner = Runner(MyStrategy(), Safety(Limits(...)))
        self.runner.start()
    def on_tick(self, their_data):       # their callback
        state = to_state(their_data)     # the only translation code
        for a in self.runner.step(state):
            send(a)                      # their order API
```

## 3. Check it before trusting it

- Print one `State` and compare to their raw message by hand.
- Place one order far from the market, see it in `open_orders`, cancel it, see it gone.
- Cross the spread with the minimum size once: a `Fill` arrives, `position` changes by that size.
- Run `QuoteAroundMid` with a tight `max_position` for a few minutes. `max|pos|` must never exceed it.
- Save the log and run `python -m arena.bakeoff --load` on it.

## 4. Prompt for the AI (paste with their spec)

> Here is the exchange API spec [paste]. Here is our interface, `arena/core.py` [paste].
> Write `arena/comp_adapter.py` implementing `Adapter` (or, if the API calls us, a
> class matching their required signature that calls `Runner.step`). Map their
> book to `bids/asks` as `(price_in_ticks, qty)` best first; trades to
> `(price, qty, aggressor_side_or_None)`; our executions to `Fill`; our resting
> orders to `open_orders`. Do not add any trading logic. List every assumption
> you had to make about the spec at the top of the file.
