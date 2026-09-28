# Algo Trade Arena prep — project context

Read this first in every session. Keep it current: update it at the end of any
session that changes a decision, a result, or the code's API.

## The event

- Qminers × Matfyzák, **7–8 November 2026**, Impakt building, MFF UK Troja.
  24 hours, in person, ~200 participants, teams of 2–4. Prizes 100k / 60k / 40k CZK.
- Task: an algorithm trading **one asset** in a simulated limit order book.
  Qminers' own framing: "it's all about execution … only the quality of the
  strategy matters." Reading this as: latency is not scored.
- **Final submission must be Python.** AI tools are allowed during development
  unless the Technical Rules say otherwise. No help from anyone outside the
  team. Top solutions get **rerun for verification**, so results must be reproducible.
- **Technical Rules (format, scoring, limits, tie-breaks) are only published at
  the opening briefing.** Nothing tuned in advance survives, so prep = toolkit + speed.
- Unverified: an earlier version of arena.qminers.com mentioned 4 rounds of
  5 hours, escalating from forgiving to sophisticated. Not confirmed; check at the briefing.
- Full text: `docs/rules-official.md`.

## How Petr wants to work

- Guided sessions for anything that builds understanding: Claude gives the spec,
  the checks, and the expected output; **Petr writes the code**. Point at bugs, don't fix them.
- Claude writes plumbing directly: adapters, logging, harnesses, test suites.
- Short answers. Prefer measurements over claims. Say so when a claim is unverified.

## Code in this repo

Repo: `~/Projects/algotrading` on Petr's Mac (git, remote `origin/main`). The code
lives only there; project knowledge holds just this file and the rules. When a
session needs code, ask Petr to connect that folder.

Layout (full guide in `README.md`). Run everything from the repo root with `python -m`.

| path | what |
|---|---|
| `arena/` | **competition toolkit.** Never imports `sandbox/`. |
| `arena/core.py` | the boundary: `State`, `Submit`, `Cancel`, `OpenOrder`, `Fill`, `Strategy`, `Adapter` |
| `arena/runner.py` | `Runner.step(state)` per-tick pipeline, `Runner.run(adapter)` loop |
| `arena/safety.py` | `Limits` (all default off) + `Safety.check()`: position incl. resting orders, rate, size, fat-finger, open-order count, max-loss kill switch |
| `arena/signals.py` | mid, microprice, imbalance, OFI, TradeFlow, Volatility, `Features`, `OnlineRidge` |
| `arena/recorder.py` | `Recorder`: per-tick log, segments, save/load |
| `arena/bakeoff.py` | analysis of a saved log: horizon scan, out-of-sample per-feature table, fitted ridge, verdict. CLI `--load` |
| `arena/strategies.py` | `QuoteAroundMid` PLACEHOLDER (no skew, no model). Petr's strategy goes here |
| `sandbox/` | **practice only.** Petr's exchange + simulated market |
| `sandbox/lob.py` | Petr's matching engine, written from scratch |
| `sandbox/market.py` | FairValue random walk, Trader, Maker, Taker, MarketMaker (Petr's first bot), `dispatch`; demo under `__main__` |
| `sandbox/adapter.py` | `SandboxAdapter`: the sandbox behind the `Adapter` interface |
| `sandbox/run.py` | CLI: run a strategy (or `--strategy none` to observe), limits, `--save`, `--report` |
| `tests/` | `test_lob.py` (38, mutation-checked), `test_signals.py` (16), `test_harness.py` (19) |
| `rtg/` | Ready Trader Go practice bots + exchange config. Runs inside the RTG framework, not here |
| `docs/` | `rules-official.md`, `PLAYBOOK.md` (strategy of prep), `ADAPTER.md` (day-one checklist) |

Run: `python -m sandbox.run` (placeholder, per-seed table), `python -m sandbox.run --save r.json`
then `python -m arena.bakeoff --load r.json`, `python -m sandbox.run --strategy none --report`
(market without us + bake-off, ~10 s/seed), `python -m pytest -q` (73 tests; `pyproject.toml`
puts the root on the path).

Architecture: their API → adapter (only new code on the day, `arena/comp_adapter.py`) → `State` →
`Runner.step`: fills → `strategy.on_fill`; `features.update`; `state.x`; `recorder.record`;
`strategy.on_tick` → `safety.check` → actions → adapter. Pull APIs use `Runner.run(adapter)`;
push APIs call `runner.step(state)` from their callback. `runner.start()` at every
session/round reset.
Safety: `trust_cancels=False` by default, so a just-cancelled order still counts toward
position risk. Kill-switch cancels ignore the rate limit.

Signal pipeline, once per tick: `features.update(bids, asks, trades)` (trades as
`(price, qty, side_or_None)`, fed BEFORE the book so inferred sides use the pre-trade
mid) → `recorder.record(bids, asks, features)`. After a round: `analyse(segments,
horizon)` → if verdict is SIGNAL, `fair = mid + model.predict(features.vector(...))`.
`OnlineRidge(n, ridge=10, decay=1.0)`: standardises internally, intercept unpenalised,
`ridge` in pseudo-samples, `decay<1` fades old data, `score(xs, ys)` = R², `report()`
prints coefs in original units and per 1 sd.

`Book` API: `submit(side, price, qty, owner=None, ioc=False) -> (id, trades)`,
`cancel(id) -> bool`, `l2(depth=5, full=False) -> (bids, asks)` best-first
`(price, qty)` lists, `best_bid()`, `best_ask()`, `check()` (crossed-book
invariant), `sanity(max_distance)` (heuristic, debugging only).

Design decisions:
- `orders` is a dict keyed by id and is also the audit log. Nothing is ever
  removed. Liveness is **derived** from `remaining > 0`, never stored.
  `status` is a derived property; `cancelled` is the only stored flag.
- Trades execute at the **resting** order's price.
- `ioc=True` cancels any remainder, and marks the order cancelled rather than filled.
- `owner` is an opaque object. The engine never imports `market.py`.

Conventions: prices are integer ticks. Sides are the strings `"buy"`/`"sell"`.
Every random component gets its own seeded `random.Random`.

Known issue: `live_orders()` scans every order ever created, which makes it
quadratic. 84% of runtime. The fix is to split the live book from the log.
Deferred until runs get too slow.

## What we measured (in Petr's own sandbox)

- Makers' and takers' PnL sums to exactly zero, so dispatch is correct.
  Takers bleed and makers collect.
- Inventory skew `k=0.05`: max |position| fell from 280–690 to 40, and the
  PnL standard deviation across seeds fell 5–20×. Mean PnL rose.
- Quoting at mid ± *full* spread (a naming bug) halved PnL compared with a fixed ±2.
- **Full microprice loses to plain mid** (RMSE 1.25 vs 1.10 vs truth). The cause is
  the sandbox's uniform-size makers. Lesson: an estimator's value depends on the
  market, so measure it, don't assume it.
- Bake-off v2 (2026-09-28, 5 seeds × 2000 ticks, all out-of-sample):
  - **Horizon decides everything.** Ridge R² = +0.063 at h=1 (positive in all 5
    seeds), +0.025 at h=5 (one seed negative), ~0 at h=10. Longer horizons add
    random-walk noise to the target and drown the signal.
  - Useful features at h=1: `imb_1` (+, z≈9.5), `imb_3` (−, z≈8), `micro_dev`.
    `ofi` and `spread` carry nothing. `trade_flow` is negative: aggressive buying
    is followed by the mid reverting down (sandbox mechanics).
  - **mid + model is closer to the true value than mid** (RMSE 1.00 vs 1.10 on
    test rows). This is the real validation that the no-truth procedure works.
  - Inferring trade sides instead of knowing them costs little (R² 0.049 vs 0.051).
  - NOT yet measured: whether mid + model improves PnL. That's the next test.
- Harness (2026-09-28, placeholder ±2, size 10, 3 seeds × 2000 ticks): no limits →
  PnL mean 8.6k, sd 9.7k, max|pos| up to 570, one seed lost. `--max-position 50` →
  mean 11.3k, sd 1.1k. A hard position cap does what skew did. Also: with us in the
  market, bake-off R² at h=1 drops to 0.032 (from 0.051) — our own quotes are part of
  the book we measure.
- Bake-off v1 was retired: correlation can't rank `mid+0.5·imb` vs `mid+1.0·imb`
  (same score), seeds were glued together (fake jumps up to 58 ticks), and the
  +0.035 vs +0.019 "same winner" gap was inside noise.
- Always judge over several seeds. At 400 ticks, 1 seed in 10 lost money.

## Ready Trader Go (Optiver, HackMelbourne mirror)

- The ETF position limit (±100) is checked on its own, separately from the future.
  The unhedged-lots timer can't bind before it does. Never-hedge is safe.
- Constraints worth respecting: 50 messages/s, 10 active orders, 200 active
  volume, maker rebate −0.0001, taker fee +0.0002. Don't requote when prices are unchanged.
- Petr has built Avellaneda-Stoikov and VPIN bots there, reading fair value from
  the future and from the ETF alone, and run them against Claude-built opponent bots.

## Decisions

- No heavy ML. Only online fitting of a few parameters, refit between rounds, always with a holdout.
- Build against our own interface: `(bids, asks)`, `submit`, `cancel`, fill callback.
  Write one thin adapter on the day.
- Every constant in the bot becomes a runtime parameter.
- Remaining priorities: AI workflow + team roles > parameterised bot + sweep harness
  > message-rate discipline > timed dress rehearsal > advanced AS.
- Team roles on the day: adapter/infra, strategy (the only one who edits decision code),
  measurement (never edits the bot), rules/ops.

## Open questions

- Actual round structure and scoring: find out at the briefing.
- Do position limits, fees, or a message cap exist? Assume yes, and build so each is one parameter.
