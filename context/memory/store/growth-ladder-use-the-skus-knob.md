---
name: growth-ladder-use-the-skus-knob
description: "the batches knob saturates every backlog level, so growth in the put-away/receiving machinery only shows on the skus knob"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T02:37:43.468Z
---

`Tests/calltree/calltree_growth.py --knob batches` cannot find growth in the put-away or
receiving machinery. Every backlog **level** saturates on it: over 20→160 batches the dock went
only 4,923 → 6,471. Anything upstream of a bin is already credited to `_queued_qty`, which
suppresses further reorders — the backlog reaches a fixed point and stops growing no matter how
many batches you add.

**Why:** the ladder fits a log-log slope over rungs. A saturating quantity fits k ≈ 0 and reads
as "no growth found" even when the per-item cost is quadratic. The `_admit_held` quadratic
(k = 1.784, fixed 2026-08-25) was invisible on the batches knob and obvious on **skus**.

**How to apply:** hunt growth in this subsystem with `--knob skus`. Use `batches` only for
per-batch work whose size does not depend on the backlog. Also decompose before attributing —
a 2x2 (split × staging) showed the queue *split* costs nothing (k 1.100 → 1.013) while
**staging** carried it (k → 1.450); after the fix the exponents matched and staging was left as
a 2.6x **constant**, not a growth term. Related: [[calltree-framework-first-findings]],
[[hand-run-test-tiers-rot-silently]].
