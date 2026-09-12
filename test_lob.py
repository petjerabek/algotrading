"""Tests for lob.py — the matching engine.

Run with:  pytest -q

Each test states a rule about how exchanges work. When one fails, read the
name and the docstring before you touch the code — the test is usually right.

Convention used throughout: prices are integers (ticks) and quantities are
chosen so they can never be mistaken for prices. That is deliberate. A test
where price and quantity are interchangeable can pass with the wrong field.
"""

import random

import pytest

from lob import Book


@pytest.fixture
def b():
    return Book()


# ======================================================================
# Resting: an order that cannot match sits in the book
# ======================================================================

def test_lone_order_rests_and_trades_nothing(b):
    oid, trades = b.submit("buy", 99, 10)
    assert trades == []
    assert b.best_bid() == 99
    assert b.best_ask() is None
    assert b.orders[oid].remaining == 10
    assert b.orders[oid].status == "resting"
    b.check()


def test_best_bid_and_ask_pick_the_right_extremes(b):
    b.submit("buy", 97, 200)
    b.submit("buy", 99, 10)
    b.submit("sell", 101, 7)
    b.submit("sell", 104, 300)
    assert b.best_bid() == 99
    assert b.best_ask() == 101
    b.check()


def test_empty_book_has_no_prices(b):
    assert b.best_bid() is None
    assert b.best_ask() is None
    assert b.l2() == ([], [])
    b.check()


# ======================================================================
# Matching basics
# ======================================================================

def test_exact_match_clears_both_sides(b):
    maker, _ = b.submit("sell", 100, 10)
    taker, trades = b.submit("buy", 100, 10)

    assert len(trades) == 1
    t = trades[0]
    assert (t.price, t.qty, t.resting_id, t.incoming_id) == (100, 10, maker, taker)
    assert b.best_bid() is None and b.best_ask() is None
    assert b.orders[maker].status == "filled"
    assert b.orders[taker].status == "filled"
    b.check()


def test_price_improvement_trade_uses_the_resting_price(b):
    """A buy at 105 into an ask resting at 100 pays 100.

    The resting order publicly advertised its terms; the incoming order
    accepted them. The limit price was a ceiling, not an offer.
    """
    b.submit("sell", 100, 10)
    _, trades = b.submit("buy", 105, 10)
    assert trades[0].price == 100


def test_no_trade_when_prices_do_not_cross(b):
    b.submit("sell", 101, 10)
    _, trades = b.submit("buy", 100, 10)
    assert trades == []
    assert (b.best_bid(), b.best_ask()) == (100, 101)
    b.check()


# ======================================================================
# Price-time priority
# ======================================================================

def test_oldest_order_at_a_price_fills_first(b):
    first, _ = b.submit("sell", 100, 10)
    second, _ = b.submit("sell", 100, 10)

    _, trades = b.submit("buy", 100, 10)
    assert trades[0].resting_id == first
    assert b.orders[second].remaining == 10


def test_better_price_beats_earlier_arrival(b):
    """Price is the first sort key; time only breaks ties within a level."""
    old_expensive, _ = b.submit("sell", 101, 10)
    new_cheap, _ = b.submit("sell", 100, 10)

    _, trades = b.submit("buy", 101, 10)
    assert trades[0].resting_id == new_cheap


def test_matchable_returns_eligible_orders_in_fill_order(b):
    b.submit("sell", 101, 5)   # id 1
    b.submit("sell", 100, 5)   # id 2  cheaper, arrived later
    b.submit("sell", 101, 5)   # id 3  ties id 1 on price, arrived later
    b.submit("sell", 105, 5)   # id 4  above the buyer's limit
    b.submit("buy", 98, 5)     # id 5  wrong side

    assert [o.id for o in b.matchable("buy", 102)] == [2, 1, 3]


def test_matchable_mirrors_correctly_for_an_incoming_sell(b):
    b.submit("buy", 99, 5)     # id 1
    b.submit("buy", 100, 5)    # id 2  higher, arrived later
    b.submit("buy", 99, 5)     # id 3
    b.submit("buy", 95, 5)     # id 4  below the seller's limit

    assert [o.id for o in b.matchable("sell", 98)] == [2, 1, 3]


def test_matchable_is_a_pure_query(b):
    b.submit("sell", 100, 10)
    before = [(o.id, o.remaining) for o in b.live_orders()]
    b.matchable("buy", 105)
    assert [(o.id, o.remaining) for o in b.live_orders()] == before
    assert b.trades == []


# ======================================================================
# Partial fills and sweeping
# ======================================================================

def test_small_order_partially_fills_a_large_resting_order(b):
    maker, _ = b.submit("sell", 100, 100)
    _, trades = b.submit("buy", 100, 30)

    assert trades[0].qty == 30
    assert b.orders[maker].remaining == 70
    assert b.orders[maker].status == "resting"
    assert b.best_ask() == 100
    b.check()


def test_unfilled_remainder_rests_in_the_book(b):
    b.submit("sell", 100, 30)
    oid, trades = b.submit("buy", 100, 50)

    assert sum(t.qty for t in trades) == 30
    assert b.best_ask() is None
    assert b.best_bid() == 100
    assert b.orders[oid].remaining == 20
    b.check()


def test_sweep_pays_each_level_its_own_price(b):
    b.submit("sell", 100, 10)
    b.submit("sell", 101, 10)
    b.submit("sell", 103, 10)

    _, trades = b.submit("buy", 102, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10), (101, 10)]
    assert b.best_ask() == 103
    assert b.best_bid() == 102        # the unfilled 5 rests
    b.check()


def test_sweep_stops_at_the_limit_price(b):
    b.submit("sell", 100, 10)
    b.submit("sell", 103, 10)

    oid, trades = b.submit("buy", 101, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10)]
    assert b.orders[oid].remaining == 15
    assert b.best_bid() == 101
    b.check()


def test_sell_sweeps_bids_from_the_top_down(b):
    b.submit("buy", 100, 10)
    b.submit("buy", 99, 10)
    b.submit("buy", 98, 10)

    _, trades = b.submit("sell", 99, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10), (99, 10)]
    assert b.best_bid() == 98
    assert b.best_ask() == 99
    b.check()


def test_marketable_order_never_trades_with_itself(b):
    """The incoming order must not be visible to its own matching pass."""
    b.submit("buy", 100, 10)
    oid, trades = b.submit("sell", 100, 30)
    assert all(t.resting_id != oid for t in trades)
    assert sum(t.qty for t in trades) == 10


# ======================================================================
# Cancels
# ======================================================================

def test_cancel_removes_an_order_from_the_live_book(b):
    oid, _ = b.submit("buy", 99, 10)
    assert b.cancel(oid) is True
    assert b.best_bid() is None
    assert b.orders[oid].status == "cancelled"
    b.check()


def test_cancel_preserves_the_queue_position_of_survivors(b):
    """Removing the middle of a queue must not reorder the rest."""
    first, _ = b.submit("buy", 99, 10)
    middle, _ = b.submit("buy", 99, 20)
    last, _ = b.submit("buy", 99, 5)

    assert b.cancel(middle) is True
    assert b.l2()[0] == [(99, 15)]

    _, trades = b.submit("sell", 99, 15)
    assert [(t.resting_id, t.qty) for t in trades] == [(first, 10), (last, 5)]
    b.check()


def test_cancel_is_idempotent_and_rejects_unknown_ids(b):
    oid, _ = b.submit("buy", 99, 10)
    assert b.cancel(oid) is True
    assert b.cancel(oid) is False
    assert b.cancel(999) is False
    assert b.cancel(0) is False       # must not index from the end


def test_cannot_cancel_a_filled_order(b):
    maker, _ = b.submit("sell", 100, 10)
    b.submit("buy", 100, 10)
    assert b.cancel(maker) is False
    assert b.orders[maker].status == "filled"


def test_partially_filled_order_can_still_be_cancelled(b):
    maker, _ = b.submit("sell", 100, 100)
    b.submit("buy", 100, 30)
    assert b.cancel(maker) is True
    assert b.best_ask() is None
    b.check()


def test_cancelled_order_is_not_matchable(b):
    oid, _ = b.submit("sell", 100, 10)
    b.cancel(oid)
    _, trades = b.submit("buy", 105, 10)
    assert trades == []


# ======================================================================
# Market data (L2)
# ======================================================================

def test_l2_aggregates_by_price_level(b):
    b.submit("buy", 99, 10)
    b.submit("buy", 99, 5)
    b.submit("buy", 98, 200)
    b.submit("sell", 101, 7)
    b.submit("sell", 102, 300)

    bids, asks = b.l2()
    assert bids == [(99, 15), (98, 200)]
    assert asks == [(101, 7), (102, 300)]


def test_l2_orders_both_sides_best_first(b):
    """bids[0] and asks[0] are always the top of book, on either side."""
    for price in (95, 97, 99):
        b.submit("buy", price, 1)
    for price in (101, 103, 105):
        b.submit("sell", price, 1)

    bids, asks = b.l2()
    assert bids[0][0] == 99 == b.best_bid()
    assert asks[0][0] == 101 == b.best_ask()
    assert [p for p, _ in bids] == sorted([p for p, _ in bids], reverse=True)
    assert [p for p, _ in asks] == sorted([p for p, _ in asks])


def test_l2_truncates_to_depth(b):
    for price in range(90, 100):
        b.submit("buy", price, 1)
    bids, _ = b.l2(depth=3)
    assert bids == [(99, 1), (98, 1), (97, 1)]


def test_l2_full_ignores_depth(b):
    for price in range(90, 100):
        b.submit("buy", price, 1)
    bids, _ = b.l2(depth=3, full=True)
    assert len(bids) == 10


def test_l2_excludes_filled_and_cancelled_orders(b):
    b.submit("sell", 100, 10)
    b.submit("buy", 100, 10)          # fills the above
    doomed, _ = b.submit("sell", 102, 5)
    b.cancel(doomed)
    b.submit("sell", 103, 8)

    _, asks = b.l2()
    assert asks == [(103, 8)]


# ======================================================================
# Bookkeeping
# ======================================================================

def test_trade_log_accumulates_flat(b):
    b.submit("sell", 100, 10)
    b.submit("sell", 101, 10)
    b.submit("buy", 101, 15)

    assert len(b.trades) == 2
    assert all(hasattr(t, "price") for t in b.trades), "log must be flat, not nested"


def test_every_order_is_recorded_even_if_it_never_rests(b):
    """The order dict is an audit log: orders filled on arrival still appear."""
    b.submit("sell", 100, 10)
    taker, _ = b.submit("buy", 100, 10)
    assert taker in b.orders
    assert len(b.orders) == 2


def test_status_reflects_how_an_order_ended(b):
    resting, _ = b.submit("buy", 98, 10)
    doomed, _ = b.submit("buy", 97, 10)
    b.cancel(doomed)
    maker, _ = b.submit("sell", 99, 10)
    b.submit("buy", 99, 10)

    assert b.orders[resting].status == "resting"
    assert b.orders[doomed].status == "cancelled"
    assert b.orders[maker].status == "filled"


def test_ids_are_unique_and_increasing(b):
    ids = [b.submit("buy", 90 + i, 1)[0] for i in range(10)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 10


# ======================================================================
# Invariants under random flow
# ======================================================================

@pytest.mark.parametrize("seed", [1, 7, 20261106, 99999])
def test_random_flow_holds_every_invariant(seed):
    rng = random.Random(seed)
    b = Book()
    live = []

    for _ in range(5000):
        if live and rng.random() < 0.3:
            b.cancel(live.pop(rng.randrange(len(live))))
        else:
            side = rng.choice(["buy", "sell"])
            oid, _ = b.submit(side, rng.randint(95, 105), rng.randint(1, 20))
            if b.orders[oid].remaining > 0:
                live.append(oid)
        b.check()

    assert b.trades, "the fuzz never traded — check the price range"
    assert all(t.qty > 0 for t in b.trades), "zero-quantity trade emitted"

    filled = {}
    for t in b.trades:
        filled[t.resting_id] = filled.get(t.resting_id, 0) + t.qty
        filled[t.incoming_id] = filled.get(t.incoming_id, 0) + t.qty

    for oid, got in filled.items():
        o = b.orders[oid]
        assert got <= o.qty, f"order {oid} overfilled: {got} > {o.qty}"
        if not o.cancelled:
            assert o.remaining == o.qty - got, f"order {oid} accounting mismatch"


def test_engine_is_deterministic():
    """Same inputs, same trades. Without this, bugs are unreproducible."""
    def run():
        rng = random.Random(42)
        b = Book()
        for _ in range(2000):
            b.submit(rng.choice(["buy", "sell"]), rng.randint(95, 105), rng.randint(1, 20))
        return [(t.price, t.qty, t.resting_id, t.incoming_id) for t in b.trades]

    assert run() == run()


def test_rest_bypasses_matching_and_can_cross_the_book(b):
    """`rest` is a seeding backdoor, not a trading path. Documented so it
    doesn't surprise you later — real orders must go through `submit`."""
    b.rest("buy", 100, 10)
    b.rest("sell", 99, 10)
    with pytest.raises(AssertionError):
        b.check()
