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

import sqlite3

import pytest

# Importing the writers is what populates the registry — Schema/ imports no writer, so a family
# exists only once its own module has been loaded.
from Optimization.persistence import Picking_Data, Warehouse_Data, runtime_metrics  # noqa: F401
from Schema import identity, shape

EXPECTED_FAMILIES = {'sim_db', 'keyframes_db', 'warehouse_db', 'runtime_metrics_db'}


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
