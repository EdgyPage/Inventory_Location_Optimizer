"""test_run_shaping_params.py — run-shaping params flow sim -> run_spec.json -> analysis.

Four defects found while preparing the 2026-08 dress rehearsal, each pinned here:

  1. `fill` and `checkpoint_frac` had NO CLI.  They were reachable only by editing
     `sim_config.py` — a file in the run-tree contract's SHAPE_SOURCES, so changing a VALUE
     tripped a schema preflight.  A 10-batch run silently checkpointed every batch.
  2. `_INITIAL_FILL` was an import-time scalar that `sim_assets` writes into warehouse.db as
     `target_fill`, so a runtime override left the run's own provenance recording the stale
     number — while the module's docstring promised CONFIG was authoritative.
  3. A STANDALONE re-analysis rebuilt the warehouse from this checkout's CONFIG, ignoring the
     run's caps entirely (the comment above the call claimed the opposite).  Every
     re-analysis of a capped run silently used a different warehouse than the sim.
  4. `--max-tasks-per-child` never reached the pool: `_run_whatif_matrix` did not forward it,
     and every run is a matrix.

Run:  python -m pytest Tests/unit/test_run_shaping_params.py -q
"""
from __future__ import annotations

import inspect
import json
import logging
import os

import pytest

from Optimization.config import sim_config
from Optimization.config.sim_config import CONFIG, _checkpoint_every, ff_fill, store_fill
from Optimization.simdriver import scenario


# ── 1 + 2: fill is read at CALL time, from CONFIG ────────────────────────────────

def test_fill_accessors_read_config_live():
    """The bug: an import-time snapshot could not see a runtime override."""
    orig_s = CONFIG['channels']['store']['fill']
    orig_f = CONFIG['channels']['fulfillment']['fill']
    try:
        CONFIG['channels']['store']['fill'] = 0.9
        CONFIG['channels']['fulfillment']['fill'] = 0.77
        assert store_fill() == 0.9
        assert ff_fill() == 0.77
    finally:
        CONFIG['channels']['store']['fill'] = orig_s
        CONFIG['channels']['fulfillment']['fill'] = orig_f
    assert store_fill() == orig_s


def test_the_import_time_snapshot_is_gone():
    """_INITIAL_FILL must not come back: sim_assets writes this value into warehouse.db as
    target_fill, so a stale snapshot makes a run misreport its own sizing."""
    assert not hasattr(sim_config, '_INITIAL_FILL')


def test_sim_assets_uses_the_accessor_for_the_provenance_write():
    import Optimization.simdriver.sim_assets as sa
    src = inspect.getsource(sa)
    assert '_INITIAL_FILL' not in src
    assert src.count('store_fill()') >= 2      # the sizing target AND the warehouse.db write


# ── 1: checkpoint cadence is a real knob ─────────────────────────────────────────

def test_checkpoint_every_follows_the_configured_fraction():
    orig = CONFIG['global']['checkpoint_frac']
    try:
        CONFIG['global']['checkpoint_frac'] = 0.1
        assert _checkpoint_every(10) == 1       # the old silent behaviour on a short run
        CONFIG['global']['checkpoint_frac'] = 0.5
        assert _checkpoint_every(10) == 5       # what --checkpoint-frac 0.5 buys
        assert _checkpoint_every(100) == 50
        CONFIG['global']['checkpoint_frac'] = 0.0
        assert _checkpoint_every(10) == 1       # never zero
    finally:
        CONFIG['global']['checkpoint_frac'] = orig


@pytest.mark.parametrize('flag', ['--store-fill', '--ff-fill', '--checkpoint-frac'])
def test_the_new_flags_exist_and_are_documented(flag):
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert f"'{flag}'" in src, f'{flag} is not registered on the parser'


def test_the_new_flags_are_recorded_in_run_spec_and_restored_on_resume():
    """A flag that only mutates CONFIG is defeated by defect 3 — the value must reach
    run_spec.json, and a resume must restore it."""
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    for key in ("'store_fill'", "'ff_fill'", "'checkpoint_frac'"):
        assert src.count(key) >= 2, f'{key} must be written to run_spec AND restored on resume'


# ── 3: the standalone re-analysis honours the run's own shape ────────────────────

def test_apply_run_shape_restores_every_recorded_param(tmp_path):
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {
        'n_batches': 10, 'max_skus': 10000, 'keyframe_interval': 5, 'checkpoint_frac': 0.5,
        's_max_bins': 50000, 's_min_bins': None, 's_max_aisles': None, 's_composition': None,
        'ff_max_bins': 50000, 'ff_min_bins': None, 'ff_max_aisles': None,
        'store_fill': 0.9, 'ff_fill': 0.9,
    })
    saved = {k: CONFIG['global'].get(k) for k in ('n_batches', 'max_skus', 'keyframe_interval',
                                                  'checkpoint_frac')}
    saved_fill = (CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'])
    saved_sizing = (dict(CONFIG['channels']['store']['sizing']),
                    dict(CONFIG['channels']['fulfillment']['sizing']))
    try:
        max_skus = _apply_run_shape(str(tmp_path), logging.getLogger('t'))
        assert max_skus == 10000, 'max_skus must reach build_shared_assets — the other half'
        assert CONFIG['global']['n_batches'] == 10
        assert CONFIG['global']['checkpoint_frac'] == 0.5
        assert CONFIG['channels']['store']['fill'] == 0.9
        assert CONFIG['channels']['fulfillment']['fill'] == 0.9
        assert CONFIG['channels']['store']['sizing']['max_bins'] == 50000
        assert CONFIG['channels']['fulfillment']['sizing']['max_bins'] == 50000
    finally:
        CONFIG['global'].update(saved)
        CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'] = saved_fill
        CONFIG['channels']['store']['sizing'].update(saved_sizing[0])
        CONFIG['channels']['fulfillment']['sizing'].update(saved_sizing[1])


def test_apply_run_shape_warns_loudly_when_the_run_predates_run_spec(tmp_path, caplog):
    from Optimization.run_analysis import _apply_run_shape
    log = logging.getLogger('t-nospec')
    with caplog.at_level(logging.WARNING, logger='t-nospec'):
        assert _apply_run_shape(str(tmp_path), log) is None
    assert any('no run_spec.json' in r.message for r in caplog.records), (
        'a pre-run_spec run must SAY it is sizing from this checkout, not do it quietly')


def test_run_analysis_threads_max_skus_into_the_rebuild():
    import Optimization.run_analysis as ra
    src = inspect.getsource(ra)
    assert '_apply_run_shape(base_dir, log)' in src
    assert 'max_skus=max_skus' in src, 'the cap must reach build_shared_assets'


# ── 4: the dead flag is plumbed ──────────────────────────────────────────────────

def test_max_tasks_per_child_reaches_the_matrix_driver():
    sig = inspect.signature(scenario._run_whatif_matrix)
    assert 'max_tasks_per_child' in sig.parameters
    body = inspect.getsource(scenario._run_whatif_matrix)
    assert 'max_tasks_per_child=max_tasks_per_child' in body, (
        'accepting the parameter without forwarding it is the bug, not the fix')
    import Optimization.run_simulation as rs
    assert 'max_tasks_per_child=args.max_tasks_per_child' in inspect.getsource(rs)


# ── the early structural-floor check ─────────────────────────────────────────────

def test_structural_bin_floor_is_configuration_not_catalogue():
    """The floor is the handling x category x tier cross-product — capping SKUs never
    lowers it, which is exactly why the warning has to come from configuration alone."""
    from Warehouse.inventory.inventory_planning import structural_bin_floor
    from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
    from Optimization.config.sim_config import _CATEGORIES, _HANDLINGS
    aisles, bins = structural_bin_floor(_HANDLINGS, _CATEGORIES,
                                        aisle_width_for(50), aisle_height_for(10))
    assert aisles == len(_HANDLINGS) * len(_CATEGORIES) * 5 == 60
    assert bins == 67_800
    # Half the categories -> half the floor.  Nothing about SKUs enters.
    _, half = structural_bin_floor(_HANDLINGS, _CATEGORIES[:3],
                                   aisle_width_for(50), aisle_height_for(10))
    assert half == bins // 2


def test_run_simulation_warns_before_any_build():
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert 'structural_bin_floor(' in src
    assert 'BELOW the store structural floor' in src
