---
name: fulfillment-travel-rework-plan
description: Approved multi-phase plan — one-way travel model + velocity zoning + throughput tracking for fulfillment
metadata: 
  node_type: memory
  type: project
  originSessionId: 4489d670-51cd-451a-b42f-5e7cc534ea9e
  modified: 2026-07-29T03:48:55.570Z
---

Approved 2026-07-08. Full plan: `~/.claude/plans/i-want-to-create-elegant-adleman.md`.

Four deliverables, phased: **A** throughput-vs-labor scatter (`Optimization/Performance_Evaluations/comparison/throughput_vs_labor.py`, DONE — no rerun); **B** one-way travel-model rework (pick/non_pick decomposition, aisle entry/exit, cart-swap→non_pick, per-task position reset, shared `aisle_traverse_cost` helper); **C** velocity zoning as a per-regime candidate-layer TOGGLE (not an arm) + optional per-band aisle depth geometry.

Decisions taken (adjustable): one-way is **fulfillment-only** (store two-way, flag-gated); cart-swap stays **flat/position-independent**, reclassified to non_pick_travel; scorer rewrites (`_D_map`, tmin, Compact→aisle-consolidation, rank_labor, rank_minlabor) land **together**, gated on `one_way`; **fresh-rerun DBs** + `col in row.keys()` guards (no ALTER TABLE); aisle far-end `L = aisle.aisle_width`.

**Two corrected facts** (I asserted the opposite earlier; verified against code): (1) the picker's `(x,y)` **persists across aisle-tasks** — init once at [Pick.py:233-234], never reset per task → an inter-task travel term already exists in `ss_prod_hours` (local-frame artifact, likely a latent bug; the plan's per-task reset removes it). (2) `Warehouse/fast_pick.py` (`DeferredPickSimulation`) is the **production** sim runner (strategy_runner.py:43,438), so travel changes are a **four-way lockstep** (Pick.py + fast_pick.py + Workload.py/Workload_Builder + task decomposition), guarded by `test_placement_fastpath_equivalence`.

Throughput ≠ total labor (non-monotonic): tasks dealt static round-robin by aisle, no load balancing ([Pick.py:203-206]); throughput = items/makespan tracks 1/makespan, not Σ-labor. See [[channel-experiment-independent-warehouses]], [[results-drive-location]].
