---
name: immutable-readers-see-only-the-checkpointed-file
description: "A sim DB mutated in place (ALTER TABLE, UPDATE stamp) under WAL is invisible to the loaders' immutable open until the WAL is checkpointed and the writer closed; a test that skips that reads the OLD page image, or a misaligned row, with no error"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 204edcbc-db93-472a-928f-f7f7250e813b
  modified: 2026-09-07T02:28:30.340Z
---

The named-query loaders open sim DBs with `immutable=True` (see `_query_rows` in
`Optimization/persistence/Picking_Data.py`), and an immutable open reads ONLY the main file --
never a hot `-wal`. A test that rewrites a file in place (dropping a column to fake an older
vintage, re-stamping `simulation_runs.sim_schema_id`) and then calls a loader sees whatever
was last checkpointed: on the first read the pre-mutation image (the override never fired, the
new columns came back 0); on a later read, after an unrelated connection closed and
checkpointed, a row whose values were shifted one column. Neither raised.

**Why:** WAL commits live in the sidecar until a checkpoint folds them in; only a closing
writer or an explicit `PRAGMA wal_checkpoint(TRUNCATE)` does that. Hit 2026-09-06 building the
`shift_day_frame` override test for the pre-carry-split vintage (`487a65bf83a9`).

**How to apply:** in any test that fakes a vintage in place, `commit()`, run
`PRAGMA wal_checkpoint(TRUNCATE)`, `close()` the connection, and assert the bound id
(`Schema.dataset.bind(...).schema_id == <old id>`, source `stamped`) BEFORE asserting on the
rows -- otherwise the test can pass through the canonical SQL and prove nothing about the
override. Related: [[wal-sidecars-come-from-readers]], [[ingest-must-prefer-head-contract]].
