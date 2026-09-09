"""The spec. Run `pytest -x` and work down the failures in order.

Read a test before you try to pass it — each one encodes a real rule about
how exchanges work, not an arbitrary API convention.
"""

import random

import pytest

from orderbook import OrderBook, Side


@pytest.fixture
def book():
    return OrderBook()


# ----------------------------------------------------------------------
# 1. Resting: an order that can't match sits in the book
# ----------------------------------------------------------------------

def test_lone_order_rests_and_makes_no_trades(book):
    oid, trades = book.submit_limit(Side.BUY, price=99, qty=10)
    assert trades == []
    assert book.best_bid() == 99
    assert book.best_ask() is None
    assert book.get(oid).remaining == 10


def test_bbo_spread_and_mid(book):
    book.submit_limit(Side.BUY, 99, 10)
    book.submit_limit(Side.SELL, 101, 10)
    assert (book.best_bid(), book.best_ask()) == (99, 101)
    assert book.spread() == 2
    assert book.mid() == 100.0
    book.check_invariant()


# ----------------------------------------------------------------------
# 2. Matching basics
# ----------------------------------------------------------------------

def test_exact_match_clears_both_orders(book):
    maker, _ = book.submit_limit(Side.SELL, 100, 10)
    taker, trades = book.submit_limit(Side.BUY, 100, 10)

    assert len(trades) == 1
    t = trades[0]
    assert (t.price, t.qty, t.maker_id, t.taker_id) == (100, 10, maker, taker)
    assert book.best_bid() is None and book.best_ask() is None
    book.check_invariant()


def test_trade_happens_at_the_resting_price_not_the_incoming_price(book):
    """A buy at 105 into an ask resting at 100 pays 100, not 105.

    The resting order set the terms; the incoming order merely accepted them.
    Get this backwards and every PnL number you compute later will be wrong.
    """
    book.submit_limit(Side.SELL, 100, 10)
    _, trades = book.submit_limit(Side.BUY, 105, 10)
    assert trades[0].price == 100


def test_no_match_when_prices_do_not_cross(book):
    book.submit_limit(Side.SELL, 101, 10)
    _, trades = book.submit_limit(Side.BUY, 100, 10)
    assert trades == []
    assert book.best_bid() == 100 and book.best_ask() == 101


# ----------------------------------------------------------------------
# 3. Price-time priority
# ----------------------------------------------------------------------

def test_oldest_order_at_a_price_level_fills_first(book):
    first, _ = book.submit_limit(Side.SELL, 100, 10)
    second, _ = book.submit_limit(Side.SELL, 100, 10)

    _, trades = book.submit_limit(Side.BUY, 100, 10)
    assert trades[0].maker_id == first
    assert book.get(second).remaining == 10


def test_better_price_fills_before_older_order(book):
    """Price beats time: a newer, cheaper ask jumps the queue."""
    old_expensive, _ = book.submit_limit(Side.SELL, 101, 10)
    new_cheap, _ = book.submit_limit(Side.SELL, 100, 10)

    _, trades = book.submit_limit(Side.BUY, 101, 10)
    assert trades[0].maker_id == new_cheap


# ----------------------------------------------------------------------
# 4. Partial fills and sweeping
# ----------------------------------------------------------------------

def test_incoming_order_partially_fills_a_large_resting_order(book):
    maker, _ = book.submit_limit(Side.SELL, 100, 100)
    _, trades = book.submit_limit(Side.BUY, 100, 30)

    assert trades[0].qty == 30
    assert book.get(maker).remaining == 70
    assert book.best_ask() == 100


def test_leftover_quantity_rests_in_the_book(book):
    book.submit_limit(Side.SELL, 100, 30)
    oid, trades = book.submit_limit(Side.BUY, 100, 50)

    assert sum(t.qty for t in trades) == 30
    assert book.best_ask() is None
    assert book.best_bid() == 100
    assert book.get(oid).remaining == 20
    book.check_invariant()


def test_sweep_across_levels_pays_each_level_its_own_price(book):
    book.submit_limit(Side.SELL, 100, 10)
    book.submit_limit(Side.SELL, 101, 10)
    book.submit_limit(Side.SELL, 102, 10)

    _, trades = book.submit_limit(Side.BUY, 102, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10), (101, 10), (102, 5)]
    assert book.best_ask() == 102
    book.check_invariant()


def test_sweep_stops_at_the_limit_price(book):
    book.submit_limit(Side.SELL, 100, 10)
    book.submit_limit(Side.SELL, 103, 10)

    oid, trades = book.submit_limit(Side.BUY, 101, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10)]
    assert book.best_bid() == 101  # the unfilled 15 rests here
    assert book.get(oid).remaining == 15
    book.check_invariant()


def test_sell_side_sweeps_downward(book):
    """Same logic mirrored: a sell walks bids from the highest price down."""
    book.submit_limit(Side.BUY, 100, 10)
    book.submit_limit(Side.BUY, 99, 10)
    book.submit_limit(Side.BUY, 98, 10)

    _, trades = book.submit_limit(Side.SELL, 99, 25)

    assert [(t.price, t.qty) for t in trades] == [(100, 10), (99, 10)]
    assert book.best_bid() == 98
    assert book.best_ask() == 99  # remaining 5 rests
    book.check_invariant()


def test_emptied_price_level_is_removed(book):
    book.submit_limit(Side.SELL, 100, 10)
    book.submit_limit(Side.BUY, 100, 10)
    assert book.best_ask() is None
    book.check_invariant()


# ----------------------------------------------------------------------
# 5. Cancels
# ----------------------------------------------------------------------

def test_cancel_removes_the_order(book):
    oid, _ = book.submit_limit(Side.BUY, 99, 10)
    assert book.cancel(oid) is True
    assert book.best_bid() is None
    book.check_invariant()


def test_cancel_leaves_other_orders_at_the_same_level(book):
    first, _ = book.submit_limit(Side.BUY, 99, 10)
    second, _ = book.submit_limit(Side.BUY, 99, 10)

    book.cancel(first)
    _, trades = book.submit_limit(Side.SELL, 99, 10)
    assert trades[0].maker_id == second


def test_cancel_unknown_or_already_filled_returns_false(book):
    assert book.cancel(999) is False

    oid, _ = book.submit_limit(Side.SELL, 100, 10)
    book.submit_limit(Side.BUY, 100, 10)
    assert book.cancel(oid) is False


# ----------------------------------------------------------------------
# 6. Market data
# ----------------------------------------------------------------------

def test_l2_aggregates_quantity_and_orders_best_first(book):
    book.submit_limit(Side.BUY, 99, 10)
    book.submit_limit(Side.BUY, 99, 5)
    book.submit_limit(Side.BUY, 98, 20)
    book.submit_limit(Side.SELL, 101, 7)
    book.submit_limit(Side.SELL, 102, 3)

    bids, asks = book.l2(depth=5)
    assert bids == [(99, 15), (98, 20)]
    assert asks == [(101, 7), (102, 3)]


def test_l2_respects_depth_limit(book):
    for price in range(90, 100):
        book.submit_limit(Side.BUY, price, 1)
    bids, _ = book.l2(depth=3)
    assert bids == [(99, 1), (98, 1), (97, 1)]


# ----------------------------------------------------------------------
# 7. Fuzz: the invariants that must hold no matter what
# ----------------------------------------------------------------------

def test_random_flow_never_crosses_the_book_and_conserves_quantity():
    rng = random.Random(20261106)
    book = OrderBook()
    live: list[int] = []
    bought = sold = 0

    for _ in range(4000):
        if live and rng.random() < 0.25:
            book.cancel(live.pop(rng.randrange(len(live))))
        else:
            side = rng.choice([Side.BUY, Side.SELL])
            price = rng.randint(95, 105)
            qty = rng.randint(1, 20)
            oid, trades = book.submit_limit(side, price, qty)
            traded = sum(t.qty for t in trades)
            bought += traded
            sold += traded
            if book.get(oid) is not None:
                live.append(oid)
        book.check_invariant()

    assert bought == sold
    assert bought > 0, "the fuzz never traded — something is wrong"
