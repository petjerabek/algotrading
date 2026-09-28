"""Runner: the fixed per-tick pipeline, the same for every adapter.

    state  ->  strategy.on_fill(each new fill)
           ->  features.update(bids, asks, trades)      (trades before book)
           ->  state.x = features.vector(...)
           ->  recorder.record(...)                     (every run is a bake-off log)
           ->  strategy.on_tick(state)  ->  safety.check()  ->  actions

Sandbox CLI: python -m sandbox.run
"""

from arena import signals as S
from arena.core import Strategy
from arena.recorder import Recorder
from arena.safety import Safety


class Runner:
    def __init__(self, strategy=None, safety=None, recorder=None):
        self.strategy = strategy or Strategy()
        self.safety = safety or Safety()
        self.recorder = recorder if recorder is not None else Recorder()
        self.features = S.Features()

    def start(self):
        """Call at the start of every session (seed, round, reconnect)."""
        self.features = S.Features()
        self.recorder.new_segment()

    def step(self, state):
        """One tick. Returns the actions to send. Push-style APIs call this directly."""
        for f in state.fills:
            self.strategy.on_fill(f)
        self.features.update(state.bids, state.asks, state.trades)
        state.x = self.features.vector(state.bids, state.asks)
        self.recorder.record(state.bids, state.asks, self.features, truth=state.truth)
        actions = [] if self.safety.killed else self.strategy.on_tick(state)
        return self.safety.check(actions, state)

    def run(self, adapter):
        """Pull-style loop. Returns a summary of the session."""
        self.start()
        last, n_fills, volume, max_pos = None, 0, 0, 0
        while True:
            state = adapter.poll()
            if state is None:
                break
            n_fills += len(state.fills)
            volume += sum(f.qty for f in state.fills)
            max_pos = max(max_pos, abs(state.position))
            adapter.execute(self.step(state))
            last = state
        return {
            "pnl": last.pnl() if last else None,
            "position": last.position if last else 0,
            "max_abs_position": max_pos,
            "fills": n_fills,
            "volume": volume,
            "messages": self.safety.messages,
            "rejects": dict(self.safety.rejects),
            "killed": self.safety.kill_reason,
        }
