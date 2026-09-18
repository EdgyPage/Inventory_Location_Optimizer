---
name: unload-key-does-not-rank-like-gain
description: "REFUTED 2026-09-18: no per-trailer O(T) key (labour mass, demand mass, units aboard, FIFO) reproduces gain_forecast's yard order -- at 200k SKUs, depths 3-7, tau ~0 and 0/18 drains exact; gain's order is the contention term, which no sum over one load can see. The persisted unload-value table was NOT built; the fallback is an L2-style pool-free evaluator, not a key. Contention only exists at >=100k SKUs (5k/40k rungs never hold more than 2 trailers)."
metadata:
  type: project
---

**The measurement** (`Tests/calltree/unload_key_tau.py`, kept with a test in gate 10): wrap
`Inbound.gain.plan_order` on the inbound ladder's own workload (`gain_forecast`, the era, the
standing yard, receiving crew 4, uncoupled), score every candidate at every drain with four keys,
compare to gain's output order. At 200,000 SKUs sampled from the 400,000 campaign catalogue,
ten batches, 18 drains at yard depths 3-7:

| key | exact | top-1 | tau mean / median |
|---|---|---|---|
| labour mass aboard (Σ freq·qty·labor_cost) | 0/18 | 9/18 | +0.03 / +0.20 |
| demand mass aboard (Σ freq·qty) | 0/18 | 4/18 | −0.04 / 0.00 |
| units aboard | 0/18 | 4/18 | −0.05 / −0.07 |
| FIFO (the control) | 0/18 | 5/18 | +0.01 / 0.00 |

**Why it fails:** the plan's greedy prices each load against what the OTHER loads leave
standing (leave-one-out over the sweep's takes); a per-trailer sum has no term for that.
Ticket 04's fidelity ladder already showed dropping contention (its L1 rung) costs tau 0.58-0.94;
dropping space entirely lands at zero. Labour mass is a weak prior (half the top picks where
chance is a quarter), nothing more.

**Decisions this closes:** W8 stage 3's persisted `unload_value` table and its declared Quantity
are NOT built -- they would record a number that ranks trailers unlike the evaluator, and the
plan's own gate for building them was tau ≈ 1. If the cubic drain must get cheaper, build the
plan's fallback: an L2-style pool-free, contention-aware rung (an evaluator, not a key), which
ticket 04 measured as reproducing the full plan in 28 of 29 comparisons for the extremal-D family.

**A second fact worth keeping:** contention is a property of the catalogue's arrival rate
against four doors. At 5,000 and 40,000 SKUs the yard never holds more than two trailers in
18 `plan_order` calls per run, whatever the receiving crew; the question cannot be asked below
~100k SKUs, which is also why `inbound-pool-adapter-multiplier-is-not-13x`'s first ladder read
flat. Related: [[inbound-optimization-map-closed]], [[a-count-is-not-a-claim]].
