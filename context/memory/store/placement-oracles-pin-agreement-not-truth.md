---
name: placement-oracles-pin-agreement-not-truth
description: "The placement equivalence suites pin pool-vs-wave agreement against frozen hand-copied bodies, so they miss shared-state fixes and re-freeze themselves if you change a signature"
metadata: 
  node_type: memory
  type: reference
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T08:19:34.464Z
---

The three placement equivalence files (`test_travel_balanced_equivalence.py`,
`test_ranked_assign_pool_equivalence.py`, `test_co_demand_pool_equivalence.py`, plus
`Tests/calltree/test_rank_cache_equivalence.py`) are ~4 s and exact-float, and it is easy to
read a green run as "placement is unchanged". It is not what they say. Two limits, both hit
in the architecture-deepening effort:

**1. They pin AGREEMENT, not correctness.** Each compares a family's pool half against its
wave half. A change to state BOTH halves read moves them together, so the suite stays green.
Ticket 01 fixed a live `vol_sum` drift that had moved real placements for the life of the
feature; all 95 tests passed before AND after, untouched. A green equivalence run is
therefore not evidence that an aisle quantity is correct.

**2. The oracle is a FROZEN HAND-COPY of the impl body, living in the test file.** They call
`_ranked_assign_impl`, `_TravelBalancedPool(...)` etc. directly, with the same positional
dicts. So the copies inside `Tests/` are not duplicates to refactor away — they are the
reference. Changing the subject's SIGNATURE forces the oracle to be rewritten, which
re-freezes it against the very change it exists to check.

**Why:** both limits are invisible from the pass count, and both push the same way — toward
believing a refactor is proven when nothing that could fail has run.

**How to apply:** keep signatures still when the change is to a body, and let the oracles
stay untouched; that keeps them meaningful and is usually possible (bind a ledger over the
dicts a function was already handed rather than changing what it is handed). For anything
that moves shared state, the instrument that can actually fail is
[[toy-run-is-the-byte-identity-instrument]]. And run the FULL tier before believing a
deletion is safe: a symbol grep misses transitive importers — `test_index_equivalence.py`
and `test_velocity_zoning.py` broke on a deleted builder they never name, via
`Tests/bench/perf_simulation.py`.

**A thin-driver "oracle" compares the code with itself (2026-09-25).**
`_ranked_minlabor_impl` has been a THIN DRIVER over `_MinLaborPool` since ticket 04, so
`test_minlabor_pool_equivalence.py` cannot see a change INSIDE the pool.  A per-run
centroid memo passed it, and would have passed it just as well if it were wrong.  To test
a pool-internal cache, build the reference as the same pool with the cache disabled
(e.g. `_cen.clear()` before every take).  Then prove the test can fail with a sabotage
that genuinely breaks the invariant.  The first sabotage here restored entries after the
call and was vacuous; a dict whose `pop` is a no-op was not.
(`Tests/unit/test_minlabor_centroid_memo.py`, `Tests/unit/test_pool_drain_memo.py`.)
