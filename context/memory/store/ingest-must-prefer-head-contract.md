---
name: ingest-must-prefer-head-contract
description: "An artifact present in BOTH a run's own contract and HEAD's silently resolves to the run's older path unless the reader prefers HEAD-first; the mistake was made at three call sites before being hoisted into runschema.reader_for/analysis_path"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T19:15:00.000Z
---

`docs/experiments/ingest.py::_reader_tree` builds its resolver from a run's own recorded contract
so old runs stay readable. The trap: when an artifact name exists in **both** the run's (older)
contract and HEAD's, an absence-only fallback resolves it to the run's **old** location. If the
schema moved that file, the reader looks in the vacated path, finds nothing, and stages/renders
nothing — with no error, because the name was never "missing" from the contract.

This actually happened with `per_run_summary.csv` after the analysis-suite relayout moved the CSVs
under `tables/`, and was caught only by `context/guards/experiment_guard.py --scan` finding a
citation with no staged file. **It then happened again at two more call sites written the same
week** — `RunContext.leaf_rows` (`Optimization/Performance_Evaluations/core/context.py`) and
`analyze_run`'s dossier stage — the last one discovered only after a 20-minute analysis pass came
back with a silently empty artifact. Three independent implementations of the same bug in one
session is what forced the fix to be hoisted rather than patched per call site.

**The generalized rule, now enforced in one place:** `Optimization/runschema/__init__.py` exposes
`reader_for(rt, artifact)` and `analysis_path(rt, artifact, **parts)` — both resolve **HEAD-first**,
falling back to the run's own (older) contract only for names HEAD no longer declares. Anything
the run's **simulation** wrote resolves through the run's own contract (`resolver_for`); anything
an **analysis pass** writes or reads must resolve through `reader_for`/`analysis_path` instead.
Getting this backwards is the same silent-empty-output failure regardless of which of the three
call sites it recurs at.

**How to apply:** any new run-tree consumer that reads analysis-stage artifacts must go through
`reader_for`/`analysis_path`, never re-derive a resolver from the run's raw contract file. After
any run-tree schema mint that moves an existing artifact, re-ingest (or re-analyze) an *older*
run too and check `experiment_guard --scan` — a same-named-but-moved file is the failure mode a
fresh run will never reproduce, and no amount of staring at a single reader catches it.

Related: [[verify-tree-uses-the-runs-own-contract]] (the mirror-image trap on the run side),
[[stakeholder-site-pilot-frame]], [[run-scope-dossier]] (the third call site this hit).
