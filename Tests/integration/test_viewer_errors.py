"""test_viewer_errors.py — the viewer's schema-failure boundary: everything 501s, nothing 500s.

The identity unification re-based `SimSchemaError` onto `Schema.identity.SchemaError` and
re-pointed `reader_for` at `identity.resolve`.  These tests pin the two contracts that
unification must never lose: (1) the hierarchy is ONE tree, so a single `except SchemaError`
at the server boundary covers the viewer's own errors and the shared pipeline's alike;
(2) an unvetted sim DB reaching a route produces the JSON 501 body — the server works, the
file is unsupported — never a Flask 500.

Fixture trees are built with tempdir helpers only — no run-tree path literals beyond the
resolver-free single-cell fallback layout the discovery docstring documents.

Run:  python -m pytest Tests/integration/test_viewer_errors.py -q
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from Schema.identity import SchemaDrift, SchemaError, UnsupportedSchema
from Schema.dataset import DatasetError, UnsupportedQuery
from Visualization.readers import (SimSchemaDrift, SimSchemaError, UnsupportedSimSchema,
                                   reader_for)


# ── the hierarchy is one tree ────────────────────────────────────────────────────

def test_every_schema_failure_class_shares_one_root():
    for cls in (SimSchemaError, UnsupportedSimSchema, SimSchemaDrift,
                SchemaDrift, UnsupportedSchema, DatasetError, UnsupportedQuery):
        assert issubclass(cls, SchemaError), cls.__name__


def test_viewer_errors_stay_catchable_by_their_old_name():
    """Existing callers catch SimSchemaError; the re-base must not orphan them."""
    assert issubclass(UnsupportedSimSchema, SimSchemaError)
    assert issubclass(SimSchemaDrift, SimSchemaError)


# ── reader_for on an unvetted file ───────────────────────────────────────────────

def _unvetted_sim_db(path: str) -> None:
    """A sim DB whose shape hashes to nothing any family vets — but which still carries
    the simulation_runs row discovery needs to list the arm at all."""
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE simulation_runs (run_id INTEGER PRIMARY KEY, '
                'n_batches INTEGER, strategy_key TEXT)')
    con.execute('INSERT INTO simulation_runs VALUES (1, 3, "uni_fifo_norsl")')
    con.commit()
    con.close()


def test_reader_for_refuses_an_unvetted_shape_with_a_structural_diff(tmp_path):
    sim = str(tmp_path / 'sim_uni_fifo_norsl.db')
    _unvetted_sim_db(sim)
    with pytest.raises(UnsupportedSimSchema) as exc:
        reader_for(sim, warehouse_db='', run_id=1)
    msg = str(exc.value)
    assert 'no vetted reader' in msg
    assert 'Difference from the current declaration' in msg   # a diff, not a bare hash
    assert isinstance(exc.value, SchemaError)                 # catchable at the 501 boundary


def test_a_lying_pin_raises_the_shared_drift_error(tmp_path):
    """resolve now comes from Schema.identity: a pinned id contradicted by the file under
    verify=True raises identity.SchemaDrift — inside the unified hierarchy."""
    sim = str(tmp_path / 'sim_x.db')
    _unvetted_sim_db(sim)
    with pytest.raises(SchemaError):
        reader_for(sim, warehouse_db='', run_id=1,
                   pinned_schema_id='deadbeefcafe', verify=True)


# ── the server boundary: JSON 501, never a 500 ───────────────────────────────────

@pytest.fixture()
def viewer_app(tmp_path, monkeypatch):
    """The Flask app pointed at a tiny resolver-free tree holding ONE unvetted arm.

    Layout is the documented no-descriptor fallback (single implicit cell):
    <base>/<pair>/<config>/sim_<arm>.db with warehouse.db one level up (the walk-up path).
    """
    base = tmp_path / 'viewbase'
    leaf = base / 'pairA' / 'cfg'
    os.makedirs(leaf)
    _unvetted_sim_db(str(leaf / 'sim_uni_fifo_norsl.db'))
    wh = sqlite3.connect(str(base / 'pairA' / 'warehouse.db'))
    wh.execute('CREATE TABLE aisle_layout (aisle_id INTEGER)')
    wh.commit(); wh.close()

    monkeypatch.setenv('COMPARISON_OUTPUT_DIR', str(base))
    from Visualization import server
    monkeypatch.setattr(server, '_BASE', str(base))
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_unvetted_sim_db_501s_with_the_json_body(viewer_app):
    runs = viewer_app.get('/api/runs').get_json()
    assert runs['runs'], 'discovery listed no arms — the fixture tree shape rotted'
    rid = runs['runs'][0]['id']
    resp = viewer_app.get(f'/api/meta?run={rid}')
    assert resp.status_code == 501, resp.data
    body = resp.get_json()
    assert body['status'] == 501
    assert 'no vetted reader' in body['error']
