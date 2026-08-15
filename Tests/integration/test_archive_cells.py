"""test_archive_cells.py — the count invariant that decides whether a cell can be archived.

`scripts/archive_cells.py` copies a cell to the cold drive, verifies the copy, then swaps the
original for a junction pointing at it.  Two guards stand between the copy and that swap, and both
compare a FILE COUNT: one before the junction, one after it resolves.  A junction swap is the one
irreversible step in the archiver, so those guards are the reason it is safe — but a guard that
counts the wrong set is worse than none, because it fails closed on healthy input and the operator
learns to override it.

That is exactly what happened.  `_sweep_sidecars` deliberately deletes `-wal`/`-shm` from the copy
(a stray sidecar makes the archived copy differ from the original, and SQLite would later try to
recover from a WAL describing nothing).  Both guards then compared that swept copy against a source
count taken BEFORE the sweep, so every cell came up short by exactly the number of sidecars it
carried — hundreds, since a read-only open creates them and cannot remove them.  Real failure:

    copy has 4343 files, source had 4621 - refusing to swap in a junction

4621 - 4343 = 278 = that cell's sidecars.  Any cell anyone had ever opened could never be archived.

These tests pin the invariant rather than the arithmetic: whatever `_sweep_sidecars` removes must be
exactly what a sidecar-excluding count already ignores, so the two agree by construction.

    python -m pytest Tests/integration/test_archive_cells.py -q
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'scripts'))          # the archiver is a script, not a package
import archive_cells as ac                             # noqa: E402


# ── fixture ───────────────────────────────────────────────────────────────────

def _make_cell(root, *, dbs: int = 4, sidecars_per_db: int = 2, extras: int = 3):
    """A miniature cell tree: some .db files, their sidecars, and unrelated files.

    Sidecars live beside their DB and in a nested dir, because the sweep and the counts both walk
    recursively and a bug that only handles the top level would otherwise pass.
    """
    nested = os.path.join(root, 'sub')
    os.makedirs(nested, exist_ok=True)
    kept = 0
    for i in range(dbs):
        d = root if i % 2 == 0 else nested
        with open(os.path.join(d, f'sim_{i}.db'), 'wb') as fh:
            fh.write(b'SQLite format 3\x00' + bytes(64))
        kept += 1
        for suffix in ('-wal', '-shm')[:sidecars_per_db]:
            with open(os.path.join(d, f'sim_{i}.db{suffix}'), 'wb') as fh:
                fh.write(bytes(32))
    for i in range(extras):
        with open(os.path.join(root, f'report_{i}.json'), 'w', encoding='utf-8') as fh:
            fh.write('{}')
        kept += 1
    return kept


# ── the invariant ─────────────────────────────────────────────────────────────

def test_sweeping_the_copy_cannot_change_a_sidecar_excluding_count(tmp_path):
    """The guard's premise: what the sweep removes is what the count already ignores.

    This is the whole fix in one assertion.  If these two ever disagree, the post-sweep copy and
    the pre-sweep source stop being comparable and the archiver refuses healthy cells again.
    """
    src = tmp_path / 'src'
    src.mkdir()
    kept = _make_cell(str(src))

    dst = tmp_path / 'dst'
    shutil.copytree(src, dst)

    n_before, _ = ac._tree_stats(str(dst), skip_sidecars=True)
    removed = ac._sweep_sidecars(str(dst))
    n_after, _ = ac._tree_stats(str(dst), skip_sidecars=True)

    assert removed > 0, 'fixture built no sidecars — the test would pass vacuously'
    assert n_before == n_after == kept, (
        f'sweeping changed a sidecar-excluding count: {n_before} -> {n_after} (expected {kept})')

    # And the counts that DO include sidecars must genuinely differ, or the bug this pins could
    # not have existed and the test proves nothing.
    n_src_raw, _ = ac._tree_stats(str(src))
    n_dst_raw, _ = ac._tree_stats(str(dst))
    assert n_src_raw - n_dst_raw == removed, (
        f'raw counts differ by {n_src_raw - n_dst_raw}, swept {removed} — fixture is inconsistent')
    assert n_dst_raw != n_src_raw, 'raw counts agree, so the original bug was unreachable here'


def test_raw_counts_are_what_used_to_reject_every_swept_cell(tmp_path):
    """The regression itself, stated as the comparison the archiver used to make.

    Kept separate and explicit: it is the one line of the old code, so a future refactor that
    reintroduces a raw comparison fails here with a message naming why.
    """
    src = tmp_path / 'src'
    src.mkdir()
    _make_cell(str(src))
    dst = tmp_path / 'dst'
    shutil.copytree(src, dst)

    n_source_precopy, _ = ac._tree_stats(str(src))          # taken BEFORE the sweep, as before
    swept = ac._sweep_sidecars(str(dst))
    n_dst_raw, _ = ac._tree_stats(str(dst))

    assert n_dst_raw == n_source_precopy - swept, (
        'the old guard compared these two directly, so it was short by exactly the sidecar count '
        f'({swept}) on every cell that had any')

    n_dst, _ = ac._tree_stats(str(dst), skip_sidecars=True)
    n_kept, _ = ac._tree_stats(str(src), skip_sidecars=True)
    assert n_dst == n_kept, (
        f'the corrected guard must agree: copy {n_dst} vs source {n_kept}')


def test_sidecar_suffixes_are_shared_by_the_sweep_and_the_count(tmp_path):
    """One suffix list, or the two halves drift apart silently.

    The sweep and the count are ~330 lines apart.  If someone adds a third transient suffix to one
    and not the other, the archiver goes straight back to rejecting healthy cells — with no error
    at the point of the edit.
    """
    assert ac._SIDECAR_SUFFIXES, 'no suffixes declared'

    src = tmp_path / 'src'
    src.mkdir()
    with open(src / 'x.db', 'wb') as fh:
        fh.write(b'SQLite format 3\x00')
    for suffix in ac._SIDECAR_SUFFIXES:
        with open(src / f'x.db{suffix}', 'wb') as fh:
            fh.write(b'\x00')

    n_excl, _ = ac._tree_stats(str(src), skip_sidecars=True)
    assert n_excl == 1, f'the count ignores a suffix the sweep removes: kept {n_excl} of 1'

    removed = ac._sweep_sidecars(str(src))
    assert removed == len(ac._SIDECAR_SUFFIXES), (
        f'the sweep removed {removed} of {len(ac._SIDECAR_SUFFIXES)} declared suffixes')


@pytest.mark.parametrize('skip', [False, True])
def test_tree_stats_size_tracks_the_files_it_counted(tmp_path, skip):
    """Both return values must describe the same set — the caller uses them interchangeably."""
    src = tmp_path / 'src'
    src.mkdir()
    _make_cell(str(src))

    n, total = ac._tree_stats(str(src), skip_sidecars=skip)
    expected = 0
    for dirpath, _d, files in os.walk(str(src)):
        for fn in files:
            if skip and fn.endswith(ac._SIDECAR_SUFFIXES):
                continue
            expected += os.path.getsize(os.path.join(dirpath, fn))
    assert total == expected, f'skip={skip}: size {total} does not match the {n} files counted'
