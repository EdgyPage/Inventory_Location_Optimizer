"""test_run_layout.py

Locks the run_layout.json descriptor (Phase 2 Commits B+C): write/read round-trip, kind inference,
descriptor-driven runlayout.cells() (yields cells with NO sim_*.db on disk — resume-safety), and
the invariant that write_run_layout emits EXACTLY the committed JSON Schema's top-level properties
(so the schema, the writer, and artifacts.yml can't silently drift).

Run:  python -m pytest Tests/test_run_layout.py -q
"""
from __future__ import annotations

import json
import os

from Optimization.runschema.sim_manifest import write_run_layout, read_run_layout
from Optimization.runschema.runlayout import cells

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA = os.path.join(_ROOT, 'Optimization', 'schemas', 'run_layout.schema.json')


def _write(base, **over):
    kw = dict(spec='scheduler_ab', reference='k1_off_rr',
              cells=[('k1_off_rr', None, {'enabled': False}, 'round_robin'),
                     ('k1_off_lpt', None, {'enabled': False}, 'lpt')],
              pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
              ff_cfgs=[{'name': 'ful_calibrated'}], channels=['store', 'fulfillment'],
              arms=None, created='2026-07-15T10:00:00')
    kw.update(over)
    write_run_layout(str(base), **kw)


def test_roundtrip_and_descriptor_cells(tmp_path):
    base = tmp_path / 'comparison_whatif_x'
    base.mkdir()
    _write(base)
    lay = read_run_layout(str(base))
    assert lay['kind'] == 'sweep' and lay['reference'] == 'k1_off_rr'
    assert [c['name'] for c in lay['cells']] == ['k1_off_rr', 'k1_off_lpt']
    assert lay['configs'] == {'store': ['store'], 'fulfillment': ['ful_calibrated']}
    # cells() reads the descriptor even with NO sim_*.db on disk (partial/crashed cells still analyze)
    assert [n for n, _d in cells(str(base))] == ['k1_off_rr', 'k1_off_lpt']


def test_single_kind_inferred(tmp_path):
    base = tmp_path / 'comparison_y'
    base.mkdir()
    _write(base, spec='single', reference='k1_off',
           cells=[('k1_off', None, {'enabled': False}, 'round_robin')])
    assert read_run_layout(str(base))['kind'] == 'single'


def test_missing_descriptor_is_none(tmp_path):
    assert read_run_layout(str(tmp_path)) is None


def test_written_keys_are_exactly_schema_properties(tmp_path):
    base = tmp_path / 'c'
    base.mkdir()
    _write(base)
    written = set(read_run_layout(str(base)).keys())
    props = set(json.load(open(_SCHEMA))['properties'])
    assert written == props, (sorted(written), sorted(props))
