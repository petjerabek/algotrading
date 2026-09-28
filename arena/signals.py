"""Fair-value signals from a SINGLE order book.

Every function takes L2 snapshots in the format your own engine already emits:
(bids, asks), each a list of (price, quantity) tuples, BEST FIRST.

Nothing here depends on a particular exchange API. On competition day you write
one adapter that produces (bids, asks) and all of this works unchanged.

The question every signal answers: given only the book, where is fair value,
or which way is it about to move?
"""

from collections import deque


# ----------------------------------------------------------------------
# Static signals — one snapshot, no memory
# ----------------------------------------------------------------------

def mid(bids, asks):
    """The naive estimate. Ignores how much size sits on each side."""
    if not bids or not asks:
        return None
    return (bids[0][0] + asks[0][0]) / 2


def spread(bids, asks):
    if not bids or not asks:
        return None
    return asks[0][0] - bids[0][0]


def imbalance(bids, asks, depth=1):
    """Queue imbalance in [-1, 1]. +1 = all size on the bid side.

    If 500 units want to buy at 99 and 5 units want to sell at 100, the 5 get
    consumed long before the 500. True value is nearer 100 than the midpoint.
    """
    if not bids or not asks:
        return 0.0
    b = sum(q for _, q in bids[:depth])
    a = sum(q for _, q in asks[:depth])
    return 0.0 if b + a == 0 else (b - a) / (b + a)


def microprice(bids, asks):
    """Mid weighted by queue imbalance. The highest-value upgrade over mid.

    The weights are CROSSED on purpose: the bid price is weighted by the ASK's
    size. Heavy bid size pushes the estimate UP toward the ask, because that
    thin ask is what will trade first.
    """
    if not bids or not asks:
        return None
    (bp, bq), (ap, aq) = bids[0], asks[0]
    if bq + aq == 0:
        return (bp + ap) / 2
    return (bp * aq + ap * bq) / (bq + aq)


def deep_microprice(bids, asks, depth=3):
    """Microprice generalised over several levels. Steadier, slower to react."""
    if not bids or not asks:
        return None
    b = sum(q for _, q in bids[:depth])
    a = sum(q for _, q in asks[:depth])
    if b + a == 0:
        return mid(bids, asks)
    return (bids[0][0] * a + asks[0][0] * b) / (a + b)


# ----------------------------------------------------------------------
# Dynamic signals — need memory of what changed
# ----------------------------------------------------------------------

class OFI:
    """Order Flow Imbalance (Cont, Kukanov & Stoikov).

    Net buying pressure measured from CHANGES at the top of book, not its
    level. Three things count as buying pressure: bid price rising, size added
    at the bid, ask price falling. Mirrored for selling.

    Often the strongest short-horizon predictor available from one book,
    because it captures intent that has not executed yet.
    """

    def __init__(self, window=20):
        self.prev = None
        self.history = deque(maxlen=window)

    def update(self, bids, asks):
        if not bids or not asks:
            return 0.0
        bp, bq = bids[0]
        ap, aq = asks[0]

        if self.prev is None:
            self.prev = (bp, bq, ap, aq)
            return 0.0

        pbp, pbq, pap, paq = self.prev
        e = 0.0
        if bp > pbp:
            e += bq
        elif bp == pbp:
            e += bq - pbq
        else:
            e -= pbq

        if ap < pap:
            e -= aq
        elif ap == pap:
            e -= aq - paq
        else:
            e += paq

        self.prev = (bp, bq, ap, aq)
        self.history.append(e)
        return e

    def value(self):
        return sum(self.history)

    def normalized(self):
        total = sum(abs(x) for x in self.history)
        return 0.0 if total == 0 else self.value() / total


class TradeFlow:
    """Signed volume of recent executions.

    A resting quote is cheap talk. Crossing the spread costs money, so trades
    reveal conviction. Aggressive buying predicts price up.

    Live feeds rarely label the aggressor. Infer it with the tick rule:
    a trade above the prior mid was buyer-initiated.
    """

    def __init__(self, window=50):
        self.history = deque(maxlen=window)
        self.last_mid = None

    def add(self, side, qty):
        self.history.append(qty if side == "buy" else -qty)

    def add_inferred(self, price, qty):
        """Use when the feed gives you a trade with no aggressor side."""
        if self.last_mid is None or price == self.last_mid:
            return
        self.add("buy" if price > self.last_mid else "sell", qty)

    def observe_mid(self, m):
        if m is not None:
            self.last_mid = m

    def value(self):
        return sum(self.history)

    def normalized(self):
        total = sum(abs(x) for x in self.history)
        return 0.0 if total == 0 else self.value() / total


class Volatility:
    """Realised volatility of the mid. Drives how wide you should quote."""

    def __init__(self, window=100):
        self.mids = deque(maxlen=window)

    def add(self, m):
        if m is not None:
            self.mids.append(m)

    def value(self):
        if len(self.mids) < 3:
            return 0.0
        d = [self.mids[i] - self.mids[i - 1] for i in range(1, len(self.mids))]
        n = len(d)
        mu = sum(d) / n
        return (sum((x - mu) ** 2 for x in d) / n) ** 0.5


# ----------------------------------------------------------------------
# Bundle — one object your bot holds
# ----------------------------------------------------------------------

FEATURE_NAMES = ["imb_1", "imb_3", "micro_dev", "ofi", "trade_flow", "spread"]


class Features:
    """Feed it every tick; ask it for a feature vector.

    Use `update(bids, asks, trades)` once per tick. It feeds the trades BEFORE
    the new book, which matters: when the feed doesn't say who the aggressor
    was, a trade is classified against the mid from BEFORE it happened. Classify
    it against the mid after it and a buy that lifted the ask looks like a sell.

    `micro_dev` is microprice minus mid, in ticks. That DIFFERENCE is the
    signal. The absolute price level is not — never feed a raw price into a
    fitted model, it will not generalise past the level it was fitted at.
    """

    def __init__(self, ofi_window=20, trade_window=50, vol_window=100):
        self.ofi = OFI(ofi_window)
        self.flow = TradeFlow(trade_window)
        self.vol = Volatility(vol_window)

    def update(self, bids, asks, trades=()):
        """One call per tick.

        trades: the executions since the last call, as (price, qty, side).
                side is "buy"/"sell" for the aggressor, or None if the feed
                doesn't say — then it is inferred with the tick rule.
        """
        for price, qty, side in trades:
            if side is None:
                self.flow.add_inferred(price, qty)
            else:
                self.flow.add(side, qty)
        self.on_book(bids, asks)

    # Lower-level entry points. Prefer update(); if you use these, call
    # on_trade / flow.add_inferred for a tick's trades BEFORE its on_book.
    def on_book(self, bids, asks):
        self.ofi.update(bids, asks)
        m = mid(bids, asks)
        self.vol.add(m)
        self.flow.observe_mid(m)

    def on_trade(self, side, qty):
        self.flow.add(side, qty)

    def vector(self, bids, asks):
        if not bids or not asks:
            return None
        m, mp = mid(bids, asks), microprice(bids, asks)
        return [
            imbalance(bids, asks, 1),
            imbalance(bids, asks, 3),
            mp - m,
            self.ofi.normalized(),
            self.flow.normalized(),
            spread(bids, asks),
        ]


# ----------------------------------------------------------------------
# Online fitting — the only "ML" worth doing in a 24h competition
# ----------------------------------------------------------------------

def _solve(a, b):
    """Solve a·x = b by Gauss-Jordan with partial pivoting. None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        pv = m[c][c]
        m[c] = [x / pv for x in m[c]]
        for r in range(n):
            if r != c and m[r][c] != 0.0:
                f = m[r][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] for i in range(n)]


class OnlineRidge:
    """Linear regression y ≈ w·x + b, fitted from running sums.

    Typical use: x = Features.vector() now, y = mid h ticks later − mid now.
    Then fair value = mid + predict(x).

    Why this and not sklearn/xgboost:
      - Closed form from running sums. No training loop. Refits in milliseconds,
        so you can recalibrate between rounds.
      - Six numbers you can PRINT and sanity-check at 3am.
      - Pure Python: no dependency that might be missing in the submission.

    Details that matter:
      - Features are standardised internally before the penalty is applied,
        so `spread` (ticks) and `imb_1` (−1..1) are shrunk equally.
        Coefficients are reported back in ORIGINAL units.
      - The intercept is never penalised.
      - ridge = strength of the pull toward zero, in "pseudo-samples": with
        n samples, coefficients shrink by roughly ridge/n. Default 10 is
        negligible at thousands of samples and only bites when data is thin.
      - decay < 1 makes old samples fade: weight of a sample k steps ago is
        decay**k, so the model remembers roughly 1/(1-decay) samples.
        decay=1 (default) weights everything equally.
      - A feature that never varied (e.g. spread always 2) gets weight 0.
    """

    def __init__(self, n_features, ridge=10.0, decay=1.0):
        self.n = n_features
        self.ridge = ridge
        self.decay = decay
        k = n_features + 1
        self.s = [[0.0] * k for _ in range(k)]   # Σ v vᵀ, v = [x..., 1]
        self.sy = [0.0] * k                      # Σ v y
        self.count = 0
        self.coef = None        # [w_1..w_n, intercept], original units
        self.std_coef = None    # effect on y of a 1-sd move in each feature

    def add(self, x, y):
        v = list(x) + [1.0]
        k = self.n + 1
        d = self.decay
        for i in range(k):
            row = self.s[i]
            vi = v[i]
            for j in range(k):
                row[j] = d * row[j] + vi * v[j]
            self.sy[i] = d * self.sy[i] + vi * y
        self.count += 1

    def add_many(self, xs, ys):
        for x, y in zip(xs, ys):
            self.add(x, y)
        return self

    def fit(self):
        n = self.n
        w = self.s[n][n]                     # total (decayed) weight
        if w < 2:
            return None
        mu = [self.s[i][n] / w for i in range(n)]
        ybar = self.sy[n] / w
        # centred sums
        cxx = [[self.s[i][j] - w * mu[i] * mu[j] for j in range(n)] for i in range(n)]
        cxy = [self.sy[i] - w * mu[i] * ybar for i in range(n)]
        sd = [max(cxx[i][i], 0.0) ** 0.5 / w ** 0.5 for i in range(n)]
        live = [i for i in range(n) if sd[i] > 1e-12]

        beta_std = [0.0] * n
        if live:
            a = [[cxx[i][j] / (sd[i] * sd[j] * w) for j in live] for i in live]
            b = [cxy[i] / (sd[i] * w) for i in live]
            lam = self.ridge / w
            for r in range(len(live)):
                a[r][r] += lam
            sol = _solve(a, b)
            if sol is None:
                return None
            for r, i in enumerate(live):
                beta_std[i] = sol[r]

        beta = [beta_std[i] / sd[i] if sd[i] > 1e-12 else 0.0 for i in range(n)]
        intercept = ybar - sum(m * bt for m, bt in zip(mu, beta))
        self.coef = beta + [intercept]
        self.std_coef = beta_std
        return self.coef

    def predict(self, x):
        if self.coef is None:
            return 0.0
        return sum(c * v for c, v in zip(self.coef, list(x) + [1.0]))

    def score(self, xs, ys):
        """R² on (xs, ys). Pass data the model was NOT fitted on.

        1 = perfect. 0 = no better than always predicting the average move.
        Negative = worse than that — do not deploy.
        """
        ys = list(ys)
        if len(ys) < 2 or self.coef is None:
            return float("nan")
        ybar = sum(ys) / len(ys)
        sst = sum((y - ybar) ** 2 for y in ys)
        sse = sum((y - self.predict(x)) ** 2 for x, y in zip(xs, ys))
        return float("nan") if sst == 0 else 1.0 - sse / sst

    def report(self, names=FEATURE_NAMES):
        if self.coef is None:
            return "not fitted"
        lines = [f"  n={self.count}  ridge={self.ridge}  decay={self.decay}",
                 f"  {'feature':<12}{'coef':>10}{'per 1 sd':>10}"]
        for nm, c, s in zip(names, self.coef, self.std_coef):
            lines.append(f"  {nm:<12}{c:>+10.4f}{s:>+10.4f}")
        lines.append(f"  {'intercept':<12}{self.coef[-1]:>+10.4f}")
        return "\n".join(lines)
