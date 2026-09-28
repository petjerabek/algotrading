# Algo Trade Arena prep

Toolkit for the Qminers × Matfyzák **Algo Trade Arena** (7–8 Nov 2026): one
asset, a simulated limit order book, 24 hours, final submission in Python.
The Technical Rules only arrive at the opening briefing, so this repo holds a
toolkit that works whatever the rules turn out to be, plus a practice market
to rehearse on.

## Layout

```
arena/            THE COMPETITION TOOLKIT — what we bring on the day
  core.py           the interface: State, Submit, Cancel, Fill, Strategy, Adapter
  runner.py         the per-tick pipeline every adapter plugs into
  safety.py         limits + kill switch between strategy and exchange
  signals.py        order-book features + OnlineRidge (the only "ML")
  recorder.py       per-tick log, saved to JSON
  bakeoff.py        analyses a log: is anything predictable, and how much?
  strategies.py     the bot's decisions  ← strategy owner's file

sandbox/          PRACTICE ONLY — never imported by arena/
  lob.py            matching engine (Petr's, from scratch)
  market.py         simulated market: random-walk fair value, makers, takers
  adapter.py        the sandbox behind arena's Adapter interface
  run.py            CLI: run a strategy in the sandbox

tests/            pytest suite for both
rtg/              Ready Trader Go practice bots (run in the RTG framework)
docs/             rules, prep playbook, day-one adapter checklist
CLAUDE.md         project context for AI sessions
```

The one rule: **`arena/` never imports `sandbox/`.** On competition day the
sandbox is replaced by one new file, `arena/comp_adapter.py`, and nothing else
in `arena/` changes.

## How it fits together

```
exchange ──► adapter ──► State ──► Runner.step ─────────────────────► actions ──► adapter ──► exchange
 (theirs /    (the only          │  1. fills → strategy.on_fill        ▲
  sandbox)     new code          │  2. features.update → state.x       │
               on the day)       │  3. recorder.record  (every tick)   │
                                 │  4. strategy.on_tick → actions      │
                                 └─ 5. safety.check ───────────────────┘

after a round:   recorder log ──► arena.bakeoff ──► verdict + fitted model
next round:      fair = mid + model.predict(state.x)   (only if the verdict says SIGNAL)
```

## Setup

```bash
cd ~/Projects/algotrading
source .venv/bin/activate        # Python 3.9+, no dependencies beyond pytest
python -m pytest -q              # 73 tests, ~5 s
```

Always run from the repo root with `python -m ...` (not `python arena/x.py`),
so the `arena` and `sandbox` packages resolve.

## Common commands

| goal | command |
|---|---|
| run the strategy in the practice market | `python -m sandbox.run` |
| ...over more seeds, with limits | `python -m sandbox.run --seeds 1 2 3 4 5 --max-position 50 --max-msgs 4` |
| save a run's log | `python -m sandbox.run --save r.json` |
| analyse a saved log (also on the day) | `python -m arena.bakeoff --load r.json` |
| ...at another horizon | `python -m arena.bakeoff --load r.json --horizon 2` |
| observe the market without trading + bake-off | `python -m sandbox.run --strategy none --report` |
| Petr's original market demo | `python -m sandbox.market` |

`python -m sandbox.run --help` lists every option. Runs take ~10 s per seed
(the order book's live-order scan is quadratic; known, deferred).

## Reading the output

- **`sandbox.run` table** — per seed: final PnL (marked at mid), final and
  max |position|, fills, messages sent, and what the safety layer rejected.
  Judge a strategy by the mean *and* the spread across seeds, never one seed.
- **`arena.bakeoff` report** — four blocks: which horizon is predictable; each
  feature alone (slope, significance `z`, out-of-sample R²); all features
  together (fitted weights, R² per segment); verdict SIGNAL / WEAK / NONE.
  In the sandbox only, a fifth block compares estimators with the hidden true
  value. Details and interpretation: `docs/PLAYBOOK.md`.

## Where changes go

| you want to… | edit |
|---|---|
| change how the bot quotes | `arena/strategies.py` (subclass `Strategy`) |
| set exchange limits | `Limits(...)` where the Runner is built |
| add a feature | `arena/signals.py` (`Features.vector` + `FEATURE_NAMES`) |
| connect a new exchange | a new adapter implementing `arena.core.Adapter` — see `docs/ADAPTER.md` |
| change the practice market | `sandbox/market.py` |

## Competition day

1. Read the Technical Rules; answer the questions in `docs/ADAPTER.md`.
2. Write `arena/comp_adapter.py` (templates and an AI prompt are in that doc).
3. Set `Limits` from the rules, with headroom.
4. Round 1: trade conservatively, save the log, run `arena.bakeoff` on it.
5. Later rounds: use the fitted fair value only if the verdict is SIGNAL.

## Docs

- `docs/rules-official.md` — the official rules text.
- `docs/PLAYBOOK.md` — what to build before the event, and why; measured results.
- `docs/ADAPTER.md` — checklist for writing the adapter in the first hour.
- `CLAUDE.md` — running project context (decisions, measurements) for AI sessions.
