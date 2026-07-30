---
name: batch-95-flat-spot-is-shared-demand
description: "The near-empty store batch that puts a flat spot in every cumulative curve is a property of the shared demand draw, not of any strategy"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-07-30T21:05:03.204Z
---

In the store channel of the 2026-07-29 sweep, batch 95 contains exactly **4 items** (duration
~200 ms) against a ~74,000-item median — and it is **identical in all 34 arms**, because the batch
sequence is precomputed once and shared across arms via the `_batches_*.pkl` cache.

Batch 0 is *not* an outlier by contrast: store `uni_fifo` batch 0 is 52,260 items against a 74,297
median (70 %), fulfillment 47,221 vs 56,683 (83 %) — a mild ramp, well within family.

**Why:** a percentage-vs-baseline panel divides two tiny numbers on that batch, so its excursion goes
off-scale and looks like a dramatic policy effect. It is not one: because the batch is shared, it
biases no comparison *between* policies.

**How to apply:** never special-case batch 0 as a warm-up, and never attribute the batch-95 flat spot
to a placement or scheduling strategy. `comparison/labor_trend.py` marks the near-empty batch with a
dotted line and scales the y-view from the 2nd/98th percentiles rather than smoothing the point away
— keep that behaviour; hiding it would be worse than explaining it.
Related: [[auc-degenerate-on-volume-curves]].
