"""test_batch_sibling_cache.py -- a cell reuses a sibling cell's batch file before computing.

On a frozen matrix every cell samples the identical script, and until 2026-09-19 every cell
recomputed it (~2 min a cell at campaign scale) because the cache path is per cell.  Under
the flat work pool that recompute runs while the pool is busy, so `ensure_batches` now
copies a same-named file from a sibling cell's pair dir first (`_sibling_batches`).  The
name IS the fingerprint and the worker re-verifies the full fingerprint on load, so a copy
is the same bytes or is rejected there.

Pinned here: the copy lands under the asking cell (the tree contract is unchanged -- the
artifact still exists per cell), reserved `_`-prefixed siblings and the cell itself are
never consulted, a miss is None, and `ensure_batches` consults the sibling BEFORE it
computes (by source, because computing needs a real catalogue and a spawn pool).

Run:  python -m pytest Tests/unit/test_batch_sibling_cache.py -q
"""
from __future__ import annotations

import inspect
import os

from Optimization.simdriver import batch_precompute as bp

_NAME = '_batches_0123456789abcdef.pkl'


def _tree(tmp_path, *cells_with_file):
    root = tmp_path / 'run'
    for cell in ('k1_off_a', 'k1_off_b', '_frozen'):
        (root / cell / 'pair').mkdir(parents=True)
    for cell in cells_with_file:
        (root / cell / 'pair' / _NAME).write_bytes(b'batches:' + cell.encode())
    return root


def test_a_sibling_cells_file_is_copied_under_the_asking_cell(tmp_path):
    root = _tree(tmp_path, 'k1_off_a')
    got = bp._sibling_batches(str(root / 'k1_off_b' / 'pair'), _NAME)
    assert got == os.path.join(str(root / 'k1_off_b' / 'pair'), _NAME)
    assert (root / 'k1_off_b' / 'pair' / _NAME).read_bytes() == b'batches:k1_off_a'
    assert (root / 'k1_off_a' / 'pair' / _NAME).exists(), 'the source must not move'
    assert not any(p.name.endswith('.tmp') or '.tmp.' in p.name
                   for p in (root / 'k1_off_b' / 'pair').iterdir()), 'a temp file was left'


def test_a_reserved_sibling_and_the_cell_itself_are_never_consulted(tmp_path):
    root = _tree(tmp_path, '_frozen')
    assert bp._sibling_batches(str(root / 'k1_off_b' / 'pair'), _NAME) is None
    # the asking cell's own dir is the `path` the caller already checked; not a sibling
    (root / 'k1_off_b' / 'pair' / _NAME).write_bytes(b'mine')
    assert bp._sibling_batches(str(root / 'k1_off_a' / 'pair'), _NAME) is not None
    assert (root / 'k1_off_a' / 'pair' / _NAME).read_bytes() == b'mine'


def test_no_sibling_is_a_miss_not_an_error(tmp_path):
    root = _tree(tmp_path)
    assert bp._sibling_batches(str(root / 'k1_off_b' / 'pair'), _NAME) is None
    # a pair dir with no cell level above it (a scratch tree): also a miss
    lone = tmp_path / 'pair'
    lone.mkdir()
    assert bp._sibling_batches(str(lone), _NAME) is None


def test_ensure_batches_asks_the_sibling_before_it_computes():
    src = inspect.getsource(bp.ensure_batches)
    assert src.index('_sibling_batches(') < src.index('precompute_batches('), (
        'ensure_batches must consult a sibling cell before it opens a precompute pool')
