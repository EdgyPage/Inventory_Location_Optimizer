"""test_sim_schema_id.py — the sim DB's derived schema identity.

A sim DB carries no version number, so `Picking_Data` derives one from its own SQL shape and
stamps it into `simulation_runs.sim_schema_id`.  These tests pin the four properties that make
that id trustworthy:

  **Faithful**    the declared id equals what a freshly-created DB observably has, because it is
                  computed BY creating one — there is no hand-maintained column list to drift.
  **Sensitive**   a real schema change (a column, a type, an index's columns) moves the id.
  **Stable**      things that are not schema changes do NOT move it — ANALYZE, an AUTOINCREMENT
                  insert populating sqlite_sequence, or reordering the CREATE statements.
  **Free**        adding the column does not disturb the run-TREE contract, so no canary run and
                  no re-adopt.  If that stops being true, this suite says so rather than the
                  next simulation discovering it.

    python -m pytest Tests/unit/test_sim_schema_id.py -q
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from Optimization.persistence.Picking_Data import (
    canonical_schema_shape, create_run, declared_sim_schema_shape, init_run_db,
    observed_sim_schema_id, schema_shape_id, sim_schema_id, _apply_run_schema,
)
from Visualization.readers.fingerprint import (
    describe_diff, diff_shapes, read_stamped_id, resolve_schema_id,
)
from Visualization.readers.protocol import SimSchemaDrift

# The shape shipped by every run in the archive, before sim_schema_id existed.  Frozen as a
# literal because those DBs will never be rewritten — 500 GB of them — so the reader has to keep
# recognising this exact id forever.
PRE_STAMP_SCHEMA_ID = '23d0c7f167bc'


def _fresh_db():
    path = os.path.join(tempfile.mkdtemp(prefix='schemaid_'), 'sim.db')
    init_run_db(path)
    return path


def _shape_of(path):
    con = sqlite3.connect(path)
    try:
        return canonical_schema_shape(con)
    finally:
        con.close()


# ── faithful ─────────────────────────────────────────────────────────────────────

def test_declared_equals_observed_on_a_fresh_db():
    """The whole design: the declaration is produced by running the writer."""
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        assert observed_sim_schema_id(con) == sim_schema_id()
    finally:
        con.close()


def test_declared_shape_covers_every_table_the_viewer_reads():
    tables = declared_sim_schema_shape()['tables']
    expected = {'simulation_runs', 'batch_stats', 'task_stats', 'picker_events', 'picks',
                'bin_inventory', 'aisle_metrics', 'reorder_queue', 'bin_scores', 'sku_scores'}
    assert expected <= set(tables), sorted(expected - set(tables))
    # No sqlite_-internal table may ever enter the hash.
    assert not [t for t in tables if t.startswith('sqlite_')]


def test_create_run_stamps_the_declared_id():
    path = _fresh_db()
    create_run(path, 'uni_fifo_norsl')
    con = sqlite3.connect(path)
    try:
        assert read_stamped_id(con) == sim_schema_id()
    finally:
        con.close()


def test_an_explicit_identity_still_wins():
    """Tests and migrations must be able to pin an older id deliberately."""
    path = _fresh_db()
    create_run(path, 'uni_fifo_norsl', identity={'sim_schema_id': PRE_STAMP_SCHEMA_ID})
    con = sqlite3.connect(path)
    try:
        assert read_stamped_id(con) == PRE_STAMP_SCHEMA_ID
    finally:
        con.close()


# ── sensitive ────────────────────────────────────────────────────────────────────

def test_adding_a_column_changes_the_id():
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        before = observed_sim_schema_id(con)
        con.execute('ALTER TABLE picks ADD COLUMN wave_id INTEGER')
        con.commit()
        assert observed_sim_schema_id(con) != before
    finally:
        con.close()


def test_changing_a_column_type_changes_the_id():
    con = sqlite3.connect(':memory:')
    try:
        _apply_run_schema(con)
        before = observed_sim_schema_id(con)
        con.execute('DROP TABLE bin_scores')
        con.execute('CREATE TABLE bin_scores (run_id INTEGER, aisle_id INTEGER, bayX INTEGER, '
                    'bayY INTEGER, travel_d TEXT, height_mult REAL, layout_score REAL, '
                    'map_pref REAL, PRIMARY KEY (run_id, aisle_id, bayX, bayY))')
        con.commit()
        assert observed_sim_schema_id(con) != before, 'travel_d REAL -> TEXT must be visible'
    finally:
        con.close()


def test_index_columns_are_part_of_the_identity():
    """`(run_id, batch_id)` and `(run_id, batch_id, time)` must not hash alike."""
    a, b = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    try:
        for con, cols in ((a, '(run_id, batch_id)'), (b, '(run_id, batch_id, time)')):
            con.execute('CREATE TABLE e (run_id INTEGER, batch_id INTEGER, time REAL)')
            con.execute(f'CREATE INDEX ix_e ON e {cols}')
            con.commit()
        assert observed_sim_schema_id(a) != observed_sim_schema_id(b)
    finally:
        a.close()
        b.close()


def test_autoincrement_is_part_of_the_identity():
    """PRAGMA table_info cannot see it, and six of the ten tables use it."""
    a, b = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    try:
        a.execute('CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)')
        b.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
        assert observed_sim_schema_id(a) != observed_sim_schema_id(b)
    finally:
        a.close()
        b.close()


# ── stable ───────────────────────────────────────────────────────────────────────

def test_analyze_does_not_change_the_id():
    """ANALYZE creates sqlite_stat1.  Hashing it would orphan every vetted reader for a
    statistics refresh — and ANALYZE is a plausible thing to run, since the top-N SKU query
    is an unindexed 24 s scan on a production DB."""
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        before = observed_sim_schema_id(con)
        con.execute('ANALYZE')
        con.commit()
        assert 'sqlite_stat1' in {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert observed_sim_schema_id(con) == before
    finally:
        con.close()


def test_sqlite_sequence_does_not_change_the_id():
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        before = observed_sim_schema_id(con)
        con.execute("INSERT INTO simulation_runs (run_type, created) VALUES ('x', 'y')")
        con.commit()
        assert con.execute('SELECT COUNT(*) FROM sqlite_sequence').fetchone()[0] > 0
        assert observed_sim_schema_id(con) == before
    finally:
        con.close()


def test_shape_is_sorted_so_statement_order_is_not_a_schema_change():
    """_apply_run_schema issues its CREATEs in a fixed order; reordering those lines is not a
    schema change and must not mint a new id."""
    shape = declared_sim_schema_shape()
    assert list(shape['tables']) == sorted(shape['tables'])
    for table in shape['tables'].values():
        names = [i['name'] for i in table['indexes']]
        assert names == sorted(names)


def test_id_is_stable_across_calls():
    assert sim_schema_id() == sim_schema_id()
    assert schema_shape_id(declared_sim_schema_shape()) == sim_schema_id()


# ── free: the run-tree contract must not move ────────────────────────────────────

def test_picking_data_is_not_a_run_tree_shape_source():
    """Adding a COLUMN to a sim DB must not trigger a canary pair or a contract re-adopt.

    `contract._shape_only` hashes `artifacts.sim_db.tables` — the table LIST — and never a
    column; and `Picking_Data.py` is not among the files whose bytes feed `source_fingerprint`.
    If a refactor adds it, every DDL comment edit starts costing two canary simulations, so
    catch it here rather than in the next run's preflight.
    """
    from Optimization.runschema import contract

    sources = [os.path.normpath(s) for s in contract.SHAPE_SOURCES]
    assert not [s for s in sources if s.endswith(os.path.normpath('persistence/Picking_Data.py'))], (
        'Picking_Data.py became a run-tree shape source; a column change now costs a canary run')

    hashed = contract._shape_only(contract.build())
    assert 'sim_schema_id' not in repr(hashed), 'a column leaked into the run-tree hash'


# ── resolution + diagnostics ─────────────────────────────────────────────────────

def test_pre_stamp_db_resolves_by_derivation():
    """Every DB in the archive takes this path: no column, so derive."""
    con = sqlite3.connect(':memory:')
    try:
        _apply_run_schema(con)
        con.execute('CREATE TABLE runs_backup AS SELECT * FROM simulation_runs')
        con.execute('DROP TABLE simulation_runs')
        con.execute("""CREATE TABLE simulation_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT, run_type TEXT NOT NULL,
            created TEXT NOT NULL)""")
        con.execute('DROP TABLE runs_backup')
        con.commit()
        assert read_stamped_id(con) is None
        schema, source = resolve_schema_id(con)
        assert source == 'derived'
        assert schema == observed_sim_schema_id(con)
    finally:
        con.close()


def test_pinned_id_is_used_without_deriving():
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        con.execute('DELETE FROM simulation_runs')       # nothing stamped
        con.commit()
        schema, source = resolve_schema_id(con, pinned='deadbeefcafe')
        assert (schema, source) == ('deadbeefcafe', 'pinned')
    finally:
        con.close()


def test_verify_catches_a_lying_pin():
    path = _fresh_db()
    con = sqlite3.connect(path)
    try:
        con.execute('DELETE FROM simulation_runs')
        con.commit()
        try:
            resolve_schema_id(con, pinned='deadbeefcafe', verify=True)
        except SimSchemaDrift as exc:
            assert 'deadbeefcafe' in str(exc)
            assert observed_sim_schema_id(con) in str(exc)
        else:
            raise AssertionError('a pin disagreeing with the file must raise SimSchemaDrift')
    finally:
        con.close()


def test_diff_names_the_actual_difference():
    """`hash 4f2a... is unknown` is useless; the message must name columns and indexes."""
    declared = declared_sim_schema_shape()
    con = sqlite3.connect(':memory:')
    try:
        _apply_run_schema(con)
        con.execute('DROP INDEX ix_pe_run_batch_time')
        con.execute('DROP TABLE sku_scores')
        con.commit()
        d = diff_shapes(declared, canonical_schema_shape(con))
        text = describe_diff(d)
    finally:
        con.close()

    assert 'sku_scores' in d['tables_missing']
    assert 'ix_pe_run_batch_time' in d['indexes']['picker_events']['missing']
    assert 'sku_scores' in text and 'ix_pe_run_batch_time' in text


def test_diff_is_empty_for_an_unchanged_db():
    d = diff_shapes(declared_sim_schema_shape(), declared_sim_schema_shape())
    assert not d['tables_missing'] and not d['tables_extra']
    assert not d['columns'] and not d['indexes']
    assert 'no structural difference' in describe_diff(d)
