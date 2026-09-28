# Ready Trader Go practice bots

Opponent bots for Optiver's Ready Trader Go (HackMelbourne mirror), used to
practise against a market with an ETF and a future. They run **inside the RTG
framework**, not in this repo: copy a bot's `.py` and `.json` into the RTG
folder and start it from there.

| bot | team name | what it does |
|---|---|---|
| `skewbot.py` | TraderTwo | market maker that skews quotes against its inventory |
| `mmwide.py` | SpreadBot | market maker with a fixed wide spread and a hard inventory cap, no skew |
| `momentum.py` | MomentumBot | EMA-crossover trend follower, pure taker |
| `meanrev.py` | MeanRevBot | fades deviations from a rolling mean |
| `basisarb.py` | BasisArbBot | trades the ETF against the future, hedges every fill |
| `noise.py` | NoiseBot | random two-way flow, fixed seed |

`exchange.json` is the exchange config used for these matches (fees: maker
−0.0001, taker +0.0002).

Not yet connected to `arena/`. An RTG adapter is the planned test that the
`arena` interface generalises beyond our own sandbox.
