---
name: aisle-best-is-what-a-pool-open-now-costs
description: "After the 2026-09-20 round, 72% of a campaign-shape pool open is _TravelBalancedPool._aisle_best -- 11,212 calls, one per live aisle per SKU-run boundary, to seat 12 units"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-20T07:06:10.627Z
---

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
