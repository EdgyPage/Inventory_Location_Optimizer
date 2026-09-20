---
name: a-new-column-needs-two-more-lists-than-the-schema
description: "Measured 2026-09-19: a batch_stats column reaching the DDL, reader, semantics table and quantity table still reaches no figure until it is in frames._bdf's explicit field list AND (for a trajectory) the series document's per-batch arrays; both gaps were found only by an end-to-end run, no unit test sees either"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-20T04:25:10.096Z
---

The reporting layer has FIVE places a `batch_stats` column must reach, and unit tests exercise
only the first three:

1. the DDL (`Optimization/persistence/Picking_Data.py` `_CREATE_BATCH_STATS`)
2. the reader (`_insert_batch_stats` / `load_batch_stats`, same file)
3. the semantics table (`Optimization/persistence/sim_semantics.py`) and the capability probe /
   quantity table (`Optimization/Performance_Evaluations/core/quantities.py`)

Two more gates exist and **no unit test reaches either**:

4. `Optimization/Performance_Evaluations/common/frames.py`'s `_bdf` builder has its own EXPLICIT
   field list. A column missing from it makes every `ss_*` scalar derived from that frame **NaN
   silently** — which reads as "no arm published a score," not as a missing column.
5. For a per-batch TRAJECTORY, the column must also be threaded into the series document's
   per-batch arrays (`Optimization/Performance_Evaluations/common/series.py`). Missing this is a
   **KeyError that kills the whole trajectories family**, not a partial result.

Both gaps were found only by running a real run end to end after every unit test had already
passed — see [[the-instrument-is-what-is-wrong]] and
[[symbol-table-relationship-not-verified-by-symbols]] for the same shape of blind spot
elsewhere (a name/schema check that can't see a downstream wiring omission).

**Why:** the unit tests around `Picking_Data.py`, `sim_semantics.py` and `quantities.py` each
check that a column EXISTS and is TYPED correctly; none of them render a figure, so none of them
notice that `frames._bdf` or `series.py` never learned the new field's name.

**How to apply:** when adding a `batch_stats` column meant to reach a figure, grep
`Optimization/Performance_Evaluations/common/frames.py` and
`Optimization/Performance_Evaluations/common/series.py` for the existing field lists and add the
new name to both, THEN run an end-to-end toy run
([[toy-run-is-the-byte-identity-instrument]]) and check the figure, not just the DB row.
