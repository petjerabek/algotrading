# Algo Trade Arena — prep playbook

Technical Rules land at the opening briefing. So **nothing tuned in advance
survives**. What survives is a toolkit plus the speed to use it.

Test for whether something is worth building before 7 November:
*would it still be useful if the tick size, fee structure, position limit and
scoring were all different from what I guessed?*

---

## What's here

See `README.md` for the repo layout and every command.

---|---|
| `signals.py` | fair-value estimators + online ridge. No exchange dependency. |
| `bakeoff.py` | logs ticks, measures which signals predict, fits the model. **Run this in round 1.** |
| `core.py`, `runner.py`, `safety.py` | our interface, per-tick pipeline, limits. The day's adapter plugs in here. |
| `ADAPTER.md` | checklist + AI prompt for writing the adapter in the first hour. |

Both take `(bids, asks)` as lists of `(price, qty)`, best first — the format
`Book.l2()` already emits. On the day you write one adapter and these work.

---

## The ML question, answered with measurement

Heavy ML does not fit this competition: no historical data, no known dynamics,
no known scoring, 24 hours. What fits is **fitting a few parameters online and
recalibrating between rounds**.

Measured in your own sandbox (5 seeds × 2000 ticks, `python -m sandbox.run --strategy none --seeds 1 2 3 4 5 --report`),
against the *known* fair value, on held-out rows:

```
  estimator          RMSE
  mid+model         1.001   <- mid + OnlineRidge prediction (h=1)
  mid+0.5*imb       1.082
  mid               1.095
  microprice        1.247
  deep_micro_3      1.464   <- worst
```

**Full microprice loses to plain mid here.** That is the opposite of the
textbook answer, and it is the whole lesson: your makers post uniform size at a
fixed offset and pick sides by coin flip. An estimator's value is a property of
the market, not of the formula.

Which is why you do not pick an estimator in advance. You pick a procedure.

### The procedure that works without knowing the truth

Log every tick with `Recorder`, then `analyse()` fits the model on the first
half of each segment and scores it on the second half. No hidden truth needed,
so it runs on competition day. On the sandbox:

```
  horizon      R²    worst seed
     1     +0.063     +0.047    <- consistent: SIGNAL
     5     +0.025     -0.016    <- one seed negative
    10     +0.002     -0.050    <- nothing
```

And the model it fits at h=1 is the one that lands closest to the true value
in the table above. That agreement is the validation.

Two things this taught us:
- **Short horizons.** The further ahead you predict, the more pure noise sits in
  the target. Read the horizon scan first, then pick h.
- **Per-segment R², not the average.** A signal that is positive in 4 seeds and
  negative in 1 is not a signal you deploy.

If the verdict is NONE, the honest reading is *nothing in the book predicts
this market* — use mid and spend the time on inventory and sizing instead.
Knowing that in round 1 is worth more than a clever estimator.

Still to measure: whether mid + model earns more **PnL**. R² is a proxy; the
sweep is the judge.

### OnlineRidge

Six coefficients, closed form, refits in milliseconds, pure Python. Chosen over
sklearn/xgboost because you can **print the coefficients and sanity-check
them at 3am**. `report()` shows each weight in original units and per 1 sd,
so you can compare features directly.

Rules when using it live:
- **Never feed a raw price in.** Feed deviations (`microprice - mid`). A model
  fitted at 10000 will not generalise when the price is 12000.
- **Always hold out.** `analyse()` does this for you. If out-of-sample R² is
  negative in any segment, do not deploy it.
- **Round 2 behaves differently from round 1?** `OnlineRidge(..., decay=0.999)`
  remembers roughly the last 1000 samples and lets old data fade.

---

## Competition-day AI workflow

The rules explicitly permit AI tools during development while banning help
from any person outside the team. Most teams will not have thought about that
asymmetry. Set up before you arrive:

**Split by role, one person per lane.** The standard failure is four people
all prompting for strategy while nobody owns the harness.

- *Adapter + infra* — first 30 minutes: their API to `(bids, asks)`, logging,
  kill switch. Everything else is blocked on this.
- *Strategy* — quoting logic. Only person who edits the bot's decision code.
- *Measurement* — runs the bake-off and the sweep, reports numbers. Never
  edits the bot.
- *Rules + ops* — reads the Technical Rules properly, owns the submission,
  tracks the limits.

**Have these prompts written down beforehand**, because composing them at
hour three is where the time goes:
1. "Here is the API spec. Write an adapter exposing `l2() -> (bids, asks)`,
   `submit(side, price, qty)`, `cancel(id)`, and a fill callback."
2. "Here is the bot. Add a hard position clamp at ±N and a kill switch that
   cancels everything and stops quoting."
3. "Here is a log file. Find every point where position exceeded N or an
   order was rejected, and summarise."

**What to let AI do:** adapters, logging, replay, sweep scaffolding, parsing
the rules doc, post-round log analysis. All plumbing, all mechanical.

**What to keep human:** the quoting decision. If nobody on the team can
explain in one sentence why the bot quoted where it did, you cannot debug it
when it starts losing — and it will, in round 3, when the market changes.

**Reproducibility is a rule, not a nicety.** Organisers rerun the top
solutions to verify them. Seed every generator, never branch on wall-clock
time. A result you cannot reproduce may not be paid.

---

## Build order for the remaining weeks

1. **Every constant becomes a runtime parameter.** Spread, skew `k`, size,
   quote count, refresh rate. A literal in the bot is a bug waiting for
   round 3.
2. **The sweep harness.** N configs x M seeds -> a table with mean, sd, max
   position, fill count. You will want this at hour two; writing it then
   costs an hour you do not have.
3. **Rate-limit discipline.** Add an artificial message cap to your own sim
   and make the bot respect it. Qminers will almost certainly impose one, and
   "requote every tick" dies the moment they do.
4. **Timed dress rehearsal.** Five hours, a config you have not seen, build
   from scratch. Worth more than another strategy.

Ranked by value for the time left: AI workflow > parameterisation + sweep >
signals > advanced Avellaneda-Stoikov. You have already done AS with VPIN;
that is the lowest-return item on the list.
