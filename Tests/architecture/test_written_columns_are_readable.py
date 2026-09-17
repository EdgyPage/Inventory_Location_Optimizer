"""test_written_columns_are_readable.py — a column nobody can read back is not persisted.

The defect this exists for, found 2026-08-25 and real for three days: `batch_stats` gained
`work_day` and `released_late`. Both were declared on `BatchStats`, both were in the DDL, both
were in the INSERT — and neither was in `_BATCH_OPTIONAL`, from which `_BATCH_COLS` and the
`batch_frame` query's SELECT list are built. So every run wrote a real working day and every
reader got `0`, on every run including the ones that recorded something.

Nothing could have caught it. The schema id did not move (the DDL was right). The insert did
not fail (the column existed). The loader did not fail (it simply never asked for the column).
The value was a plausible `0`, which is also what a pre-column vintage legitimately returns —
so even looking at the data would not have told you.

The ratchet is therefore structural: for every table where a writer and a named query both
exist, the query's column set must COVER what the writer writes. It is a superset test, not an
equality test — a query may legitimately select fewer columns for a narrow purpose (the viewer
does), which is why the check is anchored on the FULL-ROW query for each family rather than on
every query.

Run:  python -m pytest Tests/architecture/test_written_columns_are_readable.py -q
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from Optimization.persistence import Picking_Data as pd


#: writer -> its DECLARED write surface, for the writers that have one.
#:
#: Ticket 11's direction, one writer at a time. The regex below is a REPAIR for writers with
#: no declaration, and it is the repair that ticket exists to delete: it recovers a column
#: list from the repo's own source, it covers 1 writer of 16, and four of the sixteen already
#: defeat it (`_insert_work_events` parses 1 column of its real set; `free_index`,
#: `shift_days` and `site_receiving` parse none). Every writer that gains a declaration leaves
#: it behind.
_DECLARED_WRITE_SURFACE = {
    '_insert_batch_stats': pd._BATCH_WRITE_COLS,
}


def _inserted_columns(fn_name: str) -> set:
    """The column names a writer writes -- from its declaration, or from its SQL text.

    Either way the question is what the WRITER writes, not what the table HAS: a column can
    exist and have no writer, which is fine and not what this file is about.
    """
    declared = _DECLARED_WRITE_SURFACE.get(fn_name)
    if declared is not None:
        return set(declared)
    src = inspect.getsource(getattr(pd, fn_name))
    # The SQL is assembled from adjacent string literals, so recover it by unparsing.
    sql = ' '.join(re.findall(r"'([^']*)'", src))
    m = re.search(r'INSERT (?:OR REPLACE )?INTO \w+\s*\(([^)]*)\)', sql, re.I)
    assert m, f'{fn_name}: no INSERT column list found — this test cannot see the writer'
    return {c.strip() for c in m.group(1).split(',') if c.strip()}


def test_every_batch_stats_column_written_can_be_read_back():
    """THE regression. `work_day` and `released_late` were written and unreadable for three
    days; this is the assertion that would have failed on the commit that did it."""
    written = _inserted_columns('_insert_batch_stats')
    readable = set(pd._BATCH_COLS)
    # `run_id` is the query's WHERE parameter rather than an output column, and `is_outlier`
    # is set by the outlier pass rather than carried on the frame — both are read through
    # their own paths, so they are named here rather than silently tolerated by a loose rule.
    exempt = {'is_outlier'}
    missing = written - readable - exempt
    assert not missing, (
        f'batch_stats columns are WRITTEN but never SELECTed: {sorted(missing)}. '
        f'They read back as their Python default on every run, including runs that '
        f'recorded a real value. Add them to _BATCH_OPTIONAL (the optional-fill route, '
        f'which is also what keeps them legal on vintages that predate them).')


def test_the_ratchet_can_actually_fail():
    """Non-vacuity. If `_inserted_columns` silently returned an empty set — a regex that
    stopped matching after a reformat, say — the test above would pass forever while checking
    nothing. This repo once had 57 tests that could not fail."""
    written = _inserted_columns('_insert_batch_stats')
    assert len(written) > 20, f'only {len(written)} columns parsed; the writer scan is broken'
    assert 'work_day' in written, 'the scan lost the very column that motivated this file'
    assert not (written - set(pd._BATCH_COLS) - {'is_outlier'}), 'sanity'
    # and prove the comparison bites on a column the reader really does not have
    assert 'no_such_column' not in set(pd._BATCH_COLS)


def test_the_declaration_is_what_the_writer_actually_writes():
    """A declaration nobody exercises is a second list, not one list.

    The ratchet above now trusts `_BATCH_WRITE_COLS` instead of parsing the writer's SQL, so
    the thing that has to be true is that the SQL IS built from it -- otherwise the ratchet
    checks a declaration the code ignores, which is a worse failure than the regex it
    replaced. Read off the generated statement, not off the source.
    """
    m = re.search(r'INSERT INTO batch_stats\s*\(([^)]*)\)', pd._BATCH_INSERT_SQL, re.I)
    assert m, 'the generated INSERT no longer names its columns'
    in_sql = tuple(c.strip() for c in m.group(1).split(','))
    assert in_sql == pd._BATCH_WRITE_COLS, (
        f'the declaration and the SQL disagree: '
        f'{sorted(set(in_sql) ^ set(pd._BATCH_WRITE_COLS))}')
    assert pd._BATCH_INSERT_SQL.count('?') == len(pd._BATCH_WRITE_COLS), (
        'the placeholder count and the column count disagree -- the exact misalignment the '
        'hand-kept pair invited')


def test_a_row_has_exactly_one_value_per_declared_column():
    """The other half of the misalignment: a value tuple the wrong length.

    Hand-written, the column string and the value tuple were kept parallel by eye, and a
    column inserted in one and not the other shifts every value after it by one -- which
    SQLite accepts whenever the types happen to line up.
    """
    row = pd._batch_row(7, pd.BatchStats(
        run_id=7, batch_id=1, duration=1.0, num_tasks=2, total_items=3,
        avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5))
    assert len(row) == len(pd._BATCH_WRITE_COLS)
    assert row[pd._BATCH_WRITE_COLS.index('run_id')] == 7, 'run_id is the call argument'
    assert row[pd._BATCH_WRITE_COLS.index('duration')] == 1.0
    assert row[pd._BATCH_WRITE_COLS.index('is_outlier')] == 0, (
        'is_outlier must reach SQLite as an int, not a bool left to the adapter')


def test_the_writer_cannot_write_a_column_no_reader_can_ask_for():
    """The import-time refusal, exercised rather than trusted.

    `Picking_Data` compares its write surface against `_BATCH_COLS` when it loads, so
    `work_day` and `released_late` -- written by every run and read back as 0 for three days
    -- would have been a refusal on the commit that introduced them. Rebuilt here with a
    planted column, because an import-time check cannot be observed by importing the module
    that already passed it.
    """
    planted = pd._BATCH_WRITE_COLS + ('a_column_no_reader_asks_for',)
    unreadable = [c for c in planted if c not in pd._BATCH_COLS and c != 'is_outlier']
    assert unreadable == ['a_column_no_reader_asks_for'], unreadable
    # and the real surface is clean
    assert not [c for c in pd._BATCH_WRITE_COLS
                if c not in pd._BATCH_COLS and c != 'is_outlier']


def test_the_optional_defaults_match_the_column_types():
    """An optional-fill default of the wrong TYPE is the quiet sequel to this bug: the column
    reads back, but as `0` where the row holds `0.0`. Every arithmetic consumer still works,
    and the difference surfaces somewhere unrelated — a `==` against a float, or a dtype that
    makes a whole pandas column integral and silently truncates a later merge.
    """
    ddl_types = {
        m.group(1): m.group(2)
        for m in re.finditer(r'^\s+(\w+)\s+(INTEGER|REAL|TEXT)\b', pd._CREATE_BATCH_STATS, re.M)
    }
    assert 'work_day' in ddl_types and 'released_late' in ddl_types, (
        f'the DDL scan found {len(ddl_types)} columns and not the two that motivated this '
        f'file — the regex has stopped matching and this test is checking nothing')

    for col, default in pd._BATCH_OPTIONAL.items():
        t = ddl_types.get(col)
        assert t is not None, f'{col} is optional-filled but is not a batch_stats column'
        if col in pd.BATCH_UNKNOWN_ON_OLDER_VINTAGES:
            # Unknown-by-design: a vintage that never recorded the column must read None,
            # never a zero in the column's type (an exhausted index / a counted zero).
            assert default is None, f'{col} is declared unknown-on-older-vintages but ' \
                                    f'defaults to {default!r}'
            continue
        if t == 'REAL':
            assert isinstance(default, float), f'{col} is REAL but defaults to {default!r}'
        elif t == 'INTEGER':
            assert isinstance(default, int) and not isinstance(default, bool), (
                f'{col} is INTEGER but defaults to {default!r}')
