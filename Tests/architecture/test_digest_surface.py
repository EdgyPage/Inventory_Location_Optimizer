"""test_digest_surface.py — the byte-identity digest must know every table a run writes.

`Tests/bench/run_digest.py` is the tool this repo reaches for to answer the question its whole
discipline rests on: "did that refactor change a number?" It hashes every table of two run
trees and prints IDENTICAL or names the table that moved.

IT HAS BEEN DEAD. Five tables — `yard_trailers`, `yard_drains`, `site_receiving`,
`shift_days`, `free_index` — are created UNCONDITIONALLY by `Picking_Data._apply_run_schema`
and appear in none of the tool's buckets, so its own `_surface_check` raised `SystemExit` on
every sim DB in existence. Not "silently under-hashed": it refused to start at all. Verified
by building the schema in memory and calling the check:

    run_digest: <db> holds table(s) this gate does not know about:
    ['free_index', 'shift_days', 'site_receiving', 'yard_drains', 'yard_trailers']

# ── why it rotted, and why this file is in `architecture/` ────────────────────────

`Tests/bench/` is in NO gate — it is hand-run tooling, and this repo has the scar: three dead
frozen-oracle tests and a never-executed feature were found rotting in exactly that kind of
directory. The tool's own docstring says "A whitelist nobody checks is how three tables went
unhashed for…" — it anticipated this failure and still could not prevent it, because the only
thing that would have caught it was running the tool, and nothing ran the tool.

So the guard goes where the drift gates live and are collected. The point is not to re-test
the tool; it is to make the SIXTH omission impossible without anyone remembering this file
exists. That is the `test_config_reaches_the_worker` pattern: a check per knob can only be
added after someone has already remembered the seam exists, so state the rule generically.

# ── the rule, and why it is stated TWICE ─────────────────────────────────────────

Every table a run DB holds lands in exactly one declared bucket: hashed (`SIM_TABLES` /
`KEYFRAME_TABLES` / `WAREHOUSE_TABLES`), deliberately excluded (`OUT_OF_SURFACE`, which
carries a written reason), or not a table at all (`NOT_A_TABLE`, the views).

It is checked from two directions because the first one alone was not enough:

  * a SOURCE PARSE of `Picking_Data`'s CREATE statements, which sees tables created behind a
    conditional a test build might not take; and
  * a REAL BUILD of each of the three DB kinds through its own initialiser, with the tool's
    own `_surface_check` over the result.

THE SIXTH TABLE IS WHY THE SECOND EXISTS. After the five above were declared, the digest was
still dead for keyframes: `schema_meta` is written by the Schema layer
(`Schema/identity.meta_ddl`, installed by `compat.stamp_checked`) and appears in NEITHER
persistence module, so a parse of `Picking_Data` was blind to it by construction. It was
caught by actually running the tool, which is the same lesson one level up -- a gate that
resolves names cannot catch a wrong relationship, and the fix shape is to exercise the thing
and assert it produced something.

"I forgot" becomes a failure here; "I decided" stays a one-line edit with a reason attached,
exactly as `OUT_OF_SURFACE`'s own comment intends.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_BENCH = os.path.join(_ROOT, 'Tests', 'bench')
for _p in (_ROOT, _BENCH):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_digest as rd                                            # noqa: E402
from Optimization.persistence.Picking_Data import _apply_run_schema  # noqa: E402

_PICKING_DATA = os.path.join(_ROOT, 'Optimization', 'persistence', 'Picking_Data.py')


def _created_tables() -> set:
    """Every table name `Picking_Data`'s DDL constants create.

    Read from the SOURCE rather than by opening a DB, so a table added behind a conditional
    the in-memory build happens not to take is still caught.
    """
    src = io.open(_PICKING_DATA, encoding='utf-8').read()
    return set(re.findall(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_0-9]+)', src, re.I))


def _created_views() -> set:
    src = io.open(_PICKING_DATA, encoding='utf-8').read()
    return set(re.findall(r'CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_0-9]+)', src, re.I))


def _declared() -> dict:
    return {
        'SIM_TABLES': set(rd.SIM_TABLES),
        'KEYFRAME_TABLES': set(rd.KEYFRAME_TABLES),
        'WAREHOUSE_TABLES': set(rd.WAREHOUSE_TABLES),
        'OUT_OF_SURFACE': set(rd.OUT_OF_SURFACE),
        'NOT_A_TABLE': set(rd.NOT_A_TABLE),
    }


# ── the rule ──────────────────────────────────────────────────────────────────────

def test_every_created_table_is_declared_in_exactly_one_bucket():
    """THE GATE. A table nobody declared is a table the digest cannot hash — or, as here,
    a table that stops the digest running at all."""
    created = _created_tables()
    buckets = _declared()
    everything = set().union(*buckets.values())

    undeclared = sorted(created - everything)
    assert not undeclared, (
        f'{undeclared} are created by Picking_Data._apply_run_schema but appear in no '
        f'run_digest bucket. `_surface_check` raises SystemExit on any table it does not '
        f'know, so the byte-identity digest cannot run on ANY run DB until each is placed '
        f'in SIM_TABLES (hashed) or OUT_OF_SURFACE (with a written reason).')

    doubled = sorted(t for t in created
                     if sum(1 for b in buckets.values() if t in b) > 1)
    assert not doubled, (
        f'{doubled} appear in more than one bucket; a table hashed twice reports one '
        f'difference as two, and one excluded-and-hashed is simply ambiguous.')


# The ghost check (a bucket entry naming nothing that exists) USED to live here as a
# source parse and had to exempt WAREHOUSE_TABLES, whose CREATEs are in another file.
# It is now per-DB-kind against a real build, at the bottom of this file -- which needs
# no exemption and covers tables from any source. Removed rather than kept alongside:
# the parse version reported `schema_meta` as a ghost precisely BECAUSE the parse cannot
# see the Schema layer, i.e. it was wrong in exactly the way that let the sixth table
# through.


def test_every_view_is_declared_not_a_table():
    """Hashing a VIEW double-counts its sources and reports one cause as two differences."""
    views = _created_views()
    assert views, 'no views found — the regex or the schema moved'
    missing = sorted(views - set(rd.NOT_A_TABLE))
    assert not missing, f'{missing} are VIEWs and must be listed in NOT_A_TABLE'


def test_out_of_surface_entries_carry_a_reason():
    """`OUT_OF_SURFACE` is a whitelist, and a whitelist without reasons is a suppression
    list. Its own comment says the check turns "I forgot" into an error and leaves "I
    decided" as a one-line edit WITH A REASON — so require the reason."""
    for table, reason in rd.OUT_OF_SURFACE.items():
        assert isinstance(reason, str) and len(reason) > 30, (
            f'OUT_OF_SURFACE[{table!r}] must carry a real reason why its content is not '
            f'comparable between two runs; got {reason!r}')


# ── the tool actually runs, on every DB KIND it opens ─────────────────────────────
# THE CHECK THAT ACTUALLY CATCHES THINGS, and the one whose absence let a sixth table
# through. The source-parse tests above read `Picking_Data`'s CREATE statements, so they
# can only ever see tables THAT FILE creates. `schema_meta` is written by the Schema layer
# (`Schema/identity.meta_ddl`, installed by `compat.stamp_checked`) into the keyframe and
# warehouse DBs, so the parse was blind to it and the digest was still dead for keyframes
# after the first five were declared.
#
# These build each DB with its REAL initialiser and put the tool's own check over the
# result. A table from any source, conditional or not, is caught.

_KINDS = ('sim', 'keyframe', 'warehouse', 'runtime')


def _build(kind, tmp_path):
    from Optimization.persistence.Picking_Data import init_run_db, init_keyframe_db
    from Optimization.persistence.Warehouse_Data import init_warehouse_db
    # runtime_metrics has no named initialiser: `record_arm` applies `_ALL_DDL` inline.
    # Building from the same constant is the faithful equivalent -- and is how the SEVENTH
    # undeclared `schema_meta` was caught, since `_ALL_DDL` carries the stamp DDL too.
    from Optimization.persistence.runtime_metrics import _ALL_DDL

    def init_runtime_db(path):
        import sqlite3 as _s
        con = _s.connect(path)
        try:
            for stmt in _ALL_DDL:
                con.execute(stmt)
            con.commit()
        finally:
            con.close()
    init = {'sim': init_run_db, 'keyframe': init_keyframe_db,
            'warehouse': init_warehouse_db, 'runtime': init_runtime_db}[kind]
    bucket = {'sim': rd.SIM_TABLES, 'keyframe': rd.KEYFRAME_TABLES,
              'warehouse': rd.WAREHOUSE_TABLES, 'runtime': rd.RUNTIME_TABLES}[kind]
    path = os.path.join(str(tmp_path), f'{kind}.db')
    init(path)
    return path, bucket


@pytest.mark.parametrize('kind', _KINDS)
def test_the_surface_check_passes_on_a_real_db_of_every_kind(kind, tmp_path):
    """Build the DB a writer actually creates and run the tool's own check over it."""
    path, bucket = _build(kind, tmp_path)
    con = sqlite3.connect(path)
    try:
        rd._surface_check(con, f'<{kind}>', bucket)
    except SystemExit as exc:
        pytest.fail(f'run_digest cannot read a {kind} DB it was built to hash: {exc}')
    finally:
        con.close()


@pytest.mark.parametrize('kind', _KINDS)
def test_no_bucket_declares_a_table_its_own_db_kind_does_not_have(kind, tmp_path):
    """The other direction, per kind: a stale entry exempts nothing and hides the next one."""
    path, bucket = _build(kind, tmp_path)
    con = sqlite3.connect(path)
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()
    ghosts = sorted(set(bucket) - have)
    assert not ghosts, (
        f'{kind} bucket declares {ghosts}, which a real {kind} DB does not contain')


@pytest.mark.parametrize('kind', _KINDS)
def test_the_surface_check_would_still_catch_an_undeclared_table(kind, tmp_path):
    """SABOTAGE, per kind: the tests above prove nothing unless an extra table still raises."""
    path, bucket = _build(kind, tmp_path)
    con = sqlite3.connect(path)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS a_table_nobody_declared (run_id INTEGER)')
        with pytest.raises(SystemExit, match='a_table_nobody_declared'):
            rd._surface_check(con, f'<{kind}>', bucket)
    finally:
        con.close()


# ── the runtime table: what its digest can and cannot see ─────────────────────────

def _runtime_cols(tmp_path):
    path, _bucket = _build('runtime', tmp_path)
    con = sqlite3.connect(path)
    try:
        return path, [r[1] for r in con.execute('PRAGMA table_info(runtime)')]
    finally:
        con.close()


def test_every_runtime_second_is_excluded_from_the_digest(tmp_path):
    """Wall-clock columns MUST be excluded, or the table reports DIFFERS on every run and a
    reader learns to ignore it -- which is worse than not hashing it at all."""
    _path, cols = _runtime_cols(tmp_path)
    excluded = rd.EXCLUDED_COLS['runtime']
    leaked = sorted(c for c in cols if c.endswith('_s') and c not in excluded)
    assert not leaked, (
        f'{leaked} are wall-clock seconds still inside the digest surface; they reproduce on '
        f'no two runs, so the runtime table would always differ')


def test_the_runtime_digest_still_sees_the_arm_identity_and_the_column_set(tmp_path):
    """What it DOES catch, asserted so the exclusions above cannot quietly empty it out.

    A digest that excluded everything would pass every comparison and mean nothing. The
    surviving columns are the arm's identity and shape, and `_table_digest` seeds the hash
    with the COLUMN LIST itself -- which is how a dropped or renamed section column shows up
    even though its seconds are excluded.
    """
    path, _cols = _runtime_cols(tmp_path)
    con = sqlite3.connect(path)
    try:
        dig = rd._table_digest(con, 'runtime')
    finally:
        con.close()
    kept = set(dig['cols'])
    for must in ('cell', 'pair', 'config', 'channel', 'arm',
                 'initial', 'assignment', 'n_bins', 'regime_bins', 'n_aisles', 'batches'):
        assert must in kept, f'{must} was excluded; the runtime digest no longer identifies an arm'
    assert dig['sha'] is not None, 'the runtime table digested to nothing'


def test_the_declared_section_partition_matches_the_table(tmp_path):
    """`SECTIONS` is the stacked graph's partition and this table is where it reads from. A
    section named there but absent here would plot a column that does not exist."""
    from Optimization.persistence.runtime_metrics import SECTIONS, OUTSIDE_TOTAL
    _path, cols = _runtime_cols(tmp_path)
    have = set(cols)
    for col, _label in list(SECTIONS) + list(OUTSIDE_TOTAL):
        assert col in have, (
            f'{col} is declared as a runtime section but the table has no such column')
