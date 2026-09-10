---
name: optional-fill-only-answers-through-an-override
description: "A named query's `optional` defaults are only read when a per-vintage override omits the column; the canonical SQL is unsupported on a file lacking any column and the loader falls to its legacy body's dataclass default"
metadata: 
  node_type: memory
  type: project
  originSessionId: c27b69d0-19d4-42f4-9761-57f1b2ef60f2
  modified: 2026-09-10T15:08:20.734Z
---

`Schema.dataset.query` fills an OPTIONAL logical column with its declared default only after
the SQL has run -- and the canonical SQL names every column in `tables`, so on a vintage that
lacks one it raises `UnsupportedQuery`, `_query_rows` returns None, and the loader falls to
its frozen legacy body, whose answer is the DATACLASS default (0), not the optional default.
Found 2026-09-10 building department-calibration 33: `free_bins` read 0 on the
798778f4fae1 vintage while every docstring since ADR-0003 promised None, because no
`batch_frame` override existed for that id.

**Why:** the vintage comments for a "pure column addition" said `optional` IS the override
and none need registering. That is true only of the fill step; the select step still has to
omit the absent column, and only an override can.

**How to apply:** every pure column addition to a table a named query reads needs a
`_dataset.override(family, query, <outgoing id>, <same select list minus the new columns>)`
for the outgoing vintage (`Picking_Data._batch_frame_sql` builds one for `batch_frame`), or
the "unknown, never zero" promise is not delivered. Prove it with a faked-vintage test that
reads the column back through the loader (`test_free_index_by_bucket`), not by reading the
`optional` dict. Related: [[immutable-readers-see-only-the-checkpointed-file]],
[[free-bins-counts-the-whole-geometry]].
