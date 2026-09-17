---
name: a-ddl-change-moves-two-tables
description: "Dropping or adding a sim_db column moves exactly two tables in the digest — the table itself and simulation_runs, which carries sim_schema_id; and one integration roundtrip is the only test that catches it"
metadata: 
  node_type: memory
  type: reference
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T14:28:39.022Z
---

**"Exactly one table will move" is never the right prediction here.** A DDL change moves TWO:
the table whose shape changed, and `simulation_runs`, because `sim_schema_id` is stamped into
it. Measured twice, on 136 arms each time — `aisle_metrics.lift_sum` (2026-09-16) and
`aisle_metrics.pick_load_sum` (2026-09-17).

**Verify it column by column rather than reading the table list.** On a pure deletion the
report should be: the table keeps every row, the dropped column is the only one missing, and
NO shared column moved; `simulation_runs` differs only in `created` (which `run_digest`
already excludes) and `sim_schema_id`. If a shared column moved as well, the change was not a
pure deletion and the digest's table list alone would not have told you.

**One test catches these, and it is not in the unit tier.**
`Tests/integration/test_visualization_data.py::test_aisle_metrics_roundtrip` is the only place
a record, its INSERT column list and its loader meet a real file together. It has now caught
two column removals in two days; `python -m pytest Tests/unit` was green for both.

**Why:** the schema id is derived by EXECUTING the DDL, so it moves with any shape change,
including one whose values are untouched. And a dataclass field, a DDL line, an INSERT column
string, a parallel value tuple and a loader arm are five hand-kept lists — drop a column from
four of them and the fifth fails only when something actually round-trips.

**How to apply:** ride the schema pipeline (`scripts/schema_report.py --sync` BEFORE the DDL
edit, `--accept` after, commit-window comment by hand), predict the two tables, then check
column-by-column on one arm before writing the claim down. Run `Tests/integration` as well as
`Tests/unit`. Related: [[toy-run-is-the-byte-identity-instrument]],
[[immutable-readers-see-only-the-checkpointed-file]].
