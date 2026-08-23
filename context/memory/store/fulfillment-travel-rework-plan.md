---
name: fulfillment-travel-rework-plan
description: Approved multi-phase plan — one-way travel model + velocity zoning + throughput tracking for fulfillment
metadata: 
  node_type: memory
  type: project
  originSessionId: 4489d670-51cd-451a-b42f-5e7cc534ea9e
  modified: 2026-08-23T09:21:14.854Z
---

Approved 2026-07-08. Full plan: `~/.claude/plans/i-want-to-create-elegant-adleman.md`.

Four deliverables, phased: **A** throughput-vs-labor scatter (as of 2026-08-23 living at
`Optimization/Performance_Evaluations/headline/throughput_vs_labor.py` — the `compare/` package it
originally shipped in was deleted wholesale by the analysis-suite rebuild (commits 0389da8,
e73158b, 429a9ee); DONE — no rerun); **B** one-way travel-model rework (pick/non_pick decomposition, aisle entry/exit, cart-swap→non_pick, per-task position reset, shared `aisle_traverse_cost` helper); **C** velocity zoning as a per-regime candidate-layer TOGGLE (not an arm) + optional per-band aisle depth geometry.

Decisions taken (adjustable): one-way is **fulfillment-only** (store two-way, flag-gated); cart-swap stays **flat/position-independent**, reclassified to non_pick_travel; scorer rewrites (`_D_map`, tmin, Compact→aisle-consolidation, rank_labor, rank_minlabor) land **together**, gated on `one_way`; **fresh-rerun DBs** + `col in row.keys()` guards (no ALTER TABLE); aisle far-end `L = aisle.aisle_width`.

**Two corrected facts, as they stood 2026-07-08** (I asserted the opposite earlier; verified against code at that date): (1) the picker's `(x,y)` **persisted across aisle-tasks** — init once, never reset per task → an inter-task travel term existed in `ss_prod_hours` (local-frame artifact, a latent bug the plan's per-task reset was meant to remove). (2) `Warehouse/picking/fast_pick.py` (`DeferredPickSimulation`) is the **production** sim runner (`strategy_runner.py`), so travel changes are a **four-way lockstep** (Pick.py + fast_pick.py + Workload.py/Workload_Builder + task decomposition), guarded by `test_placement_fastpath_equivalence`.

**As of 2026-08-14, all four deliverables have landed** — this is no longer "in flight." Confirmed
in code: `Warehouse/picking/Pick.py::PickSimulation._simulate_picker` now resets `x = y = 0.0` at
the top of every task loop iteration (comment: "Per-task position reset to the aisle entrance"),
so corrected-fact (1) above is fixed, not just diagnosed. `cfg.one_way` is wired in both
`Warehouse/picking/Pick.py` and `Warehouse/picking/fast_pick.py`, default `False`
(`Optimization/config/sim_config.py`). Velocity zoning (Phase C) is wired through
`Optimization/simdriver/strategy_runner.py` (`velocity_zoning` arg), `cells.py`, `workunits.py`,
`Optimization/config/whatif_config.py`, and covered by `Tests/unit/test_velocity_zoning.py`,
default `enabled: False`. Both are flag-gated off by default, matching the plan's decision, so the
byte-identical-when-off discipline (CLAUDE.md §2) still needs checking before trusting any
default-config run against a pre-2026-08-14 figure — see [[determinism-fix-shifted-throughput]] for
the same caution applied to a different commit.

Throughput ≠ total labor (non-monotonic): tasks dealt static round-robin by aisle, no load balancing; throughput = items/makespan tracks 1/makespan, not Σ-labor. See [[channel-experiment-independent-warehouses]], [[results-drive-location]].
