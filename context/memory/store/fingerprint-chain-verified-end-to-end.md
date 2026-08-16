---
name: fingerprint-chain-verified-end-to-end
description: "The catalogue→run→analysis→website→viewer fingerprint chain was proven working on 2026-08-16; check ingest's BY-NAME resolve lines, they were silently absent for months"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5eb3d052-8ad3-4cf3-b0d4-c2ec1fa4ece2
  modified: 2026-08-16T07:09:15.087Z
---

The whole identity chain — catalogue descriptor → run `pair_bindings` → DB stamps → analysis
broker → website by-name staging → viewer — was verified end to end on **2026-08-16** against
run `comparison_whatif_20260816_013139` (10k SKUs, 10 batches, 272 arms, both schedulers).
Treat it as working; do not re-derive it.

What was proven: `profile_layout.json` at head with both leaves and a real repo_commit;
`run_layout.json` `version: 2` with non-null `pair_bindings`; **all four DB families stamped**
(sim, keyframes, warehouse, runtime) at their expected ids; 186 broker grants and 0 denials;
`mkdocs build --strict` exit 0; viewer `schema_source: stamped`.

**The trap it exposed, worth keeping even though it is fixed.** `docs/experiments/ingest.py`
never loaded `.env`, so `--profiles-root`'s documented `$PROFILE_INPUT_DIR` default could never
populate, and BY-NAME catalogue resolution **silently fell back to a recorded absolute path** —
the exact route `pair_bindings` exists to replace. Nothing warned, because that fallback is
legitimate for pre-v2 runs.

**How to apply:** after any ingest, confirm the `resolve … catalogue by NAME via pair_bindings`
lines appear — one per pair per cell. Their absence against a v2 run with non-null bindings means
the by-name path did not run, whatever else looks healthy. More generally: when a feature has a
legitimate silent fallback, the only evidence it ran is a positive log line, so check for the
line rather than the absence of errors. Related: [[results-drive-location]] (why the absolute-path
fallback is actively dangerous here), [[verify-tree-uses-the-runs-own-contract]].
