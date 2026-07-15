"""test_run_spec_roundtrip.py

Locks run_spec.json — the persisted, fully-resolved run invocation that enables zero-param
`--resume` (no drift from re-typed flags).  Covers:
  - _write_run_spec / _load_run_spec atomic round-trip (and missing-file -> None);
  - _apply_run_spec overlays the saved spec onto args, but an EXPLICITLY-typed flag on the
    resume command overrides it (with a note).

Run:  python -m pytest Tests/test_run_spec_roundtrip.py -q
"""
from __future__ import annotations

import argparse
import os

from Optimization.sim_manifest import _write_run_spec, _load_run_spec, _run_spec_path
from Optimization import run_simulation as rs


def test_write_load_roundtrip(tmp_path):
    spec = {'argv': ['run_simulation.py', '--n-batches', '50'],
            'n_batches': 50, 'max_skus': 300, 'whatif': False,
            'pairs': [['profileX', 'inv.db', 'aff.db']], 'resume_granularity': 'strategy'}
    _write_run_spec(str(tmp_path), spec)
    assert os.path.exists(_run_spec_path(str(tmp_path)))
    assert _load_run_spec(str(tmp_path)) == spec                 # exact round-trip
    # atomic: no leftover temp file
    assert not any(n.startswith('run_spec.json.tmp') for n in os.listdir(tmp_path))
    # missing -> None (bare resume of a pre-recovery run)
    assert _load_run_spec(str(tmp_path / 'nope')) is None


def test_apply_overlays_saved_spec():
    args = argparse.Namespace(n_batches=None, max_skus=None, workers=1,
                              resume_granularity='strategy', max_retries=2)
    spec = {'n_batches': 50, 'max_skus': 300, 'workers': 4, 'resume_granularity': 'batch'}
    store_comp, notes = rs._apply_run_spec(args, spec, explicit=set())
    assert args.n_batches == 50 and args.max_skus == 300 and args.workers == 4
    assert args.resume_granularity == 'batch'          # saved value applied
    assert store_comp is None and notes == []


def test_explicit_flag_overrides_spec_with_note():
    args = argparse.Namespace(n_batches=20, workers=1)          # user re-typed --n-batches 20
    spec = {'n_batches': 50, 'workers': 4}
    _sc, notes = rs._apply_run_spec(args, spec, explicit={'n_batches'})
    assert args.n_batches == 20                                 # explicit wins
    assert args.workers == 4                                    # not explicit -> from spec
    assert any('override' in n and 'n_batches' in n for n in notes)


def test_apply_injects_resolved_store_composition():
    args = argparse.Namespace()
    comp = {'store/pallet/large/pallet': 1.0}
    store_comp, _notes = rs._apply_run_spec(args, {'s_composition': comp}, explicit=set())
    assert store_comp == comp                                   # resolved dict, not a file path
