---
name: ingest-must-prefer-head-contract
description: "An artifact that exists in BOTH the run's contract and HEAD's silently stages from the run's older path unless the reader tree prefers HEAD — a moved file then never restages"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T15:21:57.569Z
---

`docs/experiments/ingest.py::_reader_tree` builds its resolver from a run's own recorded contract
so old runs stay readable. The trap: when an artifact name exists in **both** the run's (older)
contract and HEAD's, an absence-only fallback resolves it to the run's **old** location. If the
schema moved that file, ingest looks in the vacated path, finds nothing, and stages nothing —
with no error, because the name was never "missing" from the contract.

This actually happened with `per_run_summary.csv` after the analysis-suite relayout moved the CSVs
under `tables/`. It was caught only by `context/guards/experiment_guard.py --scan` finding a
citation with no staged file.

**How to apply:** `_reader_tree` must resolve **HEAD-first**, falling back to the run's contract
only for names HEAD no longer declares. After any run-tree schema mint that moves an existing
artifact, re-ingest an *older* experiment too and check the guard — a same-named-but-moved file is
the failure mode a fresh run will never reproduce. Never diagnose a staging gap by reading ingest
alone; run `experiment_guard --scan`, which is the only thing that reliably notices.

Related: [[verify-tree-uses-the-runs-own-contract]] (the mirror-image trap on the run side),
[[stakeholder-site-pilot-frame]].
