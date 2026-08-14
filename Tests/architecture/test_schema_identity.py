"""test_schema_identity.py — every registered DB family must have a trustworthy shape id.

A drift gate, not a unit test: it sweeps whatever families the writers have registered and
asserts the same four properties for each, so adding a family gets the guarantees automatically
rather than needing its own bespoke test.

Why this exists
---------------
Nothing in this project raises when a column disappears. Every loader in `Picking_Data.py` does
`SELECT *` and guards with `row.keys()`, and `Performance_Evaluations/` reaches all of them
through those loaders — so a dropped or renamed column does not fail. It silently becomes the
dataclass default `0.0`/NaN in `common/frames.py`, and a published number quietly changes.

    python -m pytest Tests/architecture/test_schema_identity.py -q
"""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

# Importing the writers is what populates the registry — Schema/ imports no writer, so a family
# exists only once its own module has been loaded.  `cache_schema` is the writer for the DERIVED
# viewer sidecar and lives outside Optimization/, which is exactly why the registry is keyed by
# family rather than by package.
from Optimization.persistence import Picking_Data, Warehouse_Data, runtime_metrics  # noqa: F401
from Schema import identity, shape
from Visualization import cache_schema  # noqa: F401
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.generation import generate_affinity, generate_inventory  # noqa: F401

EXPECTED_FAMILIES = {'sim_db', 'keyframes_db', 'warehouse_db', 'runtime_metrics_db',
                     'viz_cache_db', 'inventory_db', 'affinity_db'}

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Where a family's shape is actually VERIFIED before its rows are trusted, and what happens
#: when it is not the shape we expect.  Same idiom as `test_bin_mutation_sites.ALLOWED`: assert
#: against a committed list rather than trusting a docstring, so a consumer added WITHOUT a
#: check fails the suite instead of silently reintroducing the defect this layer exists to
#: remove — a dropped column that becomes a plausible `0.0` in a published figure.
#:
#: If this fails, do not just add a line.  Decide first whether the new consumer should FAIL or
#: WARN: a simulation about to write from a file, or an analysis about to publish a number from
#: one, must fail; an interactive read-only viewer, and a derived cache that can rebuild itself,
#: may degrade.
VERIFIED_BY = {
    'sim_db': [
        ('Optimization/Performance_Evaluations/core/context.py',
         'EvalContext.__init__ -> _verify_sim_dbs; HARD FAIL, this path publishes figures'),
    ],
    'keyframes_db': [
        ('Visualization/db_reader.py', 'RunRef.reader(); WARN, the viewer must open old runs'),
    ],
    'warehouse_db': [
        ('Visualization/db_reader.py', 'RunRef.reader(); WARN, same reason'),
        ('Diagnostics/replay_run.py',
         'read_layout; HARD FAIL, capacity is the denominator of every exported fill %'),
    ],
    'runtime_metrics_db': [
        ('Optimization/persistence/runtime_metrics.py',
         'load_rows; WARN, wall-clock diagnostics are never a published result'),
    ],
    'viz_cache_db': [
        ('Visualization/cache_schema.py',
         'cache_freshness -> "stale"; the file is DERIVED, so precompute just rebuilds it'),
    ],
    'inventory_db': [
        ('Warehouse/generation/generate_inventory.py',
         'load_inventory_from_db; HARD FAIL, every caller is a simulation about to write'),
    ],
    # The one family verified by `check_tables` rather than `check` — its registration lives in
    # a matplotlib-importing data-gen CLI that a simulation worker must not load.  See
    # `AffinityStore._verify_shape`, and `test_the_affinity_mirror_matches_the_family` below,
    # which is what makes the narrower check equivalent for the tables it covers.
    'affinity_db': [
        ('Warehouse/catalog/Affinity_Store.py',
         'AffinityStore._verify_shape; HARD FAIL, wrong lifts place 400k bins plausibly wrong'),
    ],
}

#: Two call shapes, and in both the family name is a string LITERAL — which is the only reason
#: this is greppable, and a good reason to keep it that way:
#:     identity.check(path, 'warehouse_db') / identity.check_or_warn(path, 'warehouse_db')
#:     identity.check_tables(..., label='affinity_db(...)')
_CHECK_CALL = re.compile(
    r"""\bcheck(?:_or_warn)?\s*\(\s*[^,()]+,\s*['"](\w+_db)['"]"""     # positional family
    r"""|\blabel\s*=\s*f?['"](\w+_db)""")                              # check_tables label


def test_the_expected_families_are_registered():
    """A family that stops registering itself would silently lose its guarantees."""
    missing = EXPECTED_FAMILIES - set(identity.families())
    assert not missing, f'family registration lost: {sorted(missing)}'


@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_declared_shape_is_built_not_listed(name):
    """The declaration must come from BUILDING the schema, so it cannot drift from the writer.

    A hand-maintained column list is exactly the kind of declaration that goes stale; this
    asserts the shape is non-trivial and self-consistent.
    """
    family = identity.get(name)
    declared = family.declared_shape()

    assert declared['tables'], f'{name} declares no tables'
    assert family.declared_id() == shape.shape_id(declared)
    assert family.declared_id() in family.supported_ids()
    # sqlite internals must never enter a hash — ANALYZE would otherwise mint a new id.
    assert not [t for t in declared['tables'] if t.startswith('sqlite_')]


@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_id_is_stable_and_order_independent(name):
    family = identity.get(name)
    assert family.declared_id() == family.declared_id()

    declared = family.declared_shape()
    assert list(declared['tables']) == sorted(declared['tables']), 'tables must be sorted'
    for table in declared['tables'].values():
        idx = [i['name'] for i in table['indexes']]
        assert idx == sorted(idx), 'indexes must be sorted'


@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_a_real_change_moves_the_id(name):
    """Sensitivity. If a mutation did NOT move the id, the id would be worthless."""
    family = identity.get(name)
    con = sqlite3.connect(':memory:')
    try:
        first = next(iter(family.declared_shape()['tables']))
        # Rebuild the declared schema, then perturb one table.
        for table, spec in family.declared_shape()['tables'].items():
            cols = ', '.join(f'"{c["name"]}" {c["type"] or ""}'.strip() for c in spec['columns'])
            con.execute(f'CREATE TABLE "{table}" ({cols})')
        before = shape.observed_id(con)
        con.execute(f'ALTER TABLE "{first}" ADD COLUMN _probe INTEGER')
        assert shape.observed_id(con) != before, f'{name}: adding a column did not move the id'
    finally:
        con.close()


@pytest.mark.parametrize('name', sorted(f for f in EXPECTED_FAMILIES
                                        if identity.get(f).meta_table))
def test_stamp_round_trips(name):
    """A stamped file resolves without deriving, and the stamp equals the declared id."""
    family = identity.get(name)
    con = sqlite3.connect(':memory:')
    try:
        for table, spec in family.declared_shape()['tables'].items():
            cols = ', '.join(f'"{c["name"]}" {c["type"] or ""}'.strip() for c in spec['columns'])
            con.execute(f'CREATE TABLE "{table}" ({cols})')
        written = identity.stamp(con, family)
        assert written == family.declared_id()
        assert identity.read_stamp(con, family) == written

        sid, source = identity.resolve(con, family)
        assert (sid, source) == (written, 'stamped'), 'a stamped file must not need deriving'
    finally:
        con.close()


def test_an_unstamped_file_falls_through_to_derivation():
    """Every file in the existing archive takes this path; it must not raise."""
    family = identity.get('sim_db')
    con = sqlite3.connect(':memory:')
    try:
        Picking_Data._apply_run_schema(con)
        con.execute('DELETE FROM simulation_runs')
        sid, source = identity.resolve(con, family)
        assert source == 'derived'
        assert sid == family.declared_id()
    finally:
        con.close()


def test_drift_between_stamp_and_reality_is_caught():
    """`verify=True` must reject a file that was migrated after it was written."""
    family = identity.get('warehouse_db')
    con = sqlite3.connect(':memory:')
    try:
        for stmt in Warehouse_Data._ALL_DDL:
            con.execute(stmt)
        identity.stamp(con, family)
        con.execute('ALTER TABLE aisle_layout ADD COLUMN _drift INTEGER')

        with pytest.raises(identity.SchemaDrift) as exc:
            identity.resolve(con, family, verify=True)
        assert '_drift' in str(exc.value), 'the message must name what actually differs'
    finally:
        con.close()


def test_unsupported_schema_names_the_difference():
    """`hash 4f2a... is unknown` is useless; the error has to say what changed."""
    declared = identity.get('sim_db').declared_shape()
    con = sqlite3.connect(':memory:')
    try:
        Picking_Data._apply_run_schema(con)
        con.execute('DROP TABLE sku_scores')
        text = shape.describe_diff(shape.diff_shapes(declared, shape.canonical_shape(con)))
    finally:
        con.close()
    assert 'sku_scores' in text


# ── the layer must stay WIRED ────────────────────────────────────────────────────
# Stamping an id nothing reads is worse than not stamping one: it looks like protection.
# Everything below exists so that state cannot return unnoticed.

def _verifying_families(relpath: str) -> set:
    """Every family name passed to a `check*` call in one source file."""
    with open(os.path.join(_ROOT, relpath), encoding='utf-8') as fh:
        return {name for match in _CHECK_CALL.findall(fh.read()) for name in match if name}


def test_every_registered_family_is_verified_somewhere():
    """A family whose id nothing checks is a stamp, not a guarantee.

    This is the property the whole package hangs on. Before it existed, `identity.check` and
    `identity.resolve` appeared NOWHERE outside `Schema/` and `Tests/`: six of the seven
    families stamped an id no consumer ever read, so a dropped column stayed exactly as silent
    as it had been.
    """
    unverified = sorted(set(identity.families()) - set(VERIFIED_BY))
    assert not unverified, (
        f'registered but verified nowhere: {unverified}. A family that only STAMPS its id has '
        f'the appearance of protection and none of the substance — wire a check into whatever '
        f'reads it, then add it to VERIFIED_BY with the fail/warn decision.')


@pytest.mark.parametrize('family', sorted(VERIFIED_BY))
def test_the_named_consumer_still_checks_that_family(family):
    """The allowlist must not rot: a refactor that drops the call has to fail here."""
    for relpath, why in VERIFIED_BY[family]:
        assert family in _verifying_families(relpath), (
            f'{relpath} no longer verifies {family!r} ({why}). Either restore the check or '
            f'remove the entry — a stale allowlist permits a consumer that stopped checking.')


def test_no_consumer_verifies_a_family_that_is_not_registered():
    """A typo'd family name raises KeyError at runtime, on the archive, months later."""
    named = {f for entries in VERIFIED_BY.values() for relpath, _ in entries
             for f in _verifying_families(relpath)}
    unknown = sorted(named - set(identity.families()))
    assert not unknown, f'check() called with unregistered family name(s): {unknown}'


def test_the_affinity_mirror_matches_the_family():
    """`AffinityStore._SCHEMA` must agree with the generator's declaration, table for table.

    `AffinityStore` cannot reach the `affinity_db` family — it is registered in a data-gen CLI
    that imports matplotlib, which no simulation worker may load — so it verifies with
    `identity.check_tables` against its own mirrored DDL instead. That substitution is only
    sound while the mirror is exact, and the mirror is a hand-kept copy, which is precisely the
    kind of declaration that drifts. Both imports are free here.
    """
    family = identity.get('affinity_db').declared_shape()['tables']
    mirror = shape.shape_of_ddl((AffinityStore._SCHEMA,))['tables']

    for table in AffinityStore._TABLES:
        assert table in family, f'{table} is no longer declared by the generator'
        assert table in mirror, f'{table} is no longer declared by AffinityStore'
        assert shape.shape_id({'tables': {table: family[table]}}) == \
               shape.shape_id({'tables': {table: mirror[table]}}), (
            f'AffinityStore._SCHEMA has drifted from generate_affinity._SCHEMA on {table!r}: '
            + shape.describe_diff(shape.diff_shapes({'tables': {table: family[table]}},
                                                    {'tables': {table: mirror[table]}}))
            + ' — the store\'s check_tables guarantee is only as good as this mirror.')


@pytest.mark.parametrize('drop_the_stamp, expected', [
    # A file written by THIS build carries a stamp, so its claim and its shape now disagree:
    # `SchemaDrift`.  This is why every wired consumer passes `verify=True` — without it the
    # stamp is believed and the altered file sails through as vetted.
    (False, identity.SchemaDrift),
    # Every file in the archive is unstamped, so the id is derived and simply is not on the
    # list: `UnsupportedSchema`.
    (True, identity.UnsupportedSchema),
])
def test_a_dropped_column_is_rejected_by_name_through_a_real_consumer(
        tmp_path, drop_the_stamp, expected):
    """End to end, through an actual caller: drop a column, get told WHICH column.

    Not `identity.check` in isolation — that would prove the message exists, not that anything
    surfaces it. `Diagnostics/replay_run.read_layout` is the hard-fail warehouse.db consumer,
    and the column dropped here is the one whose loss would silently change every fill
    percentage that tool exports.
    """
    from Diagnostics import replay_run

    path = tmp_path / 'warehouse.db'
    Warehouse_Data.init_warehouse_db(str(path))
    con = sqlite3.connect(str(path))
    try:
        # `aisle_layout` minus `storage_size` — what an interrupted migration leaves behind.
        con.execute('ALTER TABLE aisle_layout DROP COLUMN storage_size')
        if drop_the_stamp:
            con.execute('DROP TABLE schema_meta')
        con.commit()
    finally:
        con.close()

    with pytest.raises(expected) as exc:
        replay_run.read_layout(str(path))
    text = str(exc.value)
    assert 'aisle_layout' in text, f'the message must name the table: {text}'
    assert 'storage_size' in text, f'the message must name the column: {text}'
    assert not text.strip().endswith('is not vetted.'), 'a bare hash is not a diagnosis'


def test_check_or_warn_reports_once_and_returns_none(tmp_path, recwarn):
    """The degrade path must still SAY something, and must not say it 272 times."""
    path = tmp_path / 'runtime_metrics.db'
    con = sqlite3.connect(str(path))
    try:
        con.execute('CREATE TABLE runtime (cell TEXT)')      # nothing like the declaration
        con.commit()
    finally:
        con.close()

    identity._WARNED.discard(('runtime_metrics_db', os.path.abspath(str(path))))
    assert identity.check_or_warn(str(path), 'runtime_metrics_db') is None
    msgs = [str(w.message) for w in recwarn if 'runtime_metrics_db' in str(w.message)]
    assert msgs, 'an unvetted file must not degrade silently'
    assert 'runtime' in msgs[0], f'the warning must name what differs: {msgs[0]}'

    before = len(recwarn)
    assert identity.check_or_warn(str(path), 'runtime_metrics_db') is None
    assert len(recwarn) == before, 'the same file must not be reported twice'
