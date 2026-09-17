# 17 - the cold-start tie-break, and cmin's per-unit lift scan

Type: research
Status: resolved

## Context

Ticket 16 removed three of four passes and a third of the wall. The complexity class did not move:
the scan is still O(|live|) per placement and |live| grows linearly with the catalogue (7.0 ->
106.4 measured across 16x).

## What is actually quadratic, in each cell

**`cluster_map`.** `tied` is the set of aisles whose lift compares exactly equal. A SKU with no
partners placed yet gives EVERY live aisle 0.0, so the tie-break degenerates to "the live aisle
whose pref is closest to `target`". The tied fraction was measured rising 3.0% -> 14.4% across the
ladder - the cold case is not a corner, it is the growing case.

That query is a nearest neighbour over the union of the live aisles' pref lists: one merged sorted
array answers it in O(log N) against O(A) today.

**Two things make it a piece of work rather than a line.** The merged array has to be maintained as
bins are consumed (`by_aisle[aid].remove(chosen)` plus the `prefs_by_aisle` multiset-sync beside
it), and the exact tie order has to survive - among equal gaps the winner is the earliest aisle in
`by_aisle` order, which is precisely the property the equivalence test needed two attempts to be
able to observe.

**`cmin`.** `score_of` is called once per aisle per unit by `_pick_extremal_aisle`, and `co`
depends on the SKU's affinity row, so nothing caches across units. `cluster_map` already solved
this shape: `_place` keeps a `run_cache` across a same-SKU run, because within a run the only
idx-set that mutates is the winner's, and the winner's value is recomputed immediately after each
commit. Measured at roughly 9.5 placements per SKU run, so the ceiling is large.

**Its byte-identity argument is written and was paid for**: `_place`'s docstring records that a
VALUE-ONLY cache is not enough - the first cut cached across the winner's set growth and drifted by
one ulp when the summation order flipped, moving one placement at 8k meso scale.

## A HEAP IS THE WRONG FIX HERE, and this is the round's third time reaching for one

Within a SKU run `lifts` changes only for the winner, so a max-heap looks like the obvious move -
it is what `_RankedAssignPool` and `_TravelBalancedPool` both took. But it would pop and re-push
the whole tied set each placement, costing O(|tied| log A) against today's O(|live|). At 14.4%
tied that is roughly 3.6x - and the tied fraction is RISING, so the heap's advantage shrinks
exactly where the problem grows. The fused pass is both simpler and faster than the heap would
have been.

Write this down before anyone re-derives it.


---

## RESOLVED 2026-09-17  (17c915a0) -- the cluster_map half. The cmin half is REFUSED, with a reason.

### What landed

Two coupled halves in `Warehouse/placement/Assignment_Functions.py`; either alone leaves the
other's O(A):

  * **`_live_idx`** -- the union of placed SKU indices, seeded at pool open and advanced one add
    per commit. One `isdisjoint` replaces one `_delta_lift_from_row` per live aisle.
  * **`_AislePrefIndex`** -- every live aisle's prefs merged once and maintained as bins are
    consumed, answering "the live aisle whose pref is closest to `target`" in O(log N + k).

The branch is exercised at the rate the ladder predicts: **5.0%** of `_cluster_map_choose_aisle`
calls on a 400-SKU scenario, against the ladder's 6.1% at its 500-SKU rung.

### The byte-identity argument had to be a different KIND of argument

`_place`'s run-cache paragraph records that the previous attempt here drifted by **one ulp** --
not because a value was wrong, but because `_delta_lift_from_row` iterates the smaller side, so
adding one member flipped the summation ORDER, and that moved a real placement at 8k.

The short-circuit sidesteps that class entirely rather than arguing within it: every aisle's
idx-set is a SUBSET of the live union, so an empty intersection with the union is an empty
intersection with every aisle -- 0.0 each, **with no float addition performed at all**. There is
no summation to reorder.

### The tie rule, and why the tests construct it

`min(tied, key=...)` returns the FIRST element achieving the minimum, so among aisles equidistant
from `target` the winner is the earliest in `by_aisle` ITERATION order -- not the lowest pref,
not the lowest aisle id. This file called that "precisely the property the equivalence test
needed two attempts to be able to observe", so the collisions are CONSTRUCTED: in
`test_the_tie_is_not_broken_on_aisle_id_or_on_pref` the correct winner deliberately holds BOTH
the higher id and the higher pref, so either wrong rule fails.

One requirement only showed up when the walk was written: **an equally distant entry FURTHER OUT
in pref order can win on rank**, so the walk cannot stop at the first minimal gap it meets.

### THE CMIN HALF IS NOT DONE, and the ticket's stated approach does not transfer

This ticket says: *"`cluster_map` already solved this shape with a `run_cache` across a same-SKU
run."* It did -- **inside a wave**, and that is load-bearing. `_place`'s own docstring says the
cache is "place_wave only", and `_ClusterMapPool`'s says in as many words:

> The cache keys on `run_cache['sku'] == sku`, so losing same-SKU adjacency costs the hit rate
> and not correctness. **Making it a persistent `{sku: lifts}` dict to win that back would
> reintroduce exactly the bug above.**

`cmin`/`cmax` are `Placement('cohesion_min', <assign fn>)` -- **place_one only. No pool, no
wave.** There is no scope object whose lifetime expresses "this run", so a cache in
`_build_aisle_score_fn`'s closure would be ARM-scoped: precisely the persistent dict that
sentence forbids. Giving cmin a run cache means first giving it a POOL, which is a different and
much larger ticket than "add a run_cache", and it should be scheduled as one.

The measured ceiling stands (~9.5 placements per SKU run); what does not stand is that the
solution transfers.

### A heap is still the wrong answer, and was not re-derived

Per this file's own instruction. It costs O(|tied| log A) against O(|live|), and the tied
fraction is RISING, so its advantage shrinks exactly where the problem grows.

### Verification -- four independent instruments

| check | result |
|---|---|
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| `test_rank_cache_equivalence` + `test_placement_fastpath_equivalence` | 16 passed in 7m10s |
| differential harness, two real `cluster_map` arms | **256 cold answers cross-checked against the O(A) scan, 0 disagreements** |
| `Tests/unit/test_cold_start_tie_break.py` | 18 tests |
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,278 passed / 2 skipped |
| all ten gates | green |

`test_cluster_map_cache_matches_frozen_oracle` is the strongest of them, and it is strong because
of how the oracle was adapted: it is **WRAPPED rather than widened**, and the wrapper DROPS
`cold_index` on purpose. The oracle keeps answering the O(A) way, so a whole arm is driven
through the scan and compared against the fast path placement for placement.

### NO SPEED-UP IS CLAIMED

This file says not to claim the win without measuring it. Quantifying it needs the growth ladder,
a separate instrument run. What is claimed is structural: the cold path no longer scans every
live aisle, twice.
