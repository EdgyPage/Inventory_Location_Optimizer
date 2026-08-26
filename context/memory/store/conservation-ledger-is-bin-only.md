---
name: conservation-ledger-is-bin-only
description: "the conservation ledger is a stock ledger over BINS, so anything lost before a unit reaches one is invisible — three defects have hidden there"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T05:20:19.952Z
---

`cons_breaks` / `cons_residual` measure `(units_placed − units_evicted − cons_picked) −
occupancy`, and `units_placed` increments at **bin entry**. A unit destroyed on the dock, in a
put queue, in `_held`, or as unsatisfied demand touches **neither side** — the ledger balances
perfectly while merchandise vanishes. `cons_breaks == 0` is therefore not evidence of
conservation; it is evidence about bins.

Three defects hid in exactly this gap, all found in 2026-08:

* **carryover key collision** — 500 units destroyed by 3 colliding keys
  ([[carryover-two-producers-one-key]]).
* **a skipped batch deleted its own demand** — a forced skip of a 993-unit batch handed the
  next batch 898 units *in total*, and `cons_breaks` stayed 0 throughout. The rollover carry
  `_pending` was reassigned *below* the `if not tasks: continue`.
* **swapping a loaded put-queue set** would have dropped whatever it held; the setter now
  raises.

**Why:** it is a *stock* ledger over one storage class, and it was read as if it were a
conservation law over all merchandise. Two docstrings claimed it would catch a pre-bin loss;
both were corrected.

**How to apply:** never cite `cons_breaks == 0` as proof that units were conserved. For anything
upstream of a bin, assert demand closure per batch —
`picked + carried + unmet + shortfall == sum(_eff_batch.items)` — or count the standing backlog
across `_queued_qty`, `queue_depth`, `dock_depth`, `_held`, `in_transit_qty`. When adding a new
place merchandise can sit, add the assertion with it: the ledger will not do it for you.
Related: [[cut-is-a-level-not-a-flow]], [[admit-held-was-quadratic]].
