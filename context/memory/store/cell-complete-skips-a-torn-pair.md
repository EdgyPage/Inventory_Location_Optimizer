---
name: cell-complete-skips-a-torn-pair
description: "OPEN finding (.scratch/worker-pool/issues/08), not fixed: cells._cell_complete answers complete on ONE sim_meta.json per pair, so a resumed multi-cell run can skip a cell whose coupled pair is torn before the reconciler repairs it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:34.956Z
---

`Optimization/simdriver/cells.py:_cell_complete(scenario_base, pairs)` answers "complete" as soon
as it finds AT LEAST ONE `sim_meta.json` per pair under a cell's scenario dir. On a resumed
multi-cell run this can be wrong for a COUPLED pair: if the pair's site half is torn (interrupted
mid-write) before the coupled reconciler has a chance to repair it, `_cell_complete` still finds
the one `sim_meta.json` it looks for and reports the cell done, so the resume skips it instead of
re-running or repairing.

**Why:** the coupled resume end-to-end test drives `_run_cells` directly and never goes through a
resume that meets this skip, so nothing currently catches it.

**How to apply:** this is an OPEN finding, not fixed, tracked at
`.scratch/worker-pool/issues/08`. Do not treat `_cell_complete` returning True as proof a
coupled cell's site DB is intact; check the pair's own `sim_meta.json` in `<pair>/_site/` (see
[[iter-sim-dbs-from-a-run-root-is-vacuous]] on the `_reserved`/`_site` layout) before trusting a
resume skip on a coupled run.
