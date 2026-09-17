# 11 - one column, eight declarations, joined by a regex

Type: refactor
Status: claimed

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


---

## Progress -- `batch_stats`' WRITE surface is one declaration (2026-09-17)

**Not resolved.** One table of twenty, and three of the eight sites. What landed is the piece
the ticket's own evidence is about, on the table that has actually broken twice.

### What landed

`_BATCH_WRITE_COLS` -- one ordered tuple. The INSERT's column string and its 34-value tuple
are both built from it (`_BATCH_INSERT_SQL`, `_batch_row`), so the misalignment that shape
invites -- a column added to one list and not the other, shifting every value after it by one,
which SQLite accepts whenever the types happen to line up -- **is not expressible**.

The generated statement is byte-identical to the hand-written one, asserted rather than
assumed.

### The import-time refusal

The defect this ticket exists for -- `work_day` and `released_late` written by every run and
read back as 0 by every reader, for three days, with the schema id unmoved, the insert not
failing and the loader simply never asking -- is now an **import-time RuntimeError**:

    _unreadable = [c for c in _BATCH_WRITE_COLS if c not in _BATCH_COLS]

Planted a column no reader asks for: `Picking_Data` refuses to import. That is earlier and
louder than the test it replaces, and it is a comparison of two DECLARATIONS rather than a
regex over source.

### The ratchet stops parsing source, for this writer

`test_written_columns_are_readable._inserted_columns` recovered a writer's column list with
`inspect.getsource` + `re.findall`. It now reads `_BATCH_WRITE_COLS` where a declaration
exists and keeps the regex for the fifteen writers that have none -- the "1 writer to 20
tables" direction, one writer at a time.

**The regex failed LOUDLY when the writer stopped having a SQL literal**, which is the good
version of this: the scan asserted it could not see the writer rather than quietly returning
an empty set. Three new assertions were added with it, and both new guards were proved against
planted damage:

- the declaration must BE the SQL the writer uses (a declaration nobody exercises is a second
  list, not one list);
- a row must have exactly one value per declared column;
- the import-time refusal, rebuilt with a planted column, because a check that runs at import
  cannot be observed by importing the module that already passed it.

### The writer's duplicated defaults are gone, and that is a behaviour change

The insert read eleven columns through `getattr(r, 'work_day', 0)` -- a default spelled out a
second time beside `work_day: int = 0` on the record. Every caller passes a real `BatchStats`,
which always has every field, so the fallback could only ever fire for a duck-typed stub -- and
for one of those, silently writing 0 into a column the caller forgot is exactly this table's
failure history. A missing attribute is now an `AttributeError` at the write.

The ticket predicted "a live disagreement this will expose": the writer stores
`getattr(r, 'free_bins', 0)` while sites 4, 8 and 10 insist the unknown is `None`. **It is not
a disagreement.** The writer's 0 means "this run measured zero free bins"; the reader's None
means "a vintage that never recorded the column". Two different statements that happen to sit
in the same column, and deleting the writer's copy is what leaves one default per meaning.

### What remains

- the other nineteen tables, and for `batch_stats` the DDL text, the `sim_semantics` entry and
  the frozen legacy loader body. Those need the `Column(name, sql_type, py_default,
  unknown_on_older, semantics, guaranteed)` record the ticket describes; the write surface did
  not, which is why it went first.
- `REQUIRES.tables['batch_stats']` is NOT derivable and should not be. It is the GUARANTEED
  surface -- the intersection over every vetted vintage -- so it is deliberately a subset of
  `_BATCH_COLS` and excludes every recently added column. Deriving it would silently widen
  what the read layer claims every vintage has. Worth recording: the ticket lists it as site 6
  to be derived, and it is the one site that must stay hand-written.
- `.scratch/architecture-drift/issues/07` (`_shift_day_select` reads a conditional table
  without being in `CONDITIONAL_READS`) is untouched by this slice.
