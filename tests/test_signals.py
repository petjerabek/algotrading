"""Tests for signals.py (Features, OnlineRidge) and bakeoff.py plumbing.

Run: python -m pytest -q
"""

import random

import pytest

from arena import bakeoff as B
from arena import signals as S


# ----------------------------------------------------------------------
# OnlineRidge
# ----------------------------------------------------------------------

def _linear_data(n=4000, seed=0, noise=0.5):
    r = random.Random(seed)
    xs = [[r.gauss(0, 1), r.gauss(0, 1)] for _ in range(n)]
    ys = [2.0 * a - 3.0 * b + 5.0 + r.gauss(0, noise) for a, b in xs]
    return xs, ys


def test_ridge_recovers_known_coefficients():
    xs, ys = _linear_data()
    m = S.OnlineRidge(2, ridge=1.0).add_many(xs, ys)
    w1, w2, b = m.fit()
    assert w1 == pytest.approx(2.0, abs=0.05)
    assert w2 == pytest.approx(-3.0, abs=0.05)
    assert b == pytest.approx(5.0, abs=0.05)


def test_predictions_do_not_depend_on_feature_units():
    # Standardisation: rescaling a feature (ticks -> hundredths) changes its
    # coefficient but not a single prediction.
    xs, ys = _linear_data(n=500)
    big = [[a * 1000.0, b] for a, b in xs]
    m1 = S.OnlineRidge(2, ridge=50.0).add_many(xs, ys)
    m2 = S.OnlineRidge(2, ridge=50.0).add_many(big, ys)
    m1.fit(), m2.fit()
    for x, xb in zip(xs[:20], big[:20]):
        assert m1.predict(x) == pytest.approx(m2.predict(xb), abs=1e-9)


def test_huge_ridge_shrinks_weights_but_not_the_intercept():
    xs, ys = _linear_data()
    m = S.OnlineRidge(2, ridge=1e12).add_many(xs, ys)
    m.fit()
    assert abs(m.coef[0]) < 1e-6 and abs(m.coef[1]) < 1e-6
    assert m.coef[-1] == pytest.approx(sum(ys) / len(ys), abs=1e-6)


def test_constant_feature_gets_zero_weight_and_fit_survives():
    xs, ys = _linear_data(n=300)
    xs = [x + [2.0] for x in xs]           # e.g. spread always 2
    m = S.OnlineRidge(3, ridge=1.0).add_many(xs, ys)
    assert m.fit() is not None
    assert m.coef[2] == 0.0


def test_decay_forgets_an_old_regime():
    r = random.Random(1)
    old = S.OnlineRidge(1, ridge=1.0)
    fading = S.OnlineRidge(1, ridge=1.0, decay=0.99)
    for slope in (+1.0, -1.0):             # regime flips halfway
        for _ in range(2000):
            x = r.gauss(0, 1)
            y = slope * x + r.gauss(0, 0.1)
            old.add([x], y)
            fading.add([x], y)
    old.fit(), fading.fit()
    assert abs(old.coef[0]) < 0.1          # averages the two regimes
    assert fading.coef[0] == pytest.approx(-1.0, abs=0.05)


def test_score_is_one_for_perfect_and_negative_for_backwards():
    xs, ys = _linear_data(noise=0.0)
    m = S.OnlineRidge(2, ridge=0.0).add_many(xs, ys)
    m.fit()
    assert m.score(xs, ys) == pytest.approx(1.0, abs=1e-9)
    assert m.score(xs, [-y for y in ys]) < 0


def test_unfitted_model_predicts_zero():
    assert S.OnlineRidge(3).predict([1, 2, 3]) == 0.0


# ----------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------

def test_update_classifies_trades_against_the_mid_before_them():
    f = S.Features()
    f.update([(99, 10)], [(101, 10)])                    # mid 100
    # A buy lifts the ask at 101; afterwards the mid is 102.
    f.update([(101, 10)], [(103, 10)], trades=[(101, 5, None)])
    assert f.flow.value() == +5                          # a buy, not a sell


def test_update_uses_the_given_side_when_known():
    f = S.Features()
    f.update([(99, 10)], [(101, 10)], trades=[(100, 7, "sell")])
    assert f.flow.value() == -7


def test_vector_has_one_value_per_feature_name():
    f = S.Features()
    f.update([(99, 50), (98, 30), (97, 20)], [(101, 10), (102, 40), (103, 60)])
    v = f.vector([(99, 50), (98, 30), (97, 20)], [(101, 10), (102, 40), (103, 60)])
    assert len(v) == len(S.FEATURE_NAMES)
    assert v[0] == pytest.approx(2 / 3)                  # imb_1
    assert v[2] == pytest.approx(100 + 2 / 3 - 100)      # micro_dev


# ----------------------------------------------------------------------
# Bake-off plumbing
# ----------------------------------------------------------------------

class _Fixed:
    """Stands in for Features: returns a preset vector."""
    def __init__(self, x):
        self.x = x

    def vector(self, bids, asks):
        return self.x


def _segment(mids, xs=None):
    rec = B.Recorder()
    for i, m in enumerate(mids):
        x = xs[i] if xs else [0.0] * len(S.FEATURE_NAMES)
        rec.record([(m - 1, 10)], [(m + 1, 10)], _Fixed(x))
    return rec.segments[0]


def test_recorder_snapshots_the_vector_not_a_reference():
    rec, x = B.Recorder(), [0.0] * len(S.FEATURE_NAMES)
    f = _Fixed(x)
    rec.record([(99, 1)], [(101, 1)], f)
    x[0] = 123.0                                         # mutate after recording
    rec.record([(99, 1)], [(101, 1)], f)
    assert rec.segments[0][0]["x"][0] == 0.0


def test_targets_never_cross_a_segment_boundary():
    a = _segment([100] * 50)
    b = _segment([5000] * 50)                            # restart far away
    for seg in (a, b):
        tr, te = B.split_segment(seg, horizon=5)
        assert all(y == 0 for _, y in tr + te)


def test_train_targets_end_before_the_test_set_starts():
    seg = _segment(list(range(100)))
    tr, te = B.split_segment(seg, horizon=5, train_frac=0.5)
    idx = {id(r): i for i, r in enumerate(seg)}
    assert max(idx[id(r)] for r, _ in tr) + 5 <= 50
    assert min(idx[id(r)] for r, _ in te) >= 50


def test_save_and_load_round_trip(tmp_path):
    rec = B.Recorder()
    rec.record([(99, 1)], [(101, 1)], _Fixed([0.5] * len(S.FEATURE_NAMES)), truth=100)
    p = tmp_path / "log.json"
    rec.save(p)
    back = B.Recorder.load(p)
    assert back.segments == rec.segments


def _synthetic_segments(signal, n_seg=3, n=1500, seed=0):
    """Mid moves by `signal * x0` plus noise; other features are noise."""
    r = random.Random(seed)
    segs = []
    for _ in range(n_seg):
        m, mids, xs = 1000.0, [], []
        for _ in range(n):
            x = [r.uniform(-1, 1) for _ in S.FEATURE_NAMES]
            mids.append(m)
            xs.append(x)
            m += signal * x[0] + r.gauss(0, 1)
        segs.append(_segment(mids, xs))
    return segs


def test_bakeoff_finds_a_planted_signal():
    res = B.analyse(_synthetic_segments(signal=1.0), horizon=1)
    assert B.verdict(res)[0] == "SIGNAL"
    assert res["features"][0]["feature"] == S.FEATURE_NAMES[0]
    assert res["features"][0]["slope"] == pytest.approx(1.0, abs=0.15)


def test_bakeoff_says_none_on_pure_noise():
    res = B.analyse(_synthetic_segments(signal=0.0), horizon=1)
    assert B.verdict(res)[0] == "NONE"
