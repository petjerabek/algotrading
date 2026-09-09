# Matching engine — evening 1

```
pip install sortedcontainers pytest
pytest -x
```

`-x` stops at the first failure. Work down the list in order; the tests are
arranged so each one builds on the last.

## What you're writing

Three methods in `orderbook.py`. Nothing else in that file needs to change.

| Method | Time | Why it matters |
|---|---|---|
| `l2()` | 10 min | Warm-up. Also shows you exactly how little a bot gets to see. |
| `cancel()` | 15 min | Teaches why cancels are cheap and modifies are not. |
| `submit_limit()` | 1–2 h | The whole exercise. |

Do `l2` first — it's a gentle way to get familiar with the internal structures
before the hard one.

## Order of attack for `submit_limit`

Don't try to write it all at once. Get these passing in sequence:

1. **Rest only.** Ignore matching entirely — just create the order and rest it.
   Passes tests 1–2 and `test_no_match_when_prices_do_not_cross`.
2. **Single match, exact quantities.** One resting order, one incoming, equal
   size. Passes the exact-match and resting-price tests.
3. **Partial fills.** Use `min(incoming.remaining, resting.remaining)` and
   decrement both. Handle the resting order hitting zero.
4. **Walk the level queue**, then **walk multiple levels**. Two nested loops:
   outer over price levels, inner over the FIFO queue at that level.
5. **Rest the leftover.**

## Hints, in increasing order of spoiler

- `SortedDict.peekitem(0)` gives the lowest key, `peekitem(-1)` the highest.
  Both return `(key, value)`.
- Best opposite level: index `0` when you're buying (lowest ask), `-1` when
  you're selling (highest bid).
- The stopping condition and the matching condition are the same test. A buy
  keeps going while `best_ask <= price`; a sell while `best_bid >= price`.
- The outer loop needs to `break` when the price no longer crosses, but
  `continue` naturally when a level is exhausted. Deleting the empty level is
  what advances you to the next one.

## When you get stuck

Print the book. `print(book)` gives you an ASCII ladder — asks above, bids
below. Nearly every bug becomes obvious the moment you look at the ladder
before and after the failing call.

The two failures that eat the most time:

- **Mutating the SortedDict while iterating it.** Don't iterate the keys;
  repeatedly peek at the best one and delete it when empty.
- **Trading at `order.price` instead of `resting.price`.** The test catches it,
  but it's worth understanding rather than just patching: the resting order
  advertised a price and the incoming order accepted that advertisement.

## Done. Now what?

Once `pytest` is green you have a working exchange. Before moving on, play with
it in a REPL for ten minutes — build a book by hand, sweep it, print the ladder
after each step. You want the mental picture, not just passing tests.

Then the next two pieces:

**Synthetic traders.** A hidden fair value doing a random walk, plus agents
posting limits around it and occasionally crossing the spread. This gives you a
market to trade against.

**An agent interface.** Callbacks — `on_book_update(l2)`, `on_trade(price, qty)`,
`on_fill(order_id, price, qty)` — so a bot can plug in. This is the shape of the
API Qminers will hand you in November.

**Then your first market maker.** Quote `fair ± k`, refresh on each update,
track `cash + position * mid` live, and watch what the noise traders do to you.
