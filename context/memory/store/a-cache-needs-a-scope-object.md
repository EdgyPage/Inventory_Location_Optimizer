---
name: a-cache-needs-a-scope-object
description: "cluster_map's same-SKU run cache is valid only because a POOL owns it; cmin/cmax are place_one with no wave, so the same cache there is the persistent dict that reintroduces the one-ulp drift"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T20:00:40.691Z
---

`Warehouse/placement/Assignment_Functions.py`'s cluster-map `run_cache` is correct **because a
pool owns it**, not because of what it stores. Its validity argument is "within a same-SKU run
the ONLY idx-set that mutates is the winner's, and the winner's cached delta is recomputed
immediately after each commit" — and a WAVE is the scope in which that holds. `_ClusterMapPool`
holds it as an attribute for exactly that reason, and the docstring says the rest out loud:

> The cache keys on `run_cache['sku'] == sku`, so losing same-SKU adjacency costs the hit rate
> and not correctness. **Making it a persistent `{sku: lifts}` dict to win that back would
> reintroduce exactly the bug above.**

"The bug above" is a **one-ulp** drift: `_delta_lift_from_row` iterates the smaller side, so
adding one member flips the summation ORDER. Not a wrong value — a reordered sum. It moved a
real placement at 8k-SKU meso scale.

**So the same cache cannot be given to `cmin`/`cmax`.** They are `Placement('cohesion_min',
<assign fn>)` — **place_one only, no pool, no wave**. A cache in `_build_aisle_score_fn`'s
closure is ARM-scoped, which is precisely the persistent dict that sentence forbids. Ticket 05
asks for one on the grounds that "cluster_map already solved this shape"; it solved it inside a
wave, and the solution does not transfer. Giving cmin a run cache means giving it a POOL first,
which is a different and much larger piece of work.

**Why:** a cache's invariant is about a WINDOW OF TIME ("nothing else mutated the state I read"),
and the only honest way to express a window in this codebase is an object whose lifetime is that
window. A cache keyed on data (`sku`) rather than held by a scope cannot tell when its window
ended.

**How to apply:** before adding a memo or cache on a hot path here, name the object whose
lifetime is its validity window. If there isn't one, the cache is not cheap — building the scope
is the work. Related: [[placement-oracles-pin-agreement-not-truth]] (the oracles cannot catch a
shared-state change, so the digest is the instrument), [[pool-tier-loop-cost-class-before-count]],
[[map-exact-solver-rarely-fires]].

**RESOLVED FOR cmin 2026-09-18 -- not a cache, an INDEX, and it needs no scope object.**
Measured first (`Tests/calltree/scan_width.py` over the meso skus ladder's own `cmin` config,
units matching the ladder's count exactly, 37,911 at 4,000 SKUs): live aisles scored per unit
grow linearly with the catalogue (k 0.96: 8 -> 120 over 500 -> 8,000 SKUs), total aisle scoring
is quadratic (k 1.98), and the inner lift term saturates (~21). The cost was the NUMBER of
aisles scored per unit, and the cold short-circuit would have covered only 13-18% of them.

The fix is `AisleLedger.partner_aisles`, the inverse of `idx_sets` (matrix index -> aisles
holding it), MIRRORED at the ledger's three `idx_sets` write points -- so it has no validity
window, which is the whole difference from a cache. It rides on the owner's forward dict
(`_IdxSets.inverse`) and `over()` binds it automatically wherever that object is handed, so
the pools' loose-dict views mirror it too and no signature moved; a copy-on-write view (the
gain evaluator's) carries no inverse and stays on the per-aisle fold, so a virtual placement
never touches the live one. `_co_by_aisle` then folds a unit's cohesion over touched aisles
only, in the SAME order the old per-aisle generator used (row order when the row is shorter,
the set walk when the members are), from the same int 0 -- pinned value-and-type exact in
`Tests/unit/test_partner_aisles.py`, and identical aisle-level state bound vs unbound across a
full reorder+pick run in `test_index_equivalence.py`.

Effect on the 8,000-SKU rung, quiet machine: wall 41.1 s -> 21.0 s, wall k 1.60 -> 1.43, and
`_delta_lift_from_row` is called 0 times where it was called 8.0M. The O(A) `_pick_extremal_aisle`
loop remains (k 1.98 in width) at O(1) per aisle; that is the next term if cmin matters again.
