---
name: gain-bench-must-match-production-load-size
description: "A 400k place_load seats ~1,000 units, not 12; a 12-unit gain bench predicted 2.5-4x and run C measured 0.98x -- size a planner bench by units per placement, and check it against a production profile"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-25T07:14:19.471Z
---

Measured 2026-09-24/25 (S10 of `.scratch/inbound-fullscale-perf/`).  Three S10 commits
made the campaign-shape `plan_order` bench 2.5-4.2x faster.  Run C at 400k then moved the
gain unit **0.98x**, IDENTICAL and no faster:

- the per-template matrix memo, 88ba0514;
- derived templates, a623112a;
- the copy-on-write `holding` read.

The bench seated **12 units per trailer** (the meso-era mean).  A 400k `place_load` seats
a whole trailer, **~1,000 units**: the uni winner made 110 placements and 108,274 pool
takes.  So production cost is per TAKE (the partner centroid, the partner fold), while the
bench measured per-OPEN costs that are small at 400k.  The bench had agreed with run B on
O3's ratio, which made it look calibrated; that was the one term the two shared.

A production profile found the real terms, and they did move the 400k plan: 103 -> 59 s
profiled, from the drain-scoped pool memo plus the overlay shortcut (44c70a43).

**How to apply:**
- Before trusting a gain-planner bench, compare its units per `place_load` and pool
  takes per open against a production profile.
- Profile production itself: [[profile-production-workers-with-a-throwaway-hook]].
- Do not use the meso ladder to size this either:
  [[meso-ladder-cannot-size-the-pool-prologue]].
