---
name: k-oldest-bounds-lookahead-not-staleness
description: "The put-away window lets the policy pick from the K oldest UNSERVED items, which bounds how far it can see past the head but not how long the head waits; a deadline rule was the rejected alternative"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T09:10:52.287Z
---

`Inventory_Manager.putaway_window` (and `PutQueueSpec.k_cap`) is "the policy may take its
favourite of the K oldest units still waiting, then the window slides by one".

That bounds LOOKAHEAD, not staleness. The window holds the K oldest **unserved** items, so
when a policy's preference runs opposite to arrival order the head units stay in the window
and lose every round. Measured: ascending keys, K=5, 60 units — units 0-3 are served LAST,
overtaken by 56 younger ones.

**Why:** "FIFO with a little tolerance" sounds like it bounds waiting time, and it does not.
I first wrote the test as "overtaken at most K-1 times"; the assertion was wrong, not the code.
The rejected alternative is a deadline rule — "a unit passed over K times must be served next"
— which is a different policy rather than a tuning of this one, and is cheap to add if wanted.

**How to apply:** both behaviours are pinned in `Tests/unit/test_putaway_window.py`
(`test_every_served_unit_was_among_the_k_oldest_still_waiting` and
`test_the_window_bounds_LOOKAHEAD_and_not_staleness`). Do not "fix" the starvation case
without deciding which semantics is wanted. Measured cost of FIFO on real arms (600 SKUs, 6
reorder batches, mean placement score): rank_labor 396→441 (+11.4%), rank_minlabor 379→428
(+13.0%), tmin/comp flat, map inert because it never had an ordering opinion.
Related: [[placement-pools-and-the-audit-point]].
