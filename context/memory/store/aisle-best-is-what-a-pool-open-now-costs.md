---
name: aisle-best-is-what-a-pool-open-now-costs
description: "SUPERSEDED 2026-09-24 by 9a72f8a7: the SKU-run boundary is a numpy matrix op (_TravelVec/_MinLabVec), template opens 4.6-4.8x faster; was 72% of a campaign-shape pool open in _aisle_best"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-20T07:06:10.627Z
---

**SUPERSEDED 2026-09-24 (commit 9a72f8a7, O3 of `.scratch/inbound-fullscale-perf/`).**  The
count was kept and the scan moved into C: the bucket heads are a matrix built once per
pool, a boundary is `PP[Mi] + Dh` with an argmin per row, and `_MinLaborPool`'s walk is a
stable argsort plus a running min/max.  Takes and scores are identical.  Campaign-shape
opens went 16.6 -> 3.6 ms (travel template) and 28.5 -> 5.9 ms (minlabor template).  The
eager path went 2.6-3.0x, and what is left of an eager open is the pool's own construction
over ~22k candidate bins.  `test_placement_selection_is_not_a_scan.py` now pins ZERO
per-aisle Python calls at a boundary.  The history below is kept for context.

The phase-2 performance round (2026-09-20) cut the pool PROLOGUE from 3.685 ms to
effectively nothing, which moved a campaign-shape open from 13.35 ms to 9.23 ms. A
cProfile of what is left says the residue has one owner:

    224,240 calls  0.249 s tottime  0.342 s cumtime   _TravelBalancedPool._aisle_best
    (20 opens -> 11,212 calls per open, of a 0.474 s total: 72%)

11,212 = live aisles x SKU-run boundaries. A load carries mostly distinct SKUs, so nearly
every unit opens a run, and every run rebuilds the best-bracket answer for all ~1,400 live
aisles -- to seat ~12 units, which touch ~12 aisles.

Stage 3a of that round deliberately KEPT the count and shrank the call (a cached per-aisle
head vector), because `Tests/unit/test_placement_selection_is_not_a_scan.py` pins the
rebuild at `boundaries x live_aisles` so a change there is a decision rather than a drift.
Cutting the COUNT is that decision, and it is not taken: the scan is what makes the
selection heap's scores exact, so a lazy scheme has to re-validate stale scores and that is
a different algorithm, not a refactor.

**This is the next target, and it is the largest one left in the gain evaluator.** Anything
that claims to shrink it must clear the same bar the rest of the round did: the toy
`_toy_priced` digest, the three pool families' slice oracles, and the campaign-scale probe
`_probe_unload_ref` -- and it must be sized at the campaign shape, never on the meso ladder
([[meso-ladder-cannot-size-the-pool-prologue]]).
