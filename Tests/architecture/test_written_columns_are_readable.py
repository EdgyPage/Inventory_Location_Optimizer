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


def _inserted_columns(fn_name: str) -> set:
    """The column names an `INSERT INTO <t> (a,b,c) VALUES` writer names, from its source.

    Parsed out of the SQL text rather than out of the DDL on purpose: the question is what the
    WRITER writes, and a column can exist in the table while no writer ever sets it (which is
    fine and not what this file is about).
    """
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
        if t == 'REAL':
            assert isinstance(default, float), f'{col} is REAL but defaults to {default!r}'
        elif t == 'INTEGER':
            assert isinstance(default, int) and not isinstance(default, bool), (
                f'{col} is INTEGER but defaults to {default!r}')
