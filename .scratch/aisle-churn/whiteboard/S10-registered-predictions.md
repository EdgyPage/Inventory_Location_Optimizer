# S10 — the grid, and its predictions, registered before any grid run

**Question.**  Over demand density k and first-time confidence c, where does a placement rule,
and separately an unloading order, first move pick labour beyond the noise floor?  Answer before
running, then let S11 measure.

## The knob check (`assets/s10_knobs.py`, the 40k record's own solver, before any run)

| section | k | c = 0.95: f / sum Q / median cover | c = 0.80: f / sum Q / cover | c = 0.60 |
|---|---|---|---|---|
| store | 1 | 1.308 / 306,816 / 637 d | 1.000 / 249,554 / 526 d | = 0.80 |
| store | 3 | 1.375 / 323,767 / 223 d | 1.000 / 249,554 / 175 d | = 0.80 |
| store | 10 | 1.598 / 373,270 / 78 d | 1.000 / 249,554 / 53 d | = 0.80 |
| store | 30 | 2.000 / 475,228 / 33 d | 1.091 / 277,965 / 18 d (79.5% on floor) | 1.000 / 270,617 / 18 d |
| fulfillment | 1 | 1.499 / 261,757 / 108 d | 1.000 / 185,966 / 78 d | = 0.80 |
| fulfillment | 3 | 1.842 / 321,774 / 44 d | 1.125 / 200,905 / 28 d | 1.000 / 187,177 / 26 d |
| fulfillment | 10 | 1.945 / 403,374 / 14 d (68% on floor) | 1.000 / 322,178 / 10 d (36%) | = 0.80 |

**Revision to the grid.**  The stock-depth lever saturates at the one-line floor: c = 0.60
declares exactly what c = 0.80 declares everywhere except store k = 30 and fulfillment k = 3.
Past 0.80 it cannot shorten cover, and at best it cuts cover 1.2×.  It also sets the picking
crew, which the churn question barely reads.

- The grid runs **c ∈ {0.95, 0.80}**.
- **Demand density is the strong lever:** cover falls about as 1/k.
- **At c = 0.95 a higher k RAISES the solved floor** (f 1.31 → 2.0 on the store): more lines
  arrive inside the lead, so the same confidence needs deeper shelves.  The declared stock
  grows 1.55× at k = 30.

Grid points (store k, fulfillment k) ∈ {(1,1), (3,3), (10,10), (30,10)} × c ∈ {0.95, 0.80},
plus one H = 80 point at (1,1), 0.95.  Nine runs of `_churn_probe`: fifo/lifo cells, the winner
pair and the fifo rider, both stock modes, the 40k perf catalogue.

## Registered predictions

**P1 — the fresh-bin share φ over 40 days** (`assets/s10_predict.py`).  The unconditional law
uses the sampler's per-SKU rates: the 400k campaign's per-decile lift applied to the 40k
catalogue's line share, times k, with l = 2.766.  φ is counted in lines.  S08 measured units
against lines at k = 1 (store +0.9 points, fulfillment −3.4); the same sign is expected at
every k.

| k | store φ | fulfillment φ |
|---|---|---|
| 1 | 7.8% | 26.6% |
| 3 | 20.3% | 54.5% |
| 10 | 45.9% | 79.8% |
| 30 | 69.3% | — |

Acceptance: measured (units) within ±25% relative.  φ does not depend on c: a fresh pack is
drained next whatever the bulk holds.  Caveat: at k ≥ 10 the lead shortens, because trailers
fill faster; φ then reads high.

**P2 — breathing room** (the S09 frontier law with water-filled aisle rates, on each run's own
keyframe-0 start state and its S03/S05 flows, never its placements' heights).  Under the
time–density collapse, the ranked uni store arm's ground share over days 0–9 falls from ~64% at
k = 1 toward the steady state s* = σ_G.  Anchored on the 400k k = 1 trajectory
(s(t) − s* ≈ 0.40·e^{−t/17}, s* ≈ 24%), the predictions for days 0–9 are:

| k | ground share, days 0–9 |
|---|---|
| 3 | ~47% |
| 10 | ~31% |
| 30 | ~26% |

Acceptance: the per-run frontier prediction within 5 points per block.

**P3 — the store placement gap**, uni rank vs uni fifo, pick labour over days 0–39.  The S09
chain is ΔT/T ≈ φ(k)·η·ΔM̄_k/M̄.  Anchor ΔM̄ on the 400k trajectory,
ΔM̄(t) ≈ −0.023 − 0.20·e^{−t/22}, averaged over the time-compressed window, and scale by P1's φ:

gap_k / gap_1 = [φ(k)/φ(1)]·[ΔM̄_k/ΔM̄_1]

| k | 3 | 10 | 30 |
|---|---|---|---|
| gap_k / gap_1 | 1.36 | 1.77 | 2.05 |
| from the 400k k = 1 gap (−0.80%) | −1.09% | −1.42% | −1.64% |

The grid's own k = 1 point anchors the ratio.  A 40k geometry concentrates water-filling
differently, so the absolute k = 1 value may differ from the 400k −0.80%.

Acceptance: measured / predicted ratio in [0.7, 1.3] wherever |gap| exceeds twice the floor.
The claim under test: the placement gap GROWS with churn, but sub-linearly.  φ rises about 9×
while the height deficiency falls about 4×, because the good free bins are spent k× faster.

**P4 — the unloading order.**  fifo vs lifo is exchangeable at every grid point, so
E[gap] = 0.  |measured gap| lies within the run's paired noise floor (2σ) at ≥ 17 of the 18
(grid point × pair) readings.  The rearrangement ceiling grows with φ.  S12 reports the oracle
bound beside the null at each k.

**P5 — the initial layout washes out.**  The opt start's advantage over the uni start (rank arms,
pick labour) is earned on setup-stock picks, so it scales with (1 − φ(k)).  From the 400k k = 1
value (−3.63% − (−0.80%) = −2.83%):

| k | 3 | 10 | 30 |
|---|---|---|---|
| predicted opt advantage | −2.45% | −1.66% | −0.94% |

Acceptance: ratio to the grid's own k = 1 within [0.7, 1.3].

**P6 — the free pool is at a flow equilibrium** at every k: free bins change by less than 2%
across the window while placements run at about 3 packs per store line.

## What would refute the model

| outcome | what it refutes |
|---|---|
| P1 fails | the fresh-bin law (smallest-first drain) |
| P3 grows LINEARLY with φ | the breathing-room decay, since the good bins would not be spent |
| P4 exceeds the floor systematically | exchangeability, meaning the rule's greedy sequence makes the order matter |
| P5 does not shrink | the claim that setup stock is what the initial layout is worth |

**Next.**  S11: launch the grid from an immutable snapshot, then measure P1–P6.
