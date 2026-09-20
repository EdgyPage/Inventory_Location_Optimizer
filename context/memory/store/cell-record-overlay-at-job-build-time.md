---
name: cell-record-overlay-at-job-build-time
description: "run_analysis.analyze_cells applies a cell's record (cell_scope) while that cell's jobs are built; before 2026-09-19 _sim_result_from_meta stamped inbound keys off run-level CONFIG and regime_sizing_from_config() read the LAST cell's aisle_split for every cell of a ks sweep"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:41.253Z
---

Since `53964420` (2026-09-19), `Optimization/run_analysis.py:analyze_cells` applies each cell's
own record from `run_layout.json` via `cells.cell_scope` while that cell's jobs are built. Before
that commit, two things were read from the wrong CONFIG state during scoring:

- `_sim_result_from_meta` stamped three cell-axis inbound keys off RUN-level CONFIG rather than
  the cell's own, so `inb_off` reported a dock ceiling the cell did not actually have.
- `regime_sizing_from_config()` reads `CONFIG['channels'][...]['sizing']['aisle_split']`, and
  because the scoring stage ran cells one after another with no per-cell CONFIG scope, every cell
  of a `ks` (aisle-split) sweep was scored under the LAST cell's split, not its own.

Output for split (`ks`) sweeps moved on 2026-09-19, in the correct direction, once `cell_scope`
was applied per cell during job-building.

**Why:** the scoring side inherited the same "one shared CONFIG, no per-cell scope" gap the sim
side had before [[flat-work-pool-era]]; it is a separate defect (a mis-read at scoring time) from
the sim side's mis-run.

**How to apply:** treat any pre-2026-09-19 output from a `ks` (split) sweep, or any report of
`inb_off`/inbound cell-axis keys from a multi-cell run, as suspect and re-score under the current
`analyze_cells`. See [[config-is-not-a-channel-to-an-evaluation]] for the general CONFIG-stamping
rule this refines at the cell axis.
