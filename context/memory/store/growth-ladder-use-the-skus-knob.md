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
**staging** carried it (k → 1.450).

**CORRECTION (2026-08-25):** this memory previously ended "after the fix the exponents matched
and staging was left as a 2.6x constant, not a growth term." That was wrong. `68bf962` removed
the work per touch (96x fewer `route()` calls at 2,400 SKUs, exact and real) but not the
touches: held-item touches still grow at **k = 1.81**, against 1.82 before. The early exit fires
on `len(blocked) >= len(put_queues)` — *every* queue in the set — so on a store-only catalogue,
where the three-queue split only ever routes to two, it is **unreachable**. Blast radius is zero
until the split is turned on (it is off by default and all staging is `None`), but the growth
term is OPEN, not closed. Full detail in `docs/design/STRESS_TEST_FINDINGS.md`. Related: [[calltree-framework-first-findings]],
[[hand-run-test-tiers-rot-silently]].
