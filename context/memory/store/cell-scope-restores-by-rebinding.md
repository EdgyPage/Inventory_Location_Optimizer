---
name: cell-scope-restores-by-rebinding
description: "cells.cell_scope restores CONFIG on exit by REBINDING saved objects, never by refilling live dicts in place -- a payload can be pickled hours after its cell's setup exits"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:13.748Z
---

`Optimization/simdriver/cells.py:cell_scope` wraps `_apply_cell` for the life of a `with` block
and restores CONFIG on exit by REBINDING the saved objects at their keys, never by mutating the
live containers in place. Under the flat work pool ([[flat-work-pool-era]]) a unit built inside a
cell's scope sits in the pool's own pending queue and is pickled only when a worker slot frees --
potentially hours after the cell's setup block has exited. A payload can hold a reference to a
dict CONFIG held at setup time (`velocity_zoning` did, until `workunits._prepare_channel_run`
was changed to copy it); an in-place restore (`dict.clear()` + refill) would rewrite that dict,
and the payload's contents, before it ever ships to a worker.

**Why:** found by design review before this shipped (2026-09-19), not by a failure in
production -- the flat pool's queue is what turns "restore is by rebinding" from a style choice
into a correctness requirement.

**How to apply:** anything that snapshots/restores CONFIG around payload construction under the
pool must rebind (pop-then-set at the same key), never refill a live dict/list in place. The
autouse test-suite fixture ([[the-suite-restores-config-after-every-test]],
`_restore_containers`) is fine restoring in place ONLY because no payload built during a test
outlives that test. `workunits._prepare_channel_run` copying `velocity_zoning` before building a
payload is the pattern to follow for any future per-payload CONFIG snapshot.
