---
name: v2-sampler-redraws-selected-skus
description: "the v2 Fenwick sampler re-selects SKUs it has already drawn and Batch.items (a dict) hides it, so fulfillment batches deliver 8.64% fewer distinct lines than declared; the cause is catastrophic cancellation, NOT float drift"
metadata:
  node_type: memory
  type: project
  originSessionId: ac77a40b-9c76-4130-99f0-75e52da2dda8
  modified: 2026-09-12T18:04:38.034Z
---

Found 2026-09-12 (dept-cal 40) by measuring the declared sampler's own output. `sum_s p_s` is
exactly the expected distinct-SKU count per batch, and it came back 8% under the declaration.
FIXED the same day (dept-cal 44) as a new sampler version, v3, so v1 and v2 stay
byte-reproducible for the archive; the era flipped to v3 ([[v3-sampler-era]]).

**The defect.** `_lift_weighted_sample_v2` (`Warehouse/picking/Workload_Builder.py`) loops
exactly `k` times, so `selected` usually holds `k` entries -- but `Batch.items` is a **dict
keyed by sku**, so a repeat collapses silently.

**Isolated to v2.** Same section, same seeds, same `k`, three fulfillment batches of the
reference catalogue: v2 gives **179 / 423 / 73** duplicate draws (2.9-12.2%), v1 gives
**0 / 0 / 0**.

**The mechanism, PROVEN 2026-09-12 — and it is NOT the drift story this memory used to carry.**
The refuted account was: `total()` and `find()` disagree after float drift, so part of
`uniform(0, total)` overshoots the reachable prefix and terminates on a tree boundary. Two
measurements kill it. In batch 2, **0 of 73** duplicates had `u > true_total` (batch 0: 17/179).
And a synthetic `_Fenwick` at the same n and k, driven only by drift, produces **zero**
duplicates -- its error stays at 1e-12..1e-14.

The real cause is **catastrophic cancellation under an enormous weight dynamic range**. Affinity
lift values are 2.8-5.0 (median 4.5) over a median 35 partners, and the model multiplies lift in
for every already-selected partner, so `lift_mult` compounds to **~1e20** against base
frequencies of ~1e-6 -- weights spanning ~26 orders of magnitude, against float64's ~16. A
Fenwick node is maintained as `t[j] += (new - old)`. Zeroing a ~1e19 weight subtracts a ~1e19
delta from nodes that also aggregate ~1e4 of small weights; those are annihilated
(`1e19 + 1e4 == 1e19`) and **the node keeps the difference as phantom mass**. Measured: the
Fenwick's `total()` ran **14.6% above** the true sum of its own leaves, and by mid-batch a single
SKU legitimately holds 30-89% of live weight. The `2^k - 1` index signature is a CONSEQUENCE --
once `rem` exceeds the live mass in a subtree the descent takes every remaining bit -- not the
cause, so do not reason from it.

**v2 has a second failure mode** nobody had seen: on a strongly-clustered section its total goes
non-positive and the draw loop breaks EARLY, returning fewer than `k` with no repeats at all. A
test that only checks "the batch was short" cannot tell the two apart.

**Why v1 is clean structurally, not by luck:** `np.searchsorted` over a cumsum in which inactive
entries are exactly 0.0 cannot return a zeroed index, because that would need
`prefix(i) < u <= prefix(i)`. v1 also rebuilds `w` and its cumsum from scratch every draw, so no
residue survives. Its own annihilation (small weights lost after a huge partial sum) is harmless:
it zeroes probabilities that are already ~1e-22.

**What it cost**, across all 40 batches of `comparison_20260912_055947`:

| section | requested | delivered | shortfall | batches short |
|---|---|---|---|---|
| fulfillment | 2,980.6 | 2,723.1 | **-8.64%** | **40/40** |
| store | 618.1 | 611.8 | -1.02% | 29/40 |

**How to apply:** do not trust a v2 batch's size to be its declared `k` -- check `len(b.items)`
against `b.num_skus`. Treat any per-SKU inclusion probability measured under v2 as carrying
artifact SKUs (the two worst sit at 2^17-1 and 2^16-1 with `p_s` 0.8044 / 0.7582 against a
section mean of 0.0167 -- numerical, not demand). Every run between the v2 flip
([[v2-sampler-era]]) and 2026-09-12 is affected. When a maintained accumulator must carry a wide
dynamic range, recompute the node from its children rather than adjusting it by a delta -- that
is exactly what v3 does. Related: [[draw-probability-replaces-line-share]],
[[fill-gap-is-the-line-count-shape]], [[hand-run-test-tiers-rot-silently]].
