"""test_run_unification.py

Locks the "every run is a cell matrix" unification (Phase 1):

  - runlayout.cells() enumerates cell dirs under a run root (the unified
    <base>/<cell>/<pair>/<config>[/<channel>]/sim_*.db tree), skips _-prefixed dirs, and treats a
    legacy FLAT run (<base>/<pair>/<config>/sim_*.db, no cell level) as one implicit cell so old
    runs still analyze;
  - the spec registry maps 'single' -> exactly one cell `k1_off` (a plain run) and 'scheduler_ab'
    -> the round_robin/lpt A/B (cells `k1_off_rr` + `k1_off_lpt`).

Filesystem-only (empty sim_*.db files — the walkers key on the name, never open them); no sim.

Run:  python -m pytest Tests/test_run_unification.py -q
"""
from __future__ import annotations

import os

from Optimization.runlayout import cells
from Optimization.whatif_config import get_spec, SPECS
from Optimization.run_simulation import _build_cells


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').close()


# ── runlayout.cells() ─────────────────────────────────────────────────────────────────────

def test_cells_unified_multicell(tmp_path):
    base = tmp_path / 'comparison_whatif_x'
    for cell in ('k1_off_rr', 'k1_off_lpt'):        # cell/pair/config/channel/sim
        _touch(base / cell / 'pairA' / 'store' / 'store' / 'sim_uni_fifo_norsl.db')
    _touch(base / '_frozen' / 'pairA' / 'planned_inventory.db')     # _-dir: never a cell
    got = dict(cells(str(base)))
    assert set(got) == {'k1_off_rr', 'k1_off_lpt'}                  # both cells, _frozen skipped
    assert got['k1_off_rr'] == str(base / 'k1_off_rr')


def test_cells_single(tmp_path):
    base = tmp_path / 'comparison_y'
    _touch(base / 'k1_off' / 'pairA' / 'store' / 'store' / 'sim_uni_fifo_norsl.db')
    assert [n for n, _d in cells(str(base))] == ['k1_off']


def test_cells_legacy_flat_is_one_implicit_cell(tmp_path):
    base = tmp_path / 'comparison_flat'              # flat: base/pair/config/sim (no cell level)
    _touch(base / 'pairA' / 'store' / 'sim_uni_fifo_norsl.db')
    assert list(cells(str(base))) == [('comparison_flat', str(base))]


def test_cells_empty_dir(tmp_path):
    assert list(cells(str(tmp_path / 'nope'))) == []
    (tmp_path / 'empty').mkdir()
    assert list(cells(str(tmp_path / 'empty'))) == []


# ── spec registry → cells ─────────────────────────────────────────────────────────────────

def test_single_spec_is_one_k1_off_cell():
    assert 'single' in SPECS
    assert [c[0] for c in _build_cells(get_spec('single'))] == ['k1_off']


def test_scheduler_ab_spec_is_rr_vs_lpt():
    names = [c[0] for c in _build_cells(get_spec('scheduler_ab'))]
    assert names == ['k1_off_rr', 'k1_off_lpt']     # round_robin reference + lpt treatment


def test_get_spec_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        get_spec('does_not_exist')
