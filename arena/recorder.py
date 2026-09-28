"""Recorder: one row per tick, the input to the bake-off.

The Runner records every tick automatically. Save the log after a round and
analyse it with:  python -m arena.bakeoff --load round1.json
"""

import json

from arena import signals as S


class Recorder:
    """One row per tick. Call new_segment() whenever the market restarts
    (new seed, new round, reconnect) so no prediction spans the restart."""

    def __init__(self):
        self.segments = [[]]

    def new_segment(self):
        if self.segments[-1]:
            self.segments.append([])

    def record(self, bids, asks, features, truth=None):
        """Call every tick, AFTER features.update(). Records one-sided books
        too (with x=None) so the time index stays aligned with ticks."""
        row = {
            "bids": [list(l) for l in bids],
            "asks": [list(l) for l in asks],
            "mid": S.mid(bids, asks),
            "x": None,
        }
        x = features.vector(bids, asks)
        if x is not None:
            row["x"] = list(x)                   # a copy: snapshot NOW
        if truth is not None:
            row["truth"] = truth
        self.segments[-1].append(row)

    def save(self, path):
        with open(path, "w") as fh:
            json.dump({"features": S.FEATURE_NAMES, "segments": self.segments}, fh)

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            data = json.load(fh)
        if data.get("features") != S.FEATURE_NAMES:
            raise ValueError(f"log has features {data.get('features')}, "
                             f"code has {S.FEATURE_NAMES}")
        r = cls()
        r.segments = [s for s in data["segments"] if s]
        return r
