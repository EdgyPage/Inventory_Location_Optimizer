"""test_deferred_indices.py — the run DB is written UNINDEXED and indexed at run end.

## Why this file exists, and what it is the only check for

`workunits` creates an arm's database with `defer_indices=True` and the arm's `_finish()` calls
`build_run_indices` once its last row is in. That trade is worth ~15% of `save_s`, and it has a
failure mode nothing else in this repo can see:

**A regression where `build_run_indices` silently built NOTHING would pass every gate.** The
digest surface (`Tests/architecture/test_digest_surface.py`) enumerates `type='table'` and never
looks at indexes; the schema-identity gate hashes the DECLARED shape, which is built by replaying
the DDL into a `:memory:` database and therefore always has them. Both reason about declared DDL.
Neither reasons about a FINISHED FILE. So the finished file is what this file asserts about.

## The shape-neutrality argument, and why it is tested rather than asserted in a comment

Deferring is only invisible because `_apply_run_schema` still applies BOTH halves, so
`sim_schema_id()` does not move and a finished database is the same artifact it always was. That
property is one careless edit away from being lost -- moving the index half out of
`_apply_run_schema` to "tidy up" would silently orphan every archived vintage, with no error.
`test_the_declared_shape_still_contains_every_index` and its non-vacuity twin below are what
notice.

Run:  python -m pytest Tests/unit/test_deferred_indices.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.persistence.Picking_Data import (
    _RUN_INDEXES, _apply_run_tables, build_run_indices, declared_sim_schema_shape, init_run_db,
    sim_schema_id)
from Schema import shape as _shape

#: The roster, written out rather than derived from `_RUN_INDEXES`. Deriving it would make this
#: test agree with the source by construction, which is exactly the property a roster test must
#: not have: dropping an index would change both sides and the test would still pass.
EXPECTED = (
    'ix_am_run_aisle', 'ix_am_run_batch', 'ix_bs_run', 'ix_pe_run_batch',
    'ix_pe_run_batch_time', 'ix_picks_run_batch', 'ix_rq_run_batch', 'ix_ss_run',
    'ix_we_run_batch', 'ix_we_run_tabs',
)


def _indexes(path) -> list:
    """Every named index actually in the file. `sql IS NULL` skips SQLite's autoindexes."""
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
    finally:
        con.close()


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / 'sim.db')


# ── the default arm is unchanged ──────────────────────────────────────────────────

def test_init_run_db_still_creates_every_index_by_default(db):
    """Everything that is not the arm writer keeps getting a complete database."""
    init_run_db(db)
    assert tuple(_indexes(db)) == EXPECTED


# ── the deferred arm ──────────────────────────────────────────────────────────────

def test_defer_indices_creates_none(db):
    init_run_db(db, defer_indices=True)
    assert _indexes(db) == [], 'the write path must see an unindexed database'


def test_build_run_indices_creates_exactly_the_roster(db):
    """THE ONE THAT CLOSES THE HOLE: a build that quietly does nothing fails here."""
    init_run_db(db, defer_indices=True)
    elapsed = build_run_indices(db)
    assert tuple(_indexes(db)) == EXPECTED
    assert elapsed >= 0.0, 'the caller must be able to charge the time it took'


def test_the_deferred_file_ends_up_identical_to_the_eager_one(tmp_path):
    """Deferral moves WHEN, never WHAT. Two files, same shape id."""
    eager, deferred = str(tmp_path / 'a.db'), str(tmp_path / 'b.db')
    init_run_db(eager)
    init_run_db(deferred, defer_indices=True)
    build_run_indices(deferred)

    assert _indexes(eager) == _indexes(deferred)
    ca, cb = sqlite3.connect(eager), sqlite3.connect(deferred)
    try:
        assert _shape.shape_id(_shape.canonical_shape(ca)) == \
               _shape.shape_id(_shape.canonical_shape(cb))
    finally:
        ca.close(); cb.close()


def test_building_twice_is_a_no_op(db):
    """A resumed arm reaches its run end again; every CREATE is IF NOT EXISTS."""
    init_run_db(db, defer_indices=True)
    build_run_indices(db)
    before = _indexes(db)
    build_run_indices(db)
    assert _indexes(db) == before


# ── shape neutrality, and the non-vacuity that makes it mean something ────────────

def test_the_declared_shape_still_contains_every_index():
    """`_apply_run_schema` must keep applying BOTH halves, or archived vintages orphan."""
    declared = declared_sim_schema_shape()
    named = {ix['name'] for t in declared['tables'].values() for ix in t.get('indexes', ())}
    missing = set(EXPECTED) - named
    assert not missing, f'the declared shape lost {sorted(missing)}'


def test_a_tables_only_declaration_would_have_a_DIFFERENT_id():
    """NON-VACUITY for the test above.

    If the declared shape ever stopped applying the index half, `sim_schema_id()` would move --
    and this asserts the two really are distinguishable, so the test above cannot be passing
    because `canonical_shape` ignores indexes.
    """
    con = sqlite3.connect(':memory:')
    try:
        _apply_run_tables(con)
        tables_only = _shape.shape_id(_shape.canonical_shape(con))
    finally:
        con.close()
    assert tables_only != sim_schema_id(), (
        'a tables-only shape hashes the same as tables+indexes, so the shape layer cannot see '
        'indexes at all and every index assertion in this file is vacuous')


def test_the_roster_matches_the_tuple_the_writer_iterates():
    """The literal roster above and `_RUN_INDEXES` must not drift apart silently."""
    import re
    declared = {m.group(1) for ddl in _RUN_INDEXES
                for m in [re.search(r'CREATE INDEX IF NOT EXISTS (\w+)', ddl)] if m}
    assert declared == set(EXPECTED), (
        f'_RUN_INDEXES and this file disagree: only in tuple {sorted(declared - set(EXPECTED))}, '
        f'only in roster {sorted(set(EXPECTED) - declared)}')
