"""test_runtime_span_table.py — a span's four spellings, and the hop that used to lose one.

A measured span had FOUR names and no join between the last two:

    accumulator key   log token   result-dict key   runtime_metrics column
    'sample'          smpl=       't_sample'        smpl_s

`SectionTimers.SECTIONS` fed `totals()`, which built the result dict, which crossed the
process seam, which `record_arm` read with a hand-written 32-column INSERT. A section added to
`SECTIONS` flowed automatically as far as the result dict and then **vanished** -- writing
nothing, raising nothing, and leaving a column reading 0.0 which is a legal measurement.

`runtime_metrics.SPANS` is that join, and this file is what keeps it honest. Two halves, and
the second is the one that matters:

  * **THE NAMES RESOLVE.** Every declared column exists in the DDL, and every section column in
    the DDL is declared. Neither list may contain a name the other does not.

  * **THE NAMES ARE USED.** Memory `symbol-table-relationship-not-verified-by-symbols`: a gate
    that resolves names cannot catch a wrong relationship, and this repo has now been bitten by
    that four times -- most recently in this same effort, where `SECTION_MAP`'s eight `t_save`
    anchors all resolved while the section read 0.000000 for a month. So the second half WRITES
    a row through the production writer and reads every column back off a real file.

Run:  python -m pytest Tests/unit/test_runtime_span_table.py -q
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile

import pytest

from Optimization.persistence import runtime_metrics as rm
from Optimization.simdriver.section_timers import COLUMNS, SECTIONS, SectionTimers


def _ddl_columns() -> dict:
    """`{column: declared type}` straight out of the DDL text, so the DDL is the reference and
    not a second hand-maintained list."""
    body = rm._DDL.split('(', 1)[1]
    out = {}
    for line in body.split('\n'):
        line = line.strip().rstrip(',')
        if not line or line.startswith('--') or line.startswith(')'):
            continue
        m = re.match(r'^(\w+)\s+(\w+)', line)
        if m and m.group(1).upper() not in ('PRIMARY', 'UNIQUE', 'FOREIGN', 'CHECK'):
            out[m.group(1)] = m.group(2)
    return out


def _run_dir():
    return tempfile.mkdtemp()


def _read_row(run_root: str) -> dict:
    con = sqlite3.connect(rm.runtime_db_path(run_root))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute('SELECT * FROM runtime').fetchone()
        return dict(r) if r is not None else {}
    finally:
        con.close()


def _res(**over) -> dict:
    """A worker result carrying EVERY declared key, with a distinct value per key so a column
    that took its neighbour's value is visible rather than plausible."""
    res = {'elapsed': 100.0, 'done': 10}
    for n, s in enumerate(rm.SPANS, start=1):
        res[s.result_key] = float(n)
    for n, (_col, key, cast, _absent) in enumerate(rm.RESULT_COLUMNS, start=1):
        res[key] = (n * 100) if cast is int else float(n * 100)
    res.update(over)
    return res


# ── the names resolve ─────────────────────────────────────────────────────────────

def test_every_declared_column_is_in_the_ddl():
    ddl = _ddl_columns()
    declared = ([s.column for s in rm.SPANS]
                + [c for c, _k, _c, _a in rm.RESULT_COLUMNS]
                + ['cell', 'pair', 'config', 'channel', 'arm', 'initial', 'assignment',
                   'batches', 'total_s', 'rate', 'precomp_src'])
    missing = [c for c in declared if c not in ddl]
    assert not missing, f'declared but not in the runtime DDL: {missing}'


def test_every_ddl_section_column_is_declared():
    """The direction that actually loses data. A `*_s` column in the DDL that no span claims is
    a column nothing ever writes -- it reads 0.0 forever, which is a legal measurement."""
    ddl = _ddl_columns()
    claimed = ({s.column for s in rm.SPANS}
               | {c for c, _k, _c, _a in rm.RESULT_COLUMNS}
               | {'total_s'})
    orphans = [c for c in ddl if c.endswith('_s') and c not in claimed]
    assert not orphans, (
        f'{orphans} are seconds columns in the DDL that no span declares -- nothing writes '
        f'them, and 0.0 does not look like an error')


def test_the_span_table_has_no_duplicate_spelling():
    for field in ('section', 'result_key', 'column'):
        vals = [getattr(s, field) for s in rm.SPANS]
        assert len(set(vals)) == len(vals), f'duplicate {field}: {vals}'


def test_the_partition_excludes_every_overlay():
    """`SECTIONS` is a PARTITION for the stacked graph. An overlay in it double-counts its
    seconds -- `smpl`/`task` are sub-splits of `build`, `kf` of `pre`, `p1`/`p2` of `sim`."""
    part = {c for c, _lbl in rm.SECTIONS}
    overlays = {s.column for s in rm.SPANS if s.label is None}
    assert part & overlays == set(), f'an overlay reached the partition: {part & overlays}'
    assert part == {'reord_s', 'build_s', 'pre_s', 'sim_s', 'extract_s', 'inv_s', 'save_s'}, (
        'the partition moved; a stacked graph over it now shows a different set of seconds')


def test_the_timer_vocabulary_is_the_table_and_the_order_is_preserved():
    """`SectionTimers.SECTIONS` is derived now. The ORDER is pinned as a literal because it is
    its own -- neither the log line's nor the DDL's -- and a reorder would be a silent change to
    the result dict's shape."""
    assert SECTIONS == ('reord', 'build', 'sample', 'task', 'kf', 'pre', 'sim',
                        'extract', 'inv', 'save', 'p1', 'p2')
    assert COLUMNS == {'p1': 'p1_s', 'p2': 'p2_s'}, (
        'COLUMNS is the exception list for spans whose result key is not `t_<section>`')


def test_totals_emits_the_result_keys_not_the_columns():
    """The confusion this whole table exists to end: `p1_s`/`p2_s` are the ONE pair where the
    result key and the DB column coincide, which is why reading `t_<name>` as "the column" looks
    plausible and is wrong for the other ten."""
    got = set(SectionTimers().totals())
    assert got == {s.result_key for s in rm.SPANS}
    assert 'smpl_s' not in got and 't_sample' in got


# ── the names are used ────────────────────────────────────────────────────────────

def test_every_declared_span_reaches_its_column_in_a_real_row():
    """THE HOP THAT USED TO LOSE A SECTION, exercised rather than resolved.

    Each span gets a distinct value, so a column that silently took its neighbour's fails here
    instead of looking like a plausible measurement.
    """
    root = _run_dir()
    rm.record_arm(root, 'cellA', _res(), pair='p', config='c', channel='store',
                  arm='uni_rank_norsl')
    row = _read_row(root)
    assert row, 'record_arm wrote no row at all'
    for n, s in enumerate(rm.SPANS, start=1):
        assert row[s.column] == pytest.approx(float(n)), (
            f'span {s.section!r} declared result key {s.result_key!r} -> column {s.column!r}, '
            f'and the column holds {row[s.column]!r} instead of {float(n)} -- the hop from the '
            f'result dict to the row is exactly where a section used to vanish')


def test_every_declared_result_column_reaches_its_column_too():
    root = _run_dir()
    rm.record_arm(root, 'cellA', _res(), pair='p', config='c', channel='store',
                  arm='uni_rank_norsl')
    row = _read_row(root)
    for n, (col, _key, cast, _absent) in enumerate(rm.RESULT_COLUMNS, start=1):
        want = (n * 100) if cast is int else float(n * 100)
        assert row[col] == pytest.approx(want), f'{col} holds {row[col]!r}, wanted {want!r}'


def test_an_absent_span_writes_its_declared_absent_and_not_a_crash():
    """A crashed or legacy result dict must write the declared value, never raise: `record_arm`
    is best-effort and a runtime-DB hiccup must not sink a real run."""
    root = _run_dir()
    rm.record_arm(root, 'cellA', {'elapsed': 1.0, 'done': 1}, pair='p', config='c',
                  channel='store', arm='uni_rank_norsl')
    row = _read_row(root)
    for s in rm.SPANS:
        assert row[s.column] == pytest.approx(s.absent), f'{s.column} ignored its declared absent'


def test_a_column_that_must_stay_NULL_stays_NULL():
    """THE DISTINCTION THE BLANKET `or 0.0` WOULD ERASE. A worker that did not measure the
    precompute must write NULL: 0.0 would mean "measured as zero", and telling those apart is
    the entire job of `precomp_src`."""
    root = _run_dir()
    rm.record_arm(root, 'cellA', {'elapsed': 1.0, 'done': 1}, pair='p', config='c',
                  channel='store', arm='uni_rank_norsl')
    row = _read_row(root)
    for col in ('precomp_s', 'map_lap_pct', 'peak_rss_mib', 'live_objects'):
        assert row[col] is None, f'{col} wrote {row[col]!r}; NULL and 0 are different claims'
    assert row['precomp_src'] is None

    root2 = _run_dir()
    rm.record_arm(root2, 'cellA', {'elapsed': 1.0, 'done': 1, 't_precompute': 0.0},
                  pair='p', config='c', channel='store', arm='uni_rank_norsl')
    row2 = _read_row(root2)
    assert row2['precomp_s'] == pytest.approx(0.0), 'a MEASURED zero was turned into NULL'
    assert row2['precomp_src'] == 'inline', (
        'precomp_src is what distinguishes a measured 0.0 from an unmeasured NULL')


def test_the_row_is_idempotent_by_its_key():
    """`INSERT OR REPLACE` on (cell,pair,config,channel,arm): a re-run or resume of one arm
    overwrites its row rather than adding a second."""
    root = _run_dir()
    for v in (1.0, 2.0):
        rm.record_arm(root, 'cellA', _res(t_reord=v), pair='p', config='c', channel='store',
                      arm='uni_rank_norsl')
    con = sqlite3.connect(rm.runtime_db_path(root))
    try:
        assert con.execute('SELECT COUNT(*) FROM runtime').fetchone()[0] == 1
    finally:
        con.close()
    assert _read_row(root)['reord_s'] == pytest.approx(2.0)


def test_a_span_added_without_a_column_fails_loudly():
    """NON-VACUITY for the resolve half. The failure this ticket is about is silent, so the
    check that replaces it has to be shown to fire."""
    saved = rm.SPANS
    try:
        rm.SPANS = saved + (rm.Span('ghost', 't_ghost', 'ghost_s', None),)
        with pytest.raises(AssertionError):
            test_every_declared_column_is_in_the_ddl()
    finally:
        rm.SPANS = saved
    test_every_declared_column_is_in_the_ddl()          # and it passes again afterwards


def test_a_span_added_without_a_column_also_fails_when_WRITTEN():
    """...and the write half fires too, which is the one that matters: an undeclared column in
    the INSERT is an OperationalError rather than a row missing a value."""
    saved = rm.SPANS
    root = _run_dir()
    try:
        rm.SPANS = saved + (rm.Span('ghost', 't_ghost', 'ghost_s', None),)
        with pytest.raises(sqlite3.OperationalError):
            rm.record_arm(root, 'cellA', _res(), pair='p', config='c', channel='store',
                          arm='uni_rank_norsl')
    finally:
        rm.SPANS = saved
