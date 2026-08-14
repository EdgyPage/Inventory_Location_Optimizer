---
name: wal-sidecars-come-from-readers
description: "Stray -wal/-shm files in archived runs are created by read-only opens, not by unclean writer closes — no write-side fix can remove them"
metadata: 
  node_type: memory
  type: project
  originSessionId: f8316019-3a49-404c-ad0d-e5f670ed885b
  modified: 2026-08-14T10:09:45.091Z
---

Archived runs accumulate hundreds of `-wal`/`-shm` sidecars (249–291 per run; 572 in one sweep).
The intuitive cause — "a connection that never closed cleanly" — is **wrong**. Measured:

| Scenario | sidecars after? |
|---|---|
| clean single-process write, plain `con.close()` | none (SQLite removes them itself) |
| handle dropped without `close()`, or a killed worker | none (CPython still finalizes it) |
| writing while a **reader** is open | stranded, and they persist after the reader leaves |
| `mode=ro` open on a WAL db, **no writing at all** | **created, and cannot be removed** |

So a run accrues sidecars just by being plotted, fingerprinted or opened in the viewer, long after
every writer is gone.

**Why:** this inverts where the fix belongs. `Schema/connect.py::close()` folds the WAL back in when
a writer is genuinely the file's last (measured: 241 finished DBs, zero sidecars), but it cannot
touch the reader-created ones, and it deliberately *skips* under a concurrent reader rather than
block against the 60 s busy timeout. `scripts/archive_cells.py::_sweep_sidecars` stays the answer
for the rest.

**How to apply:** don't chase sidecar counts through writer code — check whether anything opened the
run read-only first. And don't route every `.close()` through the checkpointing helper: in
`Optimization/persistence/Picking_Data.py` 12 of the 28 sites are per-flush writers firing every 10
batches against a ~1 GB DB, where a `TRUNCATE` checkpoint is pure hot-path cost for no benefit.
