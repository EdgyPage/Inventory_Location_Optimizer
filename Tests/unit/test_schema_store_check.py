"""test_schema_store_check.py -- `python -m Schema.store_index --check` is a gate, and it can fail.

The DB-shape store had a Stop hook (`Schema/hook_check.py`: advisory, always exit 0) and no
blocking form, while its sibling store had `python -m Schema.profile_tree --check` all along.
On 2026-09-18 two DDL-defining sources (`Warehouse_Data.py`, `runtime_metrics.py`) were edited
and committed with every CLAUDE.md gate green; the stale `Schema/shapes/INDEX.json` was found by
an audit, not by a check. The gate now exists. This file is what notices if its exit code stops
meaning anything -- a gate that always passes is the hook again under a different name.

Run:  python -m pytest Tests/unit/test_schema_store_check.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys

from Schema import store_index

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))


def test_a_current_store_exits_zero(capsys):
    """The committed tree is current -- or `--sync` is owed, which is its own failure and the
    assertion message says so."""
    assert store_index.main(['--check']) == 0, store_index.stale_reasons()
    assert 'current' in capsys.readouterr().out


def test_a_moved_ddl_source_exits_one(monkeypatch, capsys):
    """NON-VACUITY, through the real `stale_reasons` path rather than a stub of it: the
    fingerprint disagrees with the index, the exit code is 1, and the remedy is printed."""
    monkeypatch.setattr(store_index, 'source_fingerprint', lambda: 'not-what-the-index-holds')
    assert store_index.main(['--check']) == 1
    out = capsys.readouterr().out
    assert '[schema-db]' in out and '--sync' in out, out


def test_a_missing_committed_document_exits_one(monkeypatch, capsys):
    """The second reason: the index points at a shape no committed document backs."""
    real = store_index.read_index()
    assert real and real.get('families'), 'the committed INDEX.json indexes no families'
    family = next(iter(real['families']))
    doctored = dict(real, families=dict(real['families'], **{family: 'deadbeefcafe'}))
    monkeypatch.setattr(store_index, 'read_index', lambda: doctored)
    assert store_index.main(['--check']) == 1
    assert family in capsys.readouterr().out


def test_the_module_form_is_the_gate():
    """CLAUDE.md section 1 runs it as `python -m Schema.store_index --check`; the `__main__`
    guard must exist and forward the exit code, or the gate line runs nothing and exits 0."""
    r = subprocess.run([sys.executable, '-m', 'Schema.store_index', '--check'],
                       capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'current' in r.stdout, r.stdout
