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


#: THE REGEX IS GONE. It recovered a writer's column list with
#: `re.findall(r"\'([^\']*)\'", inspect.getsource(fn))`, covered 1 writer of 16, and four of
#: the sixteen defeated it -- three of those for a reason no amount of regex fixes: an
#: APOSTROPHE in their docstring ("strategy_runner\'s close-out row") shifts the quote pairing
#: and garbles everything after it. `Picking_Data.WRITE_SURFACES` is the declaration each
#: writer's INSERT is now BUILT FROM, so there is nothing left to recover.


def _inserted_columns(table: str) -> set:
    """The columns the writer for `table` writes, off its declaration."""
    return set(pd.WRITE_SURFACES[table])


def test_every_written_column_can_be_read_back_for_every_table():
    """THE regression, now for all nineteen tables instead of one.

    `work_day` and `released_late` were written and unreadable for three days; this is the
    assertion that would have failed on the commit that did it. `items_realized` and
    `bins_realized` were the third instance and were found by exactly this check, on its first
    run -- 7 and 2 went into the file and 0 and 0 came back out.
    """
    checked = 0
    for table, read in pd._READ_SURFACES.items():
        written = _inserted_columns(table)
        missing = written - set(read) - pd._WRITE_ONLY_BY_DESIGN
        assert not missing, (
            f'{table} columns are WRITTEN but never SELECTed: {sorted(missing)}. They read '
            f'back as their Python default on every run, including runs that recorded a real '
            f'value. Add them to the read surface with the pre-column TRUE value as the '
            f'optional fill, or stop writing them.')
        checked += 1
    assert checked >= 9, f'only {checked} tables have a read surface to check against'


def test_the_ratchet_can_actually_fail():
    """Non-vacuity. A scan that silently returned an empty set would pass forever while
    checking nothing; this repo once had 57 tests that could not fail."""
    written = _inserted_columns('batch_stats')
    assert len(written) > 20, f'only {len(written)} columns declared; the surface is empty'
    assert 'work_day' in written, 'the declaration lost the column that motivated this file'
    # and the comparison bites on a column no reader has
    assert 'no_such_column' not in set(pd._BATCH_COLS)
    assert ({'no_such_column'} - set(pd._BATCH_COLS) - pd._WRITE_ONLY_BY_DESIGN
            == {'no_such_column'}), 'the set difference is not doing what the check relies on'


def test_every_declaration_is_the_sql_its_writer_uses():
    """A declaration nobody exercises is a second list, not one list.

    The check above trusts `WRITE_SURFACES` instead of parsing SQL, so what has to be true is
    that the SQL IS built from it -- otherwise the ratchet reads a declaration the code
    ignores, which is worse than the regex it replaced. Read off the GENERATED statement.
    """
    for table, cols in pd.WRITE_SURFACES.items():
        sql = pd._INSERT_SQL[table]
        m = re.search(r'INSERT (?:OR REPLACE )?INTO (\w+)\s*\(([^)]*)\)', sql, re.I)
        assert m, f'{table}: the generated INSERT no longer names its columns'
        assert m.group(1) == table, f'{table}: generated an INSERT into {m.group(1)}'
        in_sql = tuple(c.strip() for c in m.group(2).split(','))
        assert in_sql == tuple(cols), (
            f'{table}: declaration and SQL disagree: {sorted(set(in_sql) ^ set(cols))}')
        assert sql.count('?') == len(cols), (
            f'{table}: {sql.count("?")} placeholders for {len(cols)} columns -- the exact '
            f'misalignment a hand-kept pair invites')


def test_every_writer_goes_through_the_declaration():
    """A writer that still spells its own INSERT would simply not be checked -- which is how
    the regex version covered 1 of 16 while reading as if it covered them all.

    Two shapes are legal: `_INSERT_SQL['<table>']`, the generated statement; and a literal
    `INSERT INTO <table>` whose column list is built from `WRITE_SURFACES[...]` in the same
    expression (`simulation_runs`, which predates this and already did it). Anything else is a
    writer outside every check in this file.
    """
    import ast

    tree = ast.parse(inspect.getsource(pd))
    generated, literal = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == '_INSERT_SQL'
                and isinstance(node.slice, ast.Constant)):
            generated.add(node.slice.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = re.search(r'INSERT (?:OR REPLACE )?INTO (\w+)', node.value, re.I)
            if m:
                literal.add(m.group(1))

    undeclared = sorted((generated | literal) - set(pd.WRITE_SURFACES))
    assert not undeclared, (
        f'these tables are written and have no WRITE_SURFACES entry: {undeclared}')
    assert len(generated) >= 17, (
        f'only {len(generated)} writers use the generated statement; the scan is broken or a '
        f'writer went back to spelling its own SQL')
    assert literal <= {'simulation_runs'}, (
        f'these writers spell their own INSERT again: {sorted(literal)} -- the column list '
        f'and the value tuple are two hand-kept lists the moment they do')


def test_a_row_has_exactly_one_value_per_declared_column():
    """The other half of the misalignment: a value tuple the wrong length.

    Hand-written, the column string and the value tuple were kept parallel by eye, and a
    column inserted in one and not the other shifts every value after it by one -- which
    SQLite accepts whenever the types happen to line up.
    """
    row = pd._batch_row(7, pd.BatchStats(
        run_id=7, batch_id=1, duration=1.0, num_tasks=2, total_items=3,
        avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5))
    cols = pd.WRITE_SURFACES['batch_stats']
    assert len(row) == len(cols)
    assert row[cols.index('run_id')] == 7, 'run_id is the call argument'
    assert row[cols.index('duration')] == 1.0
    assert row[cols.index('is_outlier')] == 0, (
        'is_outlier must reach SQLite as an int, not a bool left to the adapter')


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
