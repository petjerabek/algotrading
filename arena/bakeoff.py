"""Signal bake-off — the competition-day tool.

The question: does anything visible in the book predict where the mid goes
next, and by how much should the bot lean on it?

How the pieces connect:

    every tick:   Features.update(bids, asks, trades)   (signals.py, via the Runner)
                  Recorder.record(bids, asks, features)  (recorder.py, via the Runner)
    after round:  analyse(recorder.segments)             (this file)
                    -> per-feature table + a fitted OnlineRidge + a verdict
    next round:   fair = mid + model.predict(features.vector(bids, asks))
                  (or fair = mid, if the verdict says there's no signal)

Honesty rules built in:
  - Every number is OUT-OF-SAMPLE: fitted on the first part of each segment,
    scored on the rest, with a gap so no target window straddles the cut.
  - Segments (seeds, rounds, restarts) are never glued together, so no
    prediction ever spans a restart.
  - The combined model's R² is shown per segment. A real signal is positive
    in every segment, not just on average.

CLI (works on any saved log — sandbox or competition):
    python -m arena.bakeoff --load round1.json
    python -m arena.bakeoff --load round1.json --horizon 2
Sandbox demo (log a market without us, then this report):
    python -m sandbox.run --strategy none --report
"""

import argparse

from arena import signals as S
from arena.recorder import Recorder  # noqa: F401  (re-exported)


# ----------------------------------------------------------------------
# Train / test split
# ----------------------------------------------------------------------

def split_segment(segment, horizon, train_frac=0.5):
    """Return (train, test) lists of (row, y) with y = mid[t+h] − mid[t].

    Train targets end before the cut; test starts at the cut. So no future
    price used to train is ever part of a test sample.
    """
    cut = int(len(segment) * train_frac)
    train, test = [], []
    for i in range(len(segment) - horizon):
        row, fut = segment[i], segment[i + horizon]
        if row["x"] is None or fut["mid"] is None:
            continue
        y = fut["mid"] - row["mid"]
        if i + horizon <= cut:
            train.append((row, y))
        elif i >= cut:
            test.append((row, y))
    return train, test


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5


# ----------------------------------------------------------------------
# The analysis
# ----------------------------------------------------------------------

def analyse(segments, horizon=1, train_frac=0.5, ridge=10.0):
    """Everything the bake-off measures, as a dict. See print_report()."""
    splits = [split_segment(s, horizon, train_frac) for s in segments if s]
    train = [p for tr, _ in splits for p in tr]
    tests = [te for _, te in splits]
    test = [p for te in tests for p in te]
    if len(train) < 30 or len(test) < 30:
        raise ValueError(f"not enough data: {len(train)} train, {len(test)} test")

    xtr, ytr = [r["x"] for r, _ in train], [y for _, y in train]
    xte, yte = [r["x"] for r, _ in test], [y for _, y in test]

    # One feature at a time: is there a signal, and how big?
    rows = []
    for j, name in enumerate(S.FEATURE_NAMES):
        m = S.OnlineRidge(1, ridge=ridge)
        m.add_many([[x[j]] for x in xtr], ytr)
        m.fit()
        c = _corr([x[j] for x in xte], yte)
        # overlapping h-step targets: ~n/h independent samples
        z = c * (len(xte) / horizon) ** 0.5 if c == c else float("nan")
        rows.append({
            "feature": name,
            "slope": m.coef[0],
            "corr": c,
            "z": z,
            "r2": m.score([[x[j]] for x in xte], yte),
        })
    rows.sort(key=lambda r: -(r["r2"] if r["r2"] == r["r2"] else -9))

    # All features together
    model = S.OnlineRidge(len(S.FEATURE_NAMES), ridge=ridge)
    model.add_many(xtr, ytr)
    model.fit()
    seg_r2 = [model.score([r["x"] for r, _ in te], [y for _, y in te]) for te in tests]

    return {
        "horizon": horizon,
        "n_train": len(train),
        "n_test": len(test),
        "features": rows,
        "model": model,
        "r2": model.score(xte, yte),
        "segment_r2": seg_r2,
        "move_sd": (sum(y * y for y in yte) / len(yte)) ** 0.5,
        "tests": tests,
    }


def scan_horizons(segments, horizons=(1, 2, 3, 5, 10), ridge=10.0):
    """How far ahead is anything predictable? Short horizons usually win:
    the further ahead, the more unpredictable noise piles onto the target."""
    out = []
    for h in horizons:
        try:
            r = analyse(segments, horizon=h, ridge=ridge)
        except ValueError:
            continue
        out.append((h, r["r2"], min(r["segment_r2"]), r["move_sd"]))
    return out


def print_scan(rows):
    print("\nWhich horizon is predictable? (all features, out-of-sample)")
    print(f"  {'h':>4}{'R²':>9}{'worst seg':>11}{'move rms':>10}")
    for h, r2, worst, sd in rows:
        print(f"  {h:>4}{r2:>+9.4f}{worst:>+11.4f}{sd:>10.3f}")


def verdict(res, min_r2=0.01):
    """Deploy the model only if it helps out-of-sample in EVERY segment.

    min_r2 is a judgement call, not a law: below ~1% the prediction is tiny
    compared with the move, and PnL rather than R² should decide.
    """
    seg = [r for r in res["segment_r2"] if r == r]
    if res["r2"] >= min_r2 and seg and min(seg) > 0:
        return "SIGNAL", "use fair = mid + model.predict(x); confirm with a PnL sweep"
    if res["r2"] > 0 and seg and min(seg) > 0:
        return "WEAK", "consistent but tiny; test it in the PnL sweep before trusting it"
    return "NONE", "fair = mid; spend the time on spread, skew and sizing"


# Sandbox only: which fair-value formula is closest to the hidden truth?
ESTIMATORS = {
    "mid":          lambda r, m: r["mid"],
    "microprice":   lambda r, m: S.microprice(r["bids"], r["asks"]),
    "deep_micro_3": lambda r, m: S.deep_microprice(r["bids"], r["asks"], 3),
    "mid+0.5*imb":  lambda r, m: r["mid"] + 0.5 * S.imbalance(r["bids"], r["asks"], 1),
    "mid+model":    lambda r, m: r["mid"] + m.predict(r["x"]),
}


def rank_against_truth(res):
    """RMSE vs the true fair value, on TEST rows only (the model never saw
    them). Only possible in the sandbox. Useful as a check that 'predicts the
    future mid' and 'closer to true value' agree."""
    out = []
    rows = [r for te in res["tests"] for r, _ in te if "truth" in r]
    if not rows:
        return out
    for name, fn in ESTIMATORS.items():
        errs = [fn(r, res["model"]) - r["truth"] for r in rows]
        bias = sum(errs) / len(errs)
        rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5
        out.append((name, bias, rmse, len(errs)))
    out.sort(key=lambda t: t[2])
    return out


def print_report(res):
    h = res["horizon"]
    print(f"\nPREDICTING mid[t+{h}] − mid[t]   "
          f"train={res['n_train']}  test={res['n_test']}  "
          f"typical move (rms) = {res['move_sd']:.3f} ticks")
    print("\nOne feature at a time (all out-of-sample):")
    print(f"  {'feature':<12}{'slope':>10}{'corr':>9}{'z':>7}{'R²':>9}")
    for r in res["features"]:
        print(f"  {r['feature']:<12}{r['slope']:>+10.4f}{r['corr']:>+9.4f}"
              f"{r['z']:>+7.1f}{r['r2']:>+9.4f}")
    print("  slope = ticks of future move per 1 unit of feature (fitted on train)")
    print("  z     = rough significance; |z| < 3 is indistinguishable from noise")

    print("\nAll features together (OnlineRidge):")
    print(res["model"].report())
    segs = "  ".join(f"{r:+.4f}" for r in res["segment_r2"])
    print(f"  out-of-sample R² = {res['r2']:+.4f}   per segment: {segs}")

    tag, advice = verdict(res)
    print(f"\n  -> {tag}: {advice}")

    truth = rank_against_truth(res)
    if truth:
        print("\nSandbox only — distance from the TRUE fair value (test rows):")
        print(f"  {'estimator':<14}{'bias':>9}{'RMSE':>9}{'n':>8}")
        for name, bias, rmse, n in truth:
            print(f"  {name:<14}{bias:>+9.3f}{rmse:>9.3f}{n:>8}")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Analyse a saved tick log.")
    p.add_argument("--load", required=True, help="log saved by Recorder.save()")
    p.add_argument("--horizon", type=int, default=1,
                   help="ticks ahead to predict; pick it from the scan table")
    p.add_argument("--ridge", type=float, default=10.0)
    a = p.parse_args(argv)
    report(Recorder.load(a.load).segments, horizon=a.horizon, ridge=a.ridge)


def report(segments, horizon=1, ridge=10.0):
    """Print the full bake-off: horizon scan, then detail at `horizon`."""
    print_scan(scan_horizons(segments, ridge=ridge))
    print_report(analyse(segments, horizon=horizon, ridge=ridge))


if __name__ == "__main__":
    main()
