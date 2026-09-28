"""Tests for the interface, safety layer, runner, sandbox adapter, placeholder.

Run: python -m pytest -q
"""

import pytest

from arena.core import Cancel, OpenOrder, State, Strategy, Submit
from arena.runner import Runner
from arena.safety import Limits, Safety
from arena.strategies import QuoteAroundMid
from sandbox.adapter import SandboxAdapter

BOOK = dict(bids=[(99, 10)], asks=[(101, 10)])      # mid 100


def st(t=0, position=0, open_orders=(), cash=0.0, **kw):
    args = dict(BOOK)
    args.update(kw)
    return State(t=t, position=position, cash=cash,
                 open_orders={o.id: o for o in open_orders}, **args)


# ----------------------------------------------------------------------
# Safety
# ----------------------------------------------------------------------

def test_no_limits_passes_everything_through():
    acts = [Submit("buy", 99, 10), Submit("sell", 101, 10)]
    assert Safety().check(acts, st()) == acts


def test_position_limit_counts_the_order_itself():
    s = Safety(Limits(max_position=100))
    out = s.check([Submit("buy", 99, 20), Submit("buy", 99, 10)], st(position=90))
    assert out == [Submit("buy", 99, 10)]
    assert s.rejects["position"] == 1


def test_position_limit_counts_resting_orders():
    s = Safety(Limits(max_position=100))
    resting = OpenOrder(1, "buy", 98, 10)
    assert s.check([Submit("buy", 99, 10)], st(position=85, open_orders=[resting])) == []


def test_orders_that_reduce_position_are_allowed_at_the_limit():
    s = Safety(Limits(max_position=100))
    assert s.check([Submit("sell", 101, 50)], st(position=100)) == [Submit("sell", 101, 50)]


def test_cancel_replace_near_the_limit_depends_on_trust_cancels():
    resting = OpenOrder(1, "buy", 98, 10)
    acts = [Cancel(1), Submit("buy", 99, 10)]
    state = st(position=85, open_orders=[resting])
    strict = Safety(Limits(max_position=100)).check(acts, state)
    trusting = Safety(Limits(max_position=100, trust_cancels=True)).check(acts, state)
    assert strict == [Cancel(1)]                       # old order might still fill
    assert trusting == acts


def test_cancels_are_sent_before_submits():
    resting = OpenOrder(1, "buy", 98, 10)
    out = Safety().check([Submit("buy", 99, 10), Cancel(1)], st(open_orders=[resting]))
    assert out == [Cancel(1), Submit("buy", 99, 10)]


def test_cancel_of_unknown_or_duplicate_order_is_dropped():
    resting = OpenOrder(1, "buy", 98, 10)
    s = Safety()
    assert s.check([Cancel(1), Cancel(1), Cancel(7)], st(open_orders=[resting])) == [Cancel(1)]
    assert s.rejects["unknown_order"] == 2


def test_rate_limit_caps_messages_per_window_and_then_resets():
    s = Safety(Limits(max_msgs=3, msg_window=1.0))
    acts = [Submit("buy", 99 - i, 1) for i in range(5)]
    assert len(s.check(acts, st(t=0))) == 3
    assert s.rejects["rate"] == 2
    assert len(s.check(acts, st(t=0.5))) == 0          # same window, budget spent
    assert len(s.check(acts, st(t=1.0))) == 3          # new window


def test_order_size_price_and_open_order_limits():
    s = Safety(Limits(max_order_qty=10, max_price_dev=5, max_open_orders=2))
    resting = OpenOrder(1, "buy", 98, 10)
    out = s.check([Submit("buy", 99, 11),              # too big
                   Submit("buy", 90, 5),               # too far from mid 100
                   Submit("sell", 102, 5),             # ok, now 2 open
                   Submit("sell", 103, 5)],            # too many open
                  st(open_orders=[resting]))
    assert out == [Submit("sell", 102, 5)]
    assert s.rejects == {"order_qty": 1, "price_dev": 1, "open_orders": 1}


def test_kill_switch_cancels_everything_and_blocks_submits():
    s = Safety()
    s.kill()
    orders = [OpenOrder(1, "buy", 98, 10), OpenOrder(2, "sell", 102, 10)]
    out = s.check([Submit("buy", 99, 10)], st(open_orders=orders))
    assert set(out) == {Cancel(1), Cancel(2)}


def test_max_loss_trips_the_kill_switch():
    s = Safety(Limits(max_loss=500))
    s.check([], st(position=10, cash=-1600))           # pnl = -1600 + 10*100 = -600
    assert s.killed and s.kill_reason == "max_loss"


# ----------------------------------------------------------------------
# Placeholder strategy
# ----------------------------------------------------------------------

def test_placeholder_quotes_both_sides_then_stays_quiet():
    q = QuoteAroundMid(half_spread=2, size=10)
    assert set(q.on_tick(st())) == {Submit("buy", 98, 10), Submit("sell", 102, 10)}
    mine = [OpenOrder(1, "buy", 98, 10), OpenOrder(2, "sell", 102, 10)]
    assert q.on_tick(st(open_orders=mine)) == []       # nothing changed, no messages


def test_placeholder_moves_quotes_when_mid_moves():
    q = QuoteAroundMid(half_spread=2, size=10)
    mine = [OpenOrder(1, "buy", 98, 10), OpenOrder(2, "sell", 102, 10)]
    acts = q.on_tick(st(open_orders=mine, bids=[(100, 5)], asks=[(102, 5)]))  # mid 101
    assert set(acts) == {Cancel(1), Cancel(2), Submit("buy", 99, 10), Submit("sell", 103, 10)}


# ----------------------------------------------------------------------
# Runner + sandbox adapter
# ----------------------------------------------------------------------

def test_runner_fills_features_and_logs_every_tick():
    r = Runner()
    r.start()
    s = st(trades=[(101, 5, None)])
    r.step(s)
    assert s.x is not None and len(r.recorder.segments[-1]) == 1


def test_do_nothing_strategy_never_trades():
    r = Runner().run(SandboxAdapter(seed=1, ticks=200))
    assert r["fills"] == 0 and r["position"] == 0 and r["messages"] == 0


def test_pnl_sums_to_zero_with_us_in_the_market():
    ad = SandboxAdapter(seed=2, ticks=300)
    Runner(QuoteAroundMid()).run(ad)
    mark = (ad.book.best_bid() + ad.book.best_ask()) / 2
    assert sum(t.pnl(mark) for t in ad.traders()) == pytest.approx(0, abs=1e-6)


class _Collect(QuoteAroundMid):
    def __init__(self):
        super().__init__()
        self.fills = []

    def on_fill(self, f):
        self.fills.append(f)


def test_reported_fills_add_up_to_the_position():
    ad, strat = SandboxAdapter(seed=3, ticks=300), _Collect()
    Runner(strat).run(ad)
    ad.ticks += 1                                      # one more poll to collect the last fills
    last = ad.poll()
    strat.fills.extend(last.fills)
    signed = sum(f.qty if f.side == "buy" else -f.qty for f in strat.fills)
    assert strat.fills and signed == last.position


def test_runs_are_reproducible():
    a = Runner(QuoteAroundMid()).run(SandboxAdapter(seed=5, ticks=200))
    b = Runner(QuoteAroundMid()).run(SandboxAdapter(seed=5, ticks=200))
    assert a == b


def test_position_limit_holds_in_a_live_run():
    lim = 30
    ad = SandboxAdapter(seed=1, ticks=400)
    r = Runner(QuoteAroundMid(), Safety(Limits(max_position=lim))).run(ad)
    assert r["max_abs_position"] <= lim and abs(ad.me.position) <= lim
