"""test_memory_sync.py

Locks the git-tracked memory mirror (context/memory/store/) — the copy that lets this project's
session memories survive the repo moving, which orphans the live store because its directory is
named after the repo's absolute path.

Locked-in invariants:
  1. `verify_memory --repo-only` exits 0: index/shape/links/paths/anchors all hold.
  2. Stale path ANCHORS are zero. This is the check that rots on its own — a refactor that moves
     files silently invalidates every memory citing them, and nothing else in the repo notices.
     The 2026-07 restructure staled 8 anchors across 4 of 8 memories before this existed.
  3. The mirror is genuinely TRACKED. A mirror under .claude/ would be silently untracked by
     `.gitignore`'s `.claude/*`, which is precisely the failure the mirror exists to prevent.

These assert on the MIRROR only, never on the live store: the live store does not exist in another
clone or in CI, so a parity assertion would fail on every machine but the one that wrote it.
Live-vs-mirror parity is the hooks' job, not a test's.

Run:  python -m pytest Tests/architecture/test_memory_sync.py -q
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STORE = os.path.join(_ROOT, 'context', 'memory', 'store')


def _verifier():
    path = os.path.join(_ROOT, 'context', 'memory', 'verify_memory.py')
    spec = importlib.util.spec_from_file_location('verify_memory_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.skipif(
    not os.path.isdir(_STORE),
    reason='memory mirror absent (context/memory/store/) — nothing to verify in this clone')


def test_repo_only_verification_passes():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, 'context', 'memory', 'verify_memory.py'),
         '--repo-only'],
        capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, 'memory layer drift:\n' + r.stdout + r.stderr


def test_index_and_files_are_one_to_one():
    """MEMORY.md is the index Claude Code loads; a memory missing from it is invisible."""
    names = {f for f in os.listdir(_STORE) if f.endswith('.md')} - {'MEMORY.md'}
    assert names, 'the mirror holds no memories — sync.py --push has never run'
    with open(os.path.join(_STORE, 'MEMORY.md'), encoding='utf-8') as fh:
        index = fh.read()
    import re
    pointed = set(re.findall(r'\]\(([^)]+\.md)\)', index))
    assert pointed == names, (
        f'index/files mismatch — unindexed: {sorted(names - pointed)}; '
        f'dangling pointers: {sorted(pointed - names)}')


def test_no_stale_path_anchors():
    """The check nothing else in the repo performs: do the paths a memory cites still exist?"""
    vm = _verifier()
    stale = []
    for name in sorted(os.listdir(_STORE)):
        if not name.endswith('.md') or name == 'MEMORY.md':
            continue
        with open(os.path.join(_STORE, name), encoding='utf-8') as fh:
            body = fh.read()
        for cited in vm._cited_paths(body):
            rel = cited.split('::')[0]
            if not os.path.exists(os.path.join(_ROOT, rel)):
                stale.append(f'{name} -> {cited}')
    assert not stale, (
        f'{len(stale)} memory path anchor(s) no longer exist: {stale[:6]}. '
        'Run the memory-maintainer agent; verify_memory prints a candidate for each.')


def test_anchor_extraction_is_not_vacuous():
    """If _cited_paths returned nothing, test_no_stale_path_anchors would pass trivially."""
    vm = _verifier()
    found = sum(len(vm._cited_paths(open(os.path.join(_STORE, n), encoding='utf-8').read()))
                for n in os.listdir(_STORE) if n.endswith('.md') and n != 'MEMORY.md')
    assert found >= 5, (
        f'only {found} path anchors extracted across the whole store — _cited_paths is probably '
        'over-filtering, which would make the staleness check vacuous.')


def test_mirror_is_actually_tracked_by_git():
    """A mirror under .claude/ would be silently ignored — the exact failure this prevents."""
    r = subprocess.run(['git', 'ls-files', '--error-unmatch',
                        'context/memory/store/MEMORY.md'],
                       capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, (
        'context/memory/store/MEMORY.md is NOT tracked by git, so the mirror provides no '
        'durability at all. Check .gitignore.')
