# The first yard ladder was mis-calibrated, and its exponents are artifacts

Type: research
Status: open

Recorded immediately, because the artifact is already archived and the framework never overwrites
one. Anyone who reads it without this ticket will believe a finding that is not there.

## What was run

```
python Tests/calltree/calltree_growth.py --ladder meso --knob yard --config inbound_gain_pool --seed 42
```
Artifact: `Tests/calltree/out/archive/growth__cfg-inbound_gain_pool_knob-yard_ladder-meso_seed-42__20260914T012651Z_835a2bad9932+dirty.json`

It completed, produced a full offender table, and reported `gain:_Evaluator._make_pool` at
**k = 1.85, r² = 1.00** — the exact suspect this effort convicted in advance, at a textbook
exponent, with a perfect fit.

**Do not cite it.** It is an artifact of the ladder, not a property of the code.

## Why it is wrong

The counts barely moved across the five rungs:

| flow | rung values |
|---|---|
| `plan_orders` | 18, 18, 18, 18, 18 |
| `place_loads` | 136, 136, 136, 144, 144 |
| `pool_rebuilds` | 452, 452, 452, 482, 480 |

The x axis is measured T, inverted from `place_loads / plan_orders`. It ran **2.29 → 2.37** across
whistles of 400 s down to 50 s. **A 4% range.** An OLS fit over a 4% x span with monotone y returns
a high slope and r² ≈ 1.00 by construction; both numbers are noise dressed as a law. The r² is the
tell that is easiest to misread — it says the five points are collinear in log-log, which five
barely-different points always are.

This is the same class of error the framework's own history records twice: an exponent fitted where
the knob did not actually move the thing it was supposed to move.

## Root cause: the whistle rungs were calibrated on a DIFFERENT recipe

The rung values (400/200/120/80/50 s) came from a sweep run against the tight put recipe —
`bins_per_aisle=40, coverage=2.0, safety=0.4` — where they took the yard 0 → 2 → 38 → 59 and T to
~26. `_INBOUND_RECIPE` is deliberately not that: production coverage (10/2) and a roomy warehouse.

Under production coverage the whistle **cannot stand a yard at all** at this size. Measured, same
recipe, 600 SKUs, 10 batches:

| whistle | final yard | staged | `place_load` | pools | implied T |
|---|---|---|---|---|---|
| none | 0 | 0 | 136 | 452 | 2.29 |
| 40 s | 0 | 4 | 144 | 484 | 2.37 |
| 20 s | 3 | 4 | 192 | 584 | 2.80 |
| 10 s | 8 | 4 | 214 | 722 | 2.98 |
| 5 s | 8 | 4 | 264 | 886 | 3.36 |
| 2 s | 12 | 4 | 416 | 1,274 | 4.33 |

Even at an absurd 2-second whistle the yard reaches 12 and T = 4.33, against T ≈ 26 under the tight
recipe. The binding constraint is not the service rate — it is the **arrival** rate.

**And that inverts a prediction made when the recipe was designed.** The reasoning was that higher
coverage "helps twice: it lifts Q out of the tail AND makes every reorder bigger, so more trailers."
The second half is false in this regime. At `coverage=10, safety=2` a SKU must deplete from `eq` to
`rp ≈ 0.2·eq` before it reorders — roughly 80% of its stock — so reorders are larger but **far
rarer**, and over 10 batches rarity dominates size. Fewer reorders means fewer trailers means no
yard, whatever the dock does.

## The tension this exposes, which is real and not yet resolved

The recipe is being asked for two things that pull against each other at a fixed batch count:

* **A real fixture** (ticket 02): Q > 1, production-shaped levels — which wants HIGH coverage.
* **A standing yard**: enough trailer arrivals to outrun the dock — which wants FREQUENT reorders,
  i.e. LOW coverage, at least within a short run.

Low coverage was the old recipe's answer and it is the wrong one — it buys pressure by degrading the
fixture, which is what this effort was told not to do.

The candidate answer is **more batches**: at production coverage the reorder cadence needs time to
establish, and a yard under a whistle is an UNSTABLE queue, so depth should accumulate with batch
index rather than saturate. That would also settle a standing disagreement — the memory
`growth-ladder-use-the-skus-knob` records that the batches knob saturates every backlog level, but
that was measured on the PUT queue, which drains every batch. The yard behind a binding whistle does
not. Under test now; a 40-batch calibration run exceeded 600 s, which is itself weak evidence that
the depth is growing and the quadratic is biting.

## Instrument gap closed on the way

The ladder printed `final yard depth=n/a` because `_levels` returned only the put-side levels. That
was not cosmetic: `_levels` feeds the traced-vs-untraced divergence check, so two passes that agreed
on picks, placements and the put queue while differing **in the yard** would have passed silently —
and the yard is the one quantity the inbound cells exist to vary. `_levels` now reports
`yard_depth` and `staged` whenever the bound transit is STANDING, and tolerates both a
`BatchTransit` (no yard) and a site-scoped leaf (`dock_depth` refuses).

## What must happen before any inbound exponent is believed

1. A rung ladder whose measured T spans at least a factor of ~4, not 4%.
2. The inverted T checked against the observed `yard_depth` at each rung. They are independent
   measurements of the same quantity; if they disagree, the ladder is measuring something else.
3. A second seed, and a repeated top rung — one point cannot separate a code threshold from the
   machine.
