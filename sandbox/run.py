"""Run a strategy in the sandbox market. The practice version of competition day.

    python -m sandbox.run                                  # placeholder strategy, 3 seeds
    python -m sandbox.run --seeds 1 2 3 4 5 --half-spread 3
    python -m sandbox.run --max-position 50 --max-msgs 4   # with safety limits
    python -m sandbox.run --save r.json                    # keep the log...
    python -m arena.bakeoff --load r.json                  # ...and analyse it
    python -m sandbox.run --strategy none --report         # observe the market without
                                                           # trading, then the bake-off
"""

import argparse
import statistics

from arena.bakeoff import report
from arena.core import Strategy
from arena.recorder import Recorder
from arena.runner import Runner
from arena.safety import Limits, Safety
from arena.strategies import QuoteAroundMid
from sandbox.adapter import SandboxAdapter


def make_strategy(a):
    if a.strategy == "none":
        return Strategy()                       # do nothing: pure observation
    return QuoteAroundMid(a.half_spread, a.size)


def main(argv=None):
    p = argparse.ArgumentParser(description="Run a strategy in the sandbox.")
    p.add_argument("--strategy", choices=["quote", "none"], default="quote")
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--ticks", type=int, default=2000)
    p.add_argument("--half-spread", type=int, default=2)
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--infer-sides", action="store_true",
                   help="hide the aggressor side of trades, as a live feed may")
    p.add_argument("--max-position", type=int)
    p.add_argument("--max-order-qty", type=int)
    p.add_argument("--max-open-orders", type=int)
    p.add_argument("--max-msgs", type=int)
    p.add_argument("--msg-window", type=float, default=1.0)
    p.add_argument("--max-price-dev", type=int)
    p.add_argument("--max-loss", type=float)
    p.add_argument("--save", help="save the tick log (for arena.bakeoff --load)")
    p.add_argument("--report", action="store_true", help="print the bake-off after the runs")
    p.add_argument("--horizon", type=int, default=1, help="bake-off horizon for --report")
    a = p.parse_args(argv)

    limits = Limits(max_position=a.max_position, max_order_qty=a.max_order_qty,
                    max_open_orders=a.max_open_orders, max_msgs=a.max_msgs,
                    msg_window=a.msg_window, max_price_dev=a.max_price_dev,
                    max_loss=a.max_loss)
    rec = Recorder()
    rows = []
    print(f"  {'seed':>4}{'pnl':>9}{'pos':>6}{'max|pos|':>10}{'fills':>7}"
          f"{'msgs':>7}  rejects / kill")
    for seed in a.seeds:
        runner = Runner(make_strategy(a), Safety(limits), recorder=rec)
        r = runner.run(SandboxAdapter(seed, a.ticks, infer_sides=a.infer_sides))
        rows.append(r)
        extra = " ".join(f"{k}={v}" for k, v in sorted(r["rejects"].items()))
        if r["killed"]:
            extra += f"  KILLED({r['killed']})"
        print(f"  {seed:>4}{r['pnl']:>9.0f}{r['position']:>6}{r['max_abs_position']:>10}"
              f"{r['fills']:>7}{r['messages']:>7}  {extra}")

    pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
    if len(pnls) > 1:
        print(f"\n  PnL mean {statistics.mean(pnls):.0f}  sd {statistics.stdev(pnls):.0f}"
              f"  min {min(pnls):.0f}  over {len(pnls)} seeds")
    if a.save:
        rec.save(a.save)
        print(f"  log saved: python -m arena.bakeoff --load {a.save}")
    if a.report:
        report(rec.segments, horizon=a.horizon)


if __name__ == "__main__":
    main()
