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
