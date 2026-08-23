---
name: run-scope-dossier
description: "A new run evaluation scope (RunContext) lets a Performance_Evaluations key see the whole run tree across cells, rendering into <run_root>/_dossier/; it runs last because the dossier stage wipes that output tree"
metadata:
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T19:15:00.000Z
---

Before this session's run-dossier feature, `Optimization/Performance_Evaluations` had three
evaluation scopes — per_strategy, config, aggregate — none of which can see two cells at once.
That is why every cross-cell writer (what-if scanners, channel rollups) previously lived outside
the registry as bespoke scripts.

`RunContext` (`Optimization/Performance_Evaluations/core/context.py`, class at line 242) is a
fourth scope, `run`, that gives a render function: the run root, a `runschema` resolver, the
runtime DB, what-if rows (`whatif_rows`, line 348), frozen catalogues, and positionally-tagged
leaf tables (`leaf_rows`, line 386) across the whole tree. Outputs land in
`<run_root>/_dossier/`, declared by run-tree contract `Optimization/schemas/run_tree/6c44b3ce7341.json`.

`Optimization/analyze_run.py::_run_root_stage` (the whole-run entry point's last step, "dossier")
drives it: `driver.run_root_keys(preset)` selects the registered run-scope keys, then
`driver.prepare_run_dir(out_dir)` — which **wipes** the dossier tree first, since it's a derived
directory — then `driver.run_at_root(ctx, keys, ...)` renders them. It runs last because it reads
what every earlier stage wrote (per-arm runtime rows, cross-cell what-if rows, per-leaf tables).
See [[run-dossier-map-precompute]] for the ordering trap this wipe creates with a sibling backfill
CLI.

Related: [[ingest-must-prefer-head-contract]] (the `reader_for`/`analysis_path` helpers this
scope's resolver reuses).
