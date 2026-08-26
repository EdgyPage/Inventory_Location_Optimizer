---
name: carryover-two-producers-one-key
description: "two producers wrote reason='unplaced' into one carryover PK under INSERT OR REPLACE and one silently won; the table now raises"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T02:38:05.070Z
---

`carryover` is `PRIMARY KEY (run_id, batch_id, reason, sku)` written with `INSERT OR REPLACE`.
Two different producers were both emitting `reason='unplaced'` into the same `cov` list:

* `mgr.carryover_rows(i)` — the whole standing put queue, a LEVEL
* the pick `_shortfall` — this batch's miss, a FLOW

For any `(batch, sku)` where both were non-zero the pick row overwrote the put-away row and the
quantity was **gone, with no error**. Measured on the 200-batch run: 500 units destroyed by 3
colliding keys. Fixed 2026-08-25 (`3a8b4ab`) — the pick shortfall is now `unpicked_unstocked`,
and `_insert_carryover` **raises** on a duplicate key rather than replacing.

**Why:** `INSERT OR REPLACE` turns a key collision into a silent data loss. Nothing read the
table yet, which made it worse — the loss would have been discovered only after someone built
an analysis on it.

**How to apply:** any new `reason` value needs to be unique to one producer, and a level and a
flow must never share one. When adding a writer to a table with a composite PK, prefer a
constraint that raises over `OR REPLACE`. Related: [[cut-is-a-level-not-a-flow]].
