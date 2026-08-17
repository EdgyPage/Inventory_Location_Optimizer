---
name: verify-tree-uses-the-runs-own-contract
description: "verify_tree validates a run against the contract it was stamped with, so fixing a contract never rescues an already-finished run"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5eb3d052-8ad3-4cf3-b0d4-c2ec1fa4ece2
  modified: 2026-08-16T06:33:17.407Z
---

`verify_tree` (and the `preflight.observe`/`validate` pair behind it) validates a run against
**the contract recorded in that run's own `run_layout.json`** — never against the current head.
This is deliberate: a run is judged by the shape it promised when it was written.

**The trap it creates:** correct a run-tree contract defect, adopt the new schema id, then re-run
`verify_tree` on the run that exposed the defect and it **still fails with the identical message**.
Nothing is wrong. The old run is stamped with the old contract, and always will be. Confirm by
reading the run's `run_layout.json` `schema_id` and comparing with `contract.head()`; the fix
reaches new runs only.

Hit on 2026-08-16 with `aggregate_stats_summary_csv` / `aggregate_stats_tests_json`, which were
declared required while `agg.stats` only runs under the DEFAULT and NO_STATS suites — the default
preset is BY_INITIAL. Cost ~20 minutes of chasing a fix that had already worked.

**How to apply:** after adopting a contract change, verify it against the **canary output of
`preflight --yes`** (which runs under the new contract) or a fresh run — not against the run that
motivated the change.
