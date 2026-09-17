# 11 - one column, eight declarations, joined by a regex

Type: refactor
Status: needs-triage

## Context

To add or read one column of `batch_stats`, eight hand-maintained lists must agree, in three
packages:

| # | Site |
|---|---|
| 1 | `BatchStats` dataclass field -- `Picking_Data.py:42-105` |
| 2 | DDL column text -- `:284-395` |
| 3 | `_insert_batch_stats` -- a 34-name column string AND a parallel 34-value tuple, `:2102-2132` |
| 4 | `_BATCH_OPTIONAL` default map -- `:1548-1580`; `BATCH_UNKNOWN_ON_OLDER_VINTAGES` `:1587` |
| 5 | `_BATCH_COLS` + `_batch_frame_sql()` + per-vintage `override(...)` -- `:1589-1608`, `:1732-1737` |
| 6 | `REQUIRES.tables['batch_stats']` -- `:1306-1350` |
| 7 | `SIM_DB_SEMANTICS['batch_stats']` -- `sim_semantics.py:23-130` |
| 8 | the frozen legacy loader body, one `if 'x' in row.keys()` arm per column -- `:2154-2206` |

Plus a ninth per consumer -- each reader re-declares `SEMANTIC_USES` as `'table.column'` string
literals (`common/frames.py:17` 61 entries, `Diagnostics/receiving_report.py:301` 29,
`Visualization/readers/base.py:78` 22, `Diagnostics/replay_run.py:131` 20) -- and a tenth:
`common/frames.py:_bdf` re-lists every column as `getattr(s, 'free_bins', None)` (`:88-150`).

**This has already been paid for twice.** `work_day`/`released_late` were on the dataclass, the DDL
and the INSERT -- and missing from `_BATCH_OPTIONAL`, from which the SELECT list is built. Every run
wrote a real working day; every reader got `0`, for three days.
`Tests/architecture/test_written_columns_are_readable.py:1-20`:

> Nothing could have caught it. The schema id did not move. The insert did not fail. The loader did
> not fail -- it simply never asked.

`free_bins` was the second (memory `optional-fill-only-answers-through-an-override`).

**The repair is a regex over its own source.** That ratchet recovers the writer's column list with
`inspect.getsource()` + `re.findall(r"'([^']*)'", src)` (`:33-46`), needs
`test_the_ratchet_can_actually_fail()` (`:65-75`) to prove the regex still matches, and covers
**1 of 16** `_insert_*` writers. The DB has 20 tables.

**A live disagreement this will expose:** the writer stores `getattr(r, 'free_bins', 0)` (`:2127`)
into a `NOT NULL DEFAULT 0` column while sites 4, 8 and 10 all insist the unknown is `None`.

## What to build

One ordered `Column(name, sql_type, py_default, unknown_on_older, semantics, guaranteed)` tuple per
table, and derive sites 2, 3, 4, 5, 6, 7 and 8 from it. The INSERT column string and its value
tuple can then no longer misalign.

The per-vintage override stops being hand-written SQL and becomes data:
`Vintage('798778f4fae1', lacks=('put_spills','put_topups','recv_repacks','recv_repacked_packs','free_bins'))`.
The override MACHINERY is already right (`dataset.override`); only its inputs are hand-built.

**Precedent:** `declared_sim_schema_shape()` (`:1112-1120`) already derives the schema id by
EXECUTING `_apply_run_schema` into `:memory:`, so the id cannot drift from the writer. This is that
instinct applied to the other eight lists.

## Verification

- `test_written_columns_are_readable.py` deletes its regex and becomes
  `for table in SIM_TABLES: assert written(table) <= readable(table)` -- 1 writer to 20 tables.
- The writer-vs-reader unknown disagreement becomes a single assertion on the declaration.
- Any DDL movement rides the schema pipeline: `--sync` before, `--accept` after.
- `Picking_Data.py` carries `.scratch/architecture-drift/issues/07` (`_shift_day_select` reads a
  conditional table without being in `CONDITIONAL_READS`, `:1698`/`:1359`). This ticket may fix it
  incidentally -- if so, say so and close that ticket rather than leaving it counted.
- Gates 4, 5, 10.
