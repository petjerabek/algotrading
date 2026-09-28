"""Safety layer: sits between the strategy and the exchange.

Every limit is one parameter. Unknown until the Technical Rules arrive, so all
default to None (= off). Set them the moment you know the real numbers, with
some headroom.

The strategy never talks to the exchange directly; every action passes
through Safety.check(). Rejected actions are dropped and counted, never
silently modified.
"""

from collections import Counter, deque
from dataclasses import dataclass

from arena.core import Cancel, Submit


@dataclass
class Limits:
    max_position: int = None      # |position| may never exceed this, even if every open order fills
    max_order_qty: int = None     # per order
    max_open_orders: int = None   # resting at once
    max_msgs: int = None          # submits + cancels per msg_window
    msg_window: float = 1.0       # in State.t units (ticks in the sandbox, seconds live)
    max_price_dev: int = None     # fat-finger guard: reject prices further than this from mid
    max_loss: float = None        # kill switch trips when PnL < -max_loss
    trust_cancels: bool = False   # False: a cancel we just sent still counts toward
                                  # position risk (it may lose the race with a fill)


class Safety:
    def __init__(self, limits=None):
        self.limits = limits or Limits()
        self.killed = False
        self.kill_reason = None
        self.sent = deque()           # timestamps of messages sent
        self.rejects = Counter()      # reason -> count
        self.messages = 0

    def kill(self, reason="manual"):
        """Stop quoting. Every later check() returns cancels for all open orders."""
        if not self.killed:
            self.killed, self.kill_reason = True, reason

    # ------------------------------------------------------------------

    def _rate_ok(self, t):
        lim = self.limits
        if lim.max_msgs is None:
            return True
        while self.sent and self.sent[0] <= t - lim.msg_window:
            self.sent.popleft()
        return len(self.sent) < lim.max_msgs

    def _send(self, t):
        self.sent.append(t)
        self.messages += 1

    def check(self, actions, state):
        """Return the subset of actions that is safe to send, in send order."""
        lim = self.limits

        if lim.max_loss is not None:
            p = state.pnl()
            if p is not None and p < -lim.max_loss:
                self.kill("max_loss")

        if self.killed:
            actions = [Cancel(i) for i in state.open_orders]
            out = []
            for a in actions:             # kill-switch cancels ignore the rate limit
                self._send(state.t)
                out.append(a)
            return out

        cancels = [a for a in actions if isinstance(a, Cancel)]
        submits = [a for a in actions if isinstance(a, Submit)]
        out = []

        # Cancels first: they only ever reduce risk.
        cancelled = set()
        for a in cancels:
            if a.order_id not in state.open_orders or a.order_id in cancelled:
                self.rejects["unknown_order"] += 1
                continue
            if not self._rate_ok(state.t):
                self.rejects["rate"] += 1
                continue
            self._send(state.t)
            cancelled.add(a.order_id)
            out.append(a)

        # Worst-case exposure from orders still resting.
        live = {i: o for i, o in state.open_orders.items()
                if not (lim.trust_cancels and i in cancelled)}
        open_buy = sum(o.qty for o in live.values() if o.side == "buy")
        open_sell = sum(o.qty for o in live.values() if o.side == "sell")
        n_open = len(state.open_orders) - len(cancelled)
        mid = state.mid

        for a in submits:
            reason = None
            if a.side not in ("buy", "sell") or a.qty <= 0:
                reason = "malformed"
            elif lim.max_order_qty is not None and a.qty > lim.max_order_qty:
                reason = "order_qty"
            elif (lim.max_price_dev is not None and mid is not None
                  and abs(a.price - mid) > lim.max_price_dev):
                reason = "price_dev"
            elif lim.max_open_orders is not None and n_open >= lim.max_open_orders:
                reason = "open_orders"
            elif lim.max_position is not None and (
                    (a.side == "buy" and state.position + open_buy + a.qty > lim.max_position) or
                    (a.side == "sell" and state.position - open_sell - a.qty < -lim.max_position)):
                reason = "position"
            elif not self._rate_ok(state.t):
                reason = "rate"

            if reason:
                self.rejects[reason] += 1
                continue
            self._send(state.t)
            n_open += 1
            if a.side == "buy":
                open_buy += a.qty
            else:
                open_sell += a.qty
            out.append(a)
        return out
