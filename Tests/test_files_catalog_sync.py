"""test_files_catalog_sync.py

Locks the repo file catalog (context/files.yml) to the tree: every in-scope .py (product
source + Tests/, excluding __init__.py) is catalogued exactly once, every entry points at a
real file, layers are known, and key_symbols anchors exist.  Enforced by
context/arch/verify_architecture.py (the same guard that checks the call graph).

Also proves the CRITICAL invariant: a `--catalog-merge` resync PRESERVES the human-owned
purpose/notes byte-for-byte while adding new files and dropping deleted ones.

Run:  python -m pytest Tests/test_files_catalog_sync.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

pytest.importorskip('yaml', reason='catalog verification needs pyyaml (requirements-docs.txt)')
import yaml  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_extract():
    path = os.path.join(_ROOT, 'context', 'arch', 'extract.py')
    spec = importlib.util.spec_from_file_location('arch_extract', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_catalog_matches_tree():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, 'context', 'arch', 'verify_architecture.py'), '--quiet'],
        capture_output=True, text=True, cwd=_ROOT,
    )
    assert r.returncode == 0, 'architecture/catalog drift:\n' + r.stdout + r.stderr


def test_catalog_covers_every_in_scope_file():
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'files.yml'), encoding='utf-8') as fh:
        cat = yaml.safe_load(fh)
    disk = set(ex.discover_files(ex.CATALOG_ROOTS, include_init=False))
    keys = set(cat['files'])
    assert disk == keys, (
        f'catalog out of sync — uncatalogued: {sorted(disk - keys)[:5]} ; '
        f'stale: {sorted(keys - disk)[:5]}')
    assert len(keys) > 80, 'catalog implausibly small'


def test_merge_preserves_notes_and_prunes_deleted():
    """The note-preservation mechanic: merge may ADD/DROP entries and REFRESH layer/
    key_symbols, but must NEVER clobber an existing purpose/notes."""
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'files.yml'), encoding='utf-8') as fh:
        cat = yaml.safe_load(fh)
    victim = 'Optimization/strategy_runner.py'
    assert victim in cat['files']
    cat['files'][victim]['purpose'] = 'SENTINEL purpose — must survive resync'
    cat['files'][victim]['notes'] = 'SENTINEL notes — must survive resync'
    cat['files']['Optimization/__GHOST_DELETED__.py'] = {
        'purpose': 'ghost', 'layer': 'optimization', 'key_symbols': [], 'notes': 'n'}

    merged = ex.build_catalog(cat)['files']
    assert merged[victim]['purpose'] == 'SENTINEL purpose — must survive resync'
    assert merged[victim]['notes'] == 'SENTINEL notes — must survive resync'
    assert 'Optimization/__GHOST_DELETED__.py' not in merged, 'deleted file not pruned'
    # mechanical fields are still refreshed from the code
    assert merged[victim]['layer'] == 'optimization'


def test_merge_is_idempotent():
    """Re-running merge on the committed catalog is a byte-level no-op (deterministic)."""
    ex = _load_extract()
    with open(os.path.join(_ROOT, 'context', 'files.yml'), encoding='utf-8') as fh:
        committed = fh.read()
    rebuilt = ex.dump_catalog(ex.build_catalog(yaml.safe_load(committed)))
    assert rebuilt == committed, 'catalog stale — run: python context/arch/extract.py --catalog-merge'
