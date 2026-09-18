---
name: per-arm-startup-is-catalogue-loading
description: "the per-arm startup block is ~30% of deep wall and GROWS (25s->81s per wave, k=0.56); precompute is only ~3% of it, so it is asset LOADING and a shared/mmap cache is the lever, not worker recycling"
metadata:
  node_type: memory
  type: project
---

Measured 2026-09-18 by splitting each deep rung's wall into `Sum(total_s)/workers`, the analysis
tail, and the residual:

| rung | wall | sim model | analysis | startup+sched | per wave |
|---|---|---|---|---|---|
| 10k | 6.8 m | 1.9 m | 1.7 m | 3.2 m | **25 s** |
| 80k | 33.8 m | 16.3 m | 7.2 m | **10.2 m** | **81 s** |

**k = 0.56, local `0.21, 0.60, 0.75, 1.02` — accelerating toward linear.** It is ~30% of the wall
at 80k, larger than the analysis half, and it is now the biggest non-simulation cost in the tier.

**It is LOADING, not derivation.** `precomp_s` (the strategy build, which contains
`build_optimal_map`) is **1.5 -> 2.1 s per arm at k=0.29** — flat — and its share of this block
FALLS from 5.9% to **2.6%** as the catalogue grows. So a computed-artifact cache would chase ~3%
of it; the other ~97% is asset load. Spawn does not scale with catalogue size and this does.

**Consequences.**
* A shared or memory-mapped asset cache is the lever. `Affinity_Store` already has an
  `affinity_arrays.npz` sidecar and already sets `mmap_size=4GB` on its read side, so this extends
  an existing pattern. The CSR is only 41 MB in RAM ([[affinity-csr-is-41mb-in-ram]]).
* **This retires worker recycling rather than solving it.** Recycling's only prize was avoiding
  the per-arm reload — and the pin's own docstring concedes workers "reload their assets per job
  anyway". Cache the reload and you collect the prize without the deadlock
  ([[worker-recycling-pinned-at-one]]). Spawn, the part recycling actually avoids, is the flat
  remainder.
* **The "~48 s FIXED per arm" figure is wrong at depth** — 48 s is the smallest rung's value.
  See [[deep-tier-save-knee-and-fixed-arm-cost]].

**Why:** a residual nobody can name is a residual nobody fixes, and this one had been named wrong
(as fixed interpreter spawn) for long enough to steer the fix toward the wrong lever.

**How to apply:** the deep ladder prints this split itself since 2026-09-18, so read it rather
than reconstructing it. Before optimising a startup term, check whether it SCALES — a flat term is
spawn, a scaling one is load.
