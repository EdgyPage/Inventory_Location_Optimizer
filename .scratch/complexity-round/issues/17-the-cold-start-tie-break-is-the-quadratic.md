# 17 - the cold-start tie-break, and cmin's per-unit lift scan

Type: research
Status: needs-triage

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
