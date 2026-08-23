---
name: fifo-restock-ignores-initial-placement
description: "opt_fifo and uni_fifo are byte-identical runs, not a near-tie — FIFO restock has no ranking for an optimal initial placement to exploit"
metadata:
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T09:21:29.925Z
---

For the FIFO restock assignment function, the `opt_fifo` (optimal initial-assignment) and
`uni_fifo` (uniform initial-assignment) arms produce **BYTE-IDENTICAL** simulation runs — verified
directly against the sim databases on an Experiment-8 cell: same mean/min/max and the same
per-batch rows for `sigma_fd` and `production_time`, not just close values.

**Why:** `Warehouse/placement/Assignment_Functions.py:build_uniform_aisle_trip_min_assignment_fn`
(the function FIFO's `Placement` uses as its `place_one`) already documents the mechanism — it
picks a candidate aisle UNIFORMLY at random with "no affinity, no demand, no priority." Because
FIFO restocking never ranks or scores existing structure, an optimal initial placement leaves
nothing for FIFO to exploit or disturb: the two arms are not "tied" in the statistical sense of a
result with no effect size, they are the same run twice. A by-initial comparison table or chart
that surfaces `opt_fifo` vs `uni_fifo` will show this as identical rows/overlapping lines — reading
that as a 6-12% gap (as an SME reviewer did) is a mis-read of the SAME curve rendered twice, not
a finding. Any future SME review of a by-initial chart should check whether the compared arm is
FIFO before treating a gap (or its absence) as informative.

**How to apply:** don't spend time re-deriving a "why are these identical" investigation for
`opt_fifo`/`uni_fifo` specifically — it is expected and structural, not a bug in
`run_analysis`'s BY_INITIAL preset or in the simulator's determinism. Related:
[[channel-experiment-independent-warehouses]] (defines the `uni_*`/`opt_*` arm split and the
BY_INITIAL preset), [[batch-95-flat-spot-is-shared-demand]] (a different case of an
identical-looking artifact that is also not a comparison bug).
