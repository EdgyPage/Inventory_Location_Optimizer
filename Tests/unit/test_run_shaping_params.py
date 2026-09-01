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
     and every run is a matrix — so every run in this repo's history recycled workers at 1.
     Forwarding it revealed WHY that was load-bearing: the first run to honour a larger value
     deadlocked the pool at a cell boundary.  Resolution is a PIN at 1, not a plumb-through.

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


@pytest.mark.parametrize('flag', ['--store-fill', '--ff-fill', '--checkpoint-frac',
                                  '--sampler'])
def test_the_new_flags_exist_and_are_documented(flag):
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert f"'{flag}'" in src, f'{flag} is not registered on the parser'


def test_the_new_flags_are_recorded_in_run_spec_and_restored_on_resume():
    """A flag that only mutates CONFIG is defeated by defect 3 — the value must reach
    run_spec.json, and a resume must restore it."""
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    for key in ("'store_fill'", "'ff_fill'", "'checkpoint_frac'", "'sampler'"):
        assert src.count(key) >= 2, f'{key} must be written to run_spec AND restored on resume'


# ── 3: the standalone re-analysis honours the run's own shape ────────────────────

def test_apply_run_shape_restores_every_recorded_param(tmp_path):
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {
        'n_batches': 10, 'max_skus': 10000, 'keyframe_interval': 5, 'checkpoint_frac': 0.5,
        's_max_bins': 50000, 's_min_bins': None, 's_max_aisles': None, 's_composition': None,
        'ff_max_bins': 50000, 'ff_min_bins': None, 'ff_max_aisles': None,
        'store_fill': 0.9, 'ff_fill': 0.9, 'sampler': 'v2',
    })
    saved = {k: CONFIG['global'].get(k) for k in ('n_batches', 'max_skus', 'keyframe_interval',
                                                  'checkpoint_frac', 'sampler')}
    saved_fill = (CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'])
    saved_sizing = (dict(CONFIG['channels']['store']['sizing']),
                    dict(CONFIG['channels']['fulfillment']['sizing']))
    try:
        max_skus = _apply_run_shape(str(tmp_path), logging.getLogger('t'))
        assert max_skus == 10000, 'max_skus must reach build_shared_assets — the other half'
        assert CONFIG['global']['n_batches'] == 10
        assert CONFIG['global']['checkpoint_frac'] == 0.5
        assert CONFIG['global']['sampler'] == 'v2'
        assert CONFIG['channels']['store']['fill'] == 0.9
        assert CONFIG['channels']['fulfillment']['fill'] == 0.9
        assert CONFIG['channels']['store']['sizing']['max_bins'] == 50000
        assert CONFIG['channels']['fulfillment']['sizing']['max_bins'] == 50000
    finally:
        CONFIG['global'].update(saved)
        CONFIG['channels']['store']['fill'], CONFIG['channels']['fulfillment']['fill'] = saved_fill
        CONFIG['channels']['store']['sizing'].update(saved_sizing[0])
        CONFIG['channels']['fulfillment']['sizing'].update(saved_sizing[1])


#: Recorded-but-not-run-shape: provenance, restored by nobody on purpose.  `inbound_lead_tag`
#: is the lead draw's domain constant — a comparison spanning a change to it is incomparable,
#: which is why it is written down, but replaying it onto CONFIG would mean nothing.
_PROVENANCE_KEYS = {'inbound_lead_tag'}


def _recorded_shape_keys() -> set:
    """Every `recv_*` / `put_*` / `inbound_*` key `main` writes into run_spec.json.

    Read off the AST because the record is a dict literal inside `main`, and because the
    inbound family arrives through a `**{...}` comprehension over `INBOUND_KEYS` rather than
    as literals — a text scan would see the first two families and miss the third entirely.
    """
    import ast
    import Optimization.run_simulation as rs
    from Optimization.config.sim_config import INBOUND_KEYS
    tree = ast.parse(inspect.getsource(rs))
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'main')
    call = next(n for n in ast.walk(main)
                if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == '_write_run_spec')
    rec = set(INBOUND_KEYS) if any(k is None for k in call.args[1].keys) else set()
    rec |= {k.value for k in call.args[1].keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return {k for k in rec
            if k.startswith(('recv_', 'put_', 'inbound_'))} - _PROVENANCE_KEYS


def _restored_shape_keys() -> set:
    """Every `CONFIG['global']` key `_apply_run_shape` writes back."""
    import ast
    import Optimization.run_analysis as ra
    from Optimization.config.sim_config import INBOUND_KEYS
    tree = ast.parse(inspect.getsource(ra))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_apply_run_shape')
    out = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Subscript) and getattr(node.value, 'id', '') == 'g'
                and isinstance(node.slice, ast.Constant)):
            out.add(node.slice.value)
        # `for key in ('a', 'b', ...)` and `for _k in INBOUND_KEYS`
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            out |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant)}
        if isinstance(node, ast.For) and getattr(node.iter, 'id', '') == 'INBOUND_KEYS':
            out |= set(INBOUND_KEYS)
    return out


def test_every_recorded_crew_queue_and_inbound_key_is_restored_on_re_analysis():
    """Seam 4 is TWO sites, and this is the half that has no observable symptom.

    A knob recorded but not restored costs nothing on a resume (that is `_apply_run_spec`, the
    other site) and nothing during the run. It shows up only in a STANDALONE re-analysis, which
    then rebuilds a warehouse — or reads a fee threshold, a lead shape, a queue split — that the
    run never had, and reports about a different configuration without a word.

    Caught by deleting a line while adding the inbound family: `put_swap_coef` lost its restore
    and all 1,534 tests stayed green. Three families are covered by prefix rather than by name,
    so a new knob in any of them is covered the day it is recorded.
    """
    missing = sorted(_recorded_shape_keys() - _restored_shape_keys())
    assert not missing, (
        f'{missing} are written to run_spec.json but never restored by _apply_run_shape; a '
        f'standalone re-analysis of such a run silently uses THIS checkout\'s value')


def test_the_recorded_key_scan_actually_finds_all_three_families():
    """A structural test that matched nothing would pass forever. Pin that each family is
    genuinely in the scan — the inbound one especially, since it arrives via `**{...}`."""
    rec = _recorded_shape_keys()
    assert {'recv_crew_size', 'put_swap_coef', 'inbound_standing_yard'} <= rec
    assert len(rec) > 25, f'only {len(rec)} keys scanned; the AST walk has stopped matching'
    assert 'inbound_lead_tag' not in rec, 'provenance is not run shape'


def test_apply_run_shape_pre_sampler_spec_means_v1(tmp_path):
    """A run_spec.json written before the sampler field existed belongs to a run whose
    batches were drawn with v1 — the re-analysis must restore 'v1', never this
    checkout's default (which flipped to 'v2' on 2026-08-20)."""
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {'n_batches': 10, 'max_skus': 1000})
    saved = CONFIG['global'].get('sampler')
    try:
        _apply_run_shape(str(tmp_path), logging.getLogger('t'))
        assert CONFIG['global']['sampler'] == 'v1'
    finally:
        CONFIG['global']['sampler'] = saved


def test_apply_run_shape_finds_run_spec_from_a_CELL_dir(tmp_path):
    """analyze_run calls run_analysis once per CELL, but run_spec.json lives at the RUN ROOT.

    Looking only in the dir it was handed found nothing and silently sized from the current
    checkout — caught in the rehearsal when a resumed run's analysis loaded 150,000 orders
    where its own sim had loaded 8,000."""
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    cell = tmp_path / 'k1_off_rr'
    cell.mkdir()
    _write_run_spec(str(tmp_path), {'max_skus': 8000, 'n_batches': 6})   # at the ROOT
    saved = CONFIG['global'].get('max_skus')
    try:
        assert _apply_run_shape(str(cell), logging.getLogger('t-cell')) == 8000
    finally:
        CONFIG['global']['max_skus'] = saved


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


# ── 4: worker recycling is PINNED at 1, and the flag says so ─────────────────────

def test_worker_recycling_is_pinned_at_one():
    """The flag was dead (never forwarded past _run_whatif_matrix, and every run is a matrix),
    so every run in this repo's history recycled at 1.  The first run that honoured a larger
    value DEADLOCKED at the cell boundary: cell 1 finished, then the pool sat at zero CPU with
    one live worker of eighteen and never shut down.  Pinned, not plumbed."""
    from Optimization.simdriver import supervisor
    body = inspect.getsource(supervisor._supervise)
    assert 'recycle = 1' in body, 'worker recycling must stay pinned at 1'
    assert 'max_tasks_per_child if max_tasks_per_child' not in body, (
        'the CLI value must not reach the pool — that is the deadlock path')


def test_the_flag_warns_instead_of_lying():
    """Still accepted (saved run_specs and older scripts pass it), but a value it will not
    honour must SAY so — silently ignoring it is what hid the deadlock for months."""
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert 'PINNED AT 1' in src
    assert 'ignored: worker' in src, 'an unhonoured value must warn'


def test_the_pool_is_constructed_with_the_pinned_value():
    from Optimization.simdriver import supervisor
    body = inspect.getsource(supervisor._run_pool)
    assert 'max_tasks_per_child=recycle' in body    # recycle is the pinned 1


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


# ── 5. the working day reaches the worker, the spec, and a resume ─────────────────
#
# A FIFTH defect of the same family as #4 above, caught while adding the working day.
# `settings.py` names four places a new setting must reach — declared, in CONFIG, a CLI flag,
# recorded in the run spec, restored on resume and re-analysis. There is a fifth it does not
# name: `workunits._shared`, the picklable payload a spawned worker actually receives. A
# worker re-imports sim_config and gets pristine defaults, so a setting missing from the
# payload is accepted on the command line and silently ignored in the run.

_DAY_KEYS = ('work_day_seconds', 'releases_per_day', 'cut_at_day_end',
             'roll_over_unpicked')


def test_the_working_day_is_in_config():
    from Optimization.config.sim_config import CONFIG
    for k in _DAY_KEYS:
        assert k in CONFIG['global'], k
    # Defaults reproduce the pre-working-day runner, which is what lets it ship unreleased.
    assert CONFIG['global']['releases_per_day'] is None
    assert CONFIG['global']['cut_at_day_end'] is False
    # The largest behaviour change in the family, and therefore the one most obviously
    # off by default: it ends comparability with the whole archive.
    assert CONFIG['global']['roll_over_unpicked'] is False


def test_the_working_day_has_cli_flags():
    import inspect

    from Optimization import run_simulation
    src = inspect.getsource(run_simulation)
    for flag in ('--work-day-seconds', '--releases-per-day', '--cut-at-day-end',
                 '--roll-over-unpicked'):
        assert flag in src, f'{flag} has no CLI, so it is reachable only by editing config'


def test_the_working_day_reaches_the_worker_payload():
    """THE fifth seam. Without this the flags parse, the spec records them, and the run
    ignores them — which is defect 4 above, repeating."""
    import inspect

    from Optimization.config.sim_config import CONFIG, work_day_spec
    from Optimization.simdriver import workunits

    src = inspect.getsource(workunits)
    assert 'work_day            = work_day_spec()' in src, (
        'the worker payload does not carry the working day')

    before = {k: CONFIG['global'][k] for k in _DAY_KEYS}
    try:
        CONFIG['global'].update(work_day_seconds=3600.0, releases_per_day=4,
                                cut_at_day_end=True, roll_over_unpicked=True)
        assert work_day_spec() == {'seconds': 3600.0, 'releases_per_day': 4,
                                   'cut_at_day_end': True, 'roll_over_unpicked': True,
                                   'drain_or_cap': False}
    finally:
        CONFIG['global'].update(before)
    # Read at CALL time, not import time — the whole reason this is an accessor.
    assert work_day_spec()['releases_per_day'] is None


def test_the_day_length_falls_back_to_the_shift_length():
    """A run that asks for a cut without naming a day gets the eight hours it already
    reports against, rather than a second length nobody set."""
    from Optimization.config.sim_config import CONFIG, shift_seconds, work_day_spec
    before = CONFIG['global']['work_day_seconds']
    try:
        CONFIG['global']['work_day_seconds'] = None
        assert work_day_spec()['seconds'] == shift_seconds()
    finally:
        CONFIG['global']['work_day_seconds'] = before


def test_the_working_day_is_recorded_and_restored():
    """Two runs with different days are otherwise indistinguishable after the fact, and a
    resume would finish an arm on a different clock than it started on."""
    import inspect

    from Optimization import run_analysis, run_simulation
    sim_src = inspect.getsource(run_simulation)
    for k in _DAY_KEYS:
        assert f"'{k}'" in sim_src, f'{k} is never written to the run spec'
    # Restored on resume...
    assert "'work_day_seconds', 'releases_per_day', 'cut_at_day_end'," in sim_src, (
        'a resume does not restore the working day')
    # ...and on a standalone re-analysis.
    ana_src = inspect.getsource(run_analysis)
    for k in _DAY_KEYS:
        assert f"spec.get('{k}')" in ana_src, (
            f're-analysis does not restore {k} from the run spec')


# ── the day survives a resume because it never lives anywhere a resume can lose it ──

def test_the_runner_reads_the_day_only_from_its_arguments():
    """The worker body must take the working day from `args` and from nothing else.

    This is what makes a resumed arm finish on the clock it started on. A worker is SPAWNED,
    not forked: it re-imports `sim_config` and gets pristine module defaults, so any day
    value read from the module rather than the payload would silently revert — the arm would
    resume onto an eight-hour default while its run spec recorded something else, and the
    two halves of the run would disagree with nothing raising.

    Scanned on the AST with docstrings stripped, because the function's own prose names
    `work_day_spec` while explaining why it does not call it — and a plain text search
    matched that sentence.
    """
    import ast

    from Optimization.simdriver import strategy_runner

    tree = ast.parse(inspect.getsource(strategy_runner))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_run_strategy_worker_impl')
    for node in ast.walk(fn):                      # strip every docstring and comment
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    body = ast.unparse(fn)

    for name in ('work_day_spec', 'CONFIG', 'settings.WORK_DAY_SECONDS'):
        assert name not in body, (
            f'run_strategy reads {name} directly; a spawned worker would get the module '
            f'default instead of the run\'s day')
    assert "args.get('work_day')" in body, 'the day no longer comes from the payload'


def test_batch_level_resume_is_refused_while_demand_rolls_over():
    """The one resume path the working day genuinely breaks.

    Strategy granularity — the default — replays a partial arm from batch 0, so every piece
    of day-clock state (the clock, the pending demand, the held items, the queue depths, the
    arrival stamp counter) is rebuilt from nothing and there is nothing to carry. Batch
    granularity is different in kind once rollover is on: `_pending` lives in the worker's
    locals and is in no checkpoint, so resuming at batch N DROPS every unit the pre-crash run
    carried and the resumed stream never asks for them again.

    Demand that disappears is not the "not bit-identical" the existing warning describes — a
    resumed run would report throughput it did not earn. The behaviour is asserted in
    Tests/integration/test_crash_recovery.py; this pins that the refusal exists at all, next
    to the seams it belongs with.
    """
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits._plan_strategy_start)
    assert 'roll_over' in src, 'the planner cannot see whether demand rolls over'
    assert 'raise RuntimeError' in src, (
        'batch-level resume only WARNS while the carry is on, so a resumed run silently '
        'loses the demand the crash left behind')
