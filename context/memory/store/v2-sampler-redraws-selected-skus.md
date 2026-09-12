---
name: v2-sampler-redraws-selected-skus
description: "the v2 Fenwick sampler re-selects SKUs it has already drawn and Batch.items (a dict) hides it, so fulfillment batches deliver 8.64% fewer distinct lines than declared; v1 produces zero duplicates on the same seeds"
metadata: 
  node_type: memory
  type: project
  originSessionId: ac77a40b-9c76-4130-99f0-75e52da2dda8
  modified: 2026-09-12T15:21:06.800Z
---

Found 2026-09-12 (dept-cal 40) by measuring the declared sampler's own output. `sum_s p_s` is
exactly the expected distinct-SKU count per batch, and it came back 8% under the declaration.
Ticketed as dept-cal 44; the fix is a NEW sampler version (v3), so v1 and v2 stay
byte-reproducible for the archive.

**The defect.** `_lift_weighted_sample_v2` (`Warehouse/picking/Workload_Builder.py`) loops
exactly `k` times and `break`s only when every weight is zero, so `selected` always holds `k`
entries -- but `Batch.items` is a **dict keyed by sku**, so a repeat collapses silently.

**Isolated to v2.** Same section, same seeds, same `k`, three fulfillment batches of the
reference catalogue: v2 gives **179 / 423 / 73** duplicate draws (2.9-12.2%), v1 gives **0 / 0 /
0**. v1 recomputes `w` and its `cumsum` from scratch every draw; v2 maintains a Fenwick tree by
incremental `set()` deltas. That is the only structural difference.

**The signature.** Repeats land on tree boundaries -- 131071, 65535, 12287, 155647, 81919,
114687, 98303: every one `(a sum of distinct powers of two) - 1`. INFERRED, not proven:
`_Fenwick.total()` sums a different set of nodes than `find()` descends, so accumulated float
drift lets part of `r.uniform(0.0, total)` overshoot the reachable prefix and terminate on a
boundary, where `find` clamps with `min(pos, self.n - 1)`.

**What it costs**, across all 40 batches of `comparison_20260912_055947`:

| section | requested | delivered | shortfall | batches short |
|---|---|---|---|---|
| fulfillment | 2,980.6 | 2,723.1 | **-8.64%** | **40/40** |
| store | 618.1 | 611.8 | -1.02% | 29/40 |

The era declares fulfillment at `n = 2,903` lines/day and the generator delivers ~2,671, so the
coverage fixed point and the derived picking crew are sized ~8.6% high on that leaf. It scales
with `k` and with lift density, which is why fulfillment is 8x the store.

**How to apply:** do not trust a v2 batch's size to be its declared `k` -- check
`len(b.items)` against `b.num_skus`, they differ. Treat any per-SKU inclusion probability
measured under v2 as carrying artifact SKUs: the two worst sit at indices 2^17-1 and 2^16-1 with
`p_s` 0.8044 and 0.7582 against a section mean of 0.0167, which is numerical, not demand. This
also makes the lag-1 same-SKU suppression in [[fill-gap-is-the-line-count-shape]] a suspect
rather than a mystery -- a duplicate consumes a slot another SKU would have taken. Every run
since the v2 flip is affected ([[v2-sampler-era]]); fixing it moves every batch sequence, so it
is a comparability break wider than any of the five so far. Related:
[[draw-probability-replaces-line-share]], [[hand-run-test-tiers-rot-silently]].
