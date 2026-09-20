---
name: the-gain-sweep-cannot-be-made-incremental
description: "plan_order's T(T+1) is irreducible by memoisation: every commit invalidates EVERY other candidate's placement, because contention for the same bins is what the metric measures"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-20T20:52:26.802Z
---

`plan_order` costs T(T+1) `place_load` calls and `place_loads` fits **k = 1.98** against yard
depth on the corrected ladder ([[meso-ladder-cannot-size-the-pool-prologue]]). The obvious
attack is incremental reuse: each round re-places every remaining candidate against an
`ev.taken` that grew by only ONE trailer's takes, so a candidate that never touched the
newly-taken bins should keep its answer.

**Measured 2026-09-20, and the reuse potential is ZERO.** Counting, per round, how many of
the remaining candidates' now-placements took a bin the round's winner also took:

    T=17   now-placements over the plan: 153   invalidated by the winner: 136
           per round (remaining, invalidated): (17,16) (16,15) (15,14) (14,13) ...

The invalidated count is always `remaining - 1` -- every other candidate, every round.

**Why, and why it will not change.** Every candidate's pool greedily draws the cheapest bins
from the same aisles, so they all take from the same small set. That contention IS the signal
the gain arms compute (the module's own deviation note: leftovers are what the OTHER
candidates leave standing). The thing that makes the metric meaningful is the thing that makes
its sweep non-incremental.

The measurement direction is what makes this conclusive. The probe counted shared bins, which
is an UPPER bound on reuse -- sharing no bin does not by itself prove an answer is unchanged,
because excluding a bin can move a first-appearance tie-break for a candidate that never took
it (`frozen_tier` recomputes aisle and bracket order under exclusion). An upper bound of ~0
means memoisation is impossible, so the tie-break subtleties never need working out.

**So the exponent has exactly one lever: bound the candidate set.**
`INBOUND_TRAILER_BOUND` makes `plan_order` k(k+1) instead of T(T+1), independent of yard
depth. It is not results-preserving, which is why it ships as the probe cell
`k1_off_gmyopic_k8` against its unbounded twin rather than as a setting
([[inbound-campaign-is-a-three-phase-funnel]]). Everything else on that path -- the pool
prologue, the head vector, the copy-on-write reads -- is a CONSTANT on T^2. Worth taking,
because the saving grows as T^2 too, but it is not the exponent.
