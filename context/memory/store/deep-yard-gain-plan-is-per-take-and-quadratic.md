---
name: deep-yard-gain-plan-is-per-take-and-quadratic
description: "At 400k the gain plan's cost is in DEEP-yard batches and per unit placed, quadratic in the ~1,000-unit load; bdc443f4 fixed the min-labour fold/centroid/order -- slowest unit 4,459 -> 1,304 s, identical"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-25T12:10:35.846Z
---

Measured 2026-09-25 (S10 of `.scratch/inbound-fullscale-perf/`, runs A-E at 400k, 20
batches, all IDENTICAL to A).  The phase-2 winner pair's gain plan at 400k:

- **The cost is per TAKE, not per pool open.**  A `place_load` seats a whole trailer,
  ~1,000 units ([[gain-bench-must-match-production-load-size]]).
- **It lives in the DEEP-yard batches** (yard 15-25).  A profile of the first 3 batches
  saw the shallow regime only: run D moved the yard plan by nothing.
- **In one pool open it was quadratic in the load** (units x aisles written), three ways
  in `_MinLaborPool`:
  - the partner fold visited every written aisle at each SKU boundary;
  - the centroid walked every member of written winners;
  - the walk order was re-sorted after every take.

bdc443f4 fixed all three exactly:

- an added-partner index, so only aisles holding a partner the live book lacks are
  refolded;
- a partner-only centroid walk in the aisle's live order;
- `searchsorted` repositioning of the one moved row.

44c70a43 added a drain-scoped memo per owner, riding `_SHARED_CACHES['_pool_memo']`.

Run E: slowest unit 4,459 (A) -> **1,304 s**; its yard plan 3,440 -> 794 s; a whole
20-batch probe 112 -> 42 min.  The 20260920 campaign's projected sim-stage bound went
7.9 -> ~2.6 h, now set by total work / 12, not one unit.

**How to apply:**
- Profile a FULL-length production unit before optimising the gain plan
  ([[profile-production-workers-with-a-throwaway-hook]]).
- A pool-level oracle must emulate the evaluator: several virtual opens over the same
  live ledger, sharing one memo, nothing committed.  `_ranked_minlabor_impl` is a thin
  driver and cannot see pool internals ([[placement-oracles-pin-agreement-not-truth]]).
- What remains of the slowest unit is 794 s of yard plan; profile it again before
  guessing.
