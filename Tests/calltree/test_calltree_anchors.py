"""
test_calltree_anchors.py — drift gate: the framework's anchors into product code hold.

A failure here means the PRODUCT moved under the framework — a hot-path symbol was
renamed/relocated, the t_* section vocabulary changed shape, or the production engine
was swapped — and names exactly which SECTION_MAP entry or scenario assumption to update.
(The programmatic half of the drift plan; the code-reviewer/test-developer agent notes
are the reminder half.)

Run: python -m pytest Tests/calltree/test_calltree_anchors.py -q
"""
from __future__ import annotations

import importlib
import inspect
import os
import re

import calltree_scenarios as scenarios
import calltree_tracer as ct

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Where each SECTION_MAP qualname lives. Kept HERE (not in the tracer) so the tracer
# never imports product modules; this test resolves every entry against the real code.
_SECTION_MAP_HOME = {
    'Capacity_Reloader.reload'        : 'Warehouse.placement.Capacity_Reloader',
    'ReorderMixin.check_reorders'     : 'Warehouse.inventory.inventory_reorder',
    'Batch.__init__'                  : 'Warehouse.picking.Workload_Builder',
    'Task.from_batch'                 : 'Warehouse.picking.Workload_Builder',
    'fused_pre_snapshot'              : 'Optimization.metrics.Simulation_Analytics',
    'snapshot_aisle_metrics'          : 'Optimization.metrics.Simulation_Analytics',
    'save_bin_keyframe'               : 'Optimization.persistence.Picking_Data',
    'DeferredPickSimulation.run'      : 'Warehouse.picking.fast_pick',
    'extract_batch_stats'             : 'Optimization.metrics.Simulation_Analytics',
    'extract_task_stats'              : 'Optimization.metrics.Simulation_Analytics',
    'extract_picker_events'           : 'Optimization.metrics.Simulation_Analytics',
    'extract_picks'                   : 'Optimization.metrics.Simulation_Analytics',
    'save_batch_stats'                : 'Optimization.persistence.Picking_Data',
    'save_task_stats'                 : 'Optimization.persistence.Picking_Data',
    'save_picker_events'              : 'Optimization.persistence.Picking_Data',
    'save_picks'                      : 'Optimization.persistence.Picking_Data',
    'save_bin_placements'             : 'Optimization.persistence.Picking_Data',
    'save_bin_evictions'              : 'Optimization.persistence.Picking_Data',
    'save_aisle_metrics'              : 'Optimization.persistence.Picking_Data',
    'save_reorder_queue'              : 'Optimization.persistence.Picking_Data',
    'save_worker_checkpoint'          : 'Optimization.simdriver.strategy_runner',
}


def test_section_map_symbols_resolve():
    # NON-VACUITY: the two maps must cover each other exactly, and be non-trivial.
    assert set(_SECTION_MAP_HOME) == set(ct.SECTION_MAP), \
        'SECTION_MAP and its home table diverged — update both together'
    assert len(ct.SECTION_MAP) >= 15

    for qualname, module_name in _SECTION_MAP_HOME.items():
        mod = importlib.import_module(module_name)
        obj = mod
        for part in qualname.split('.'):
            assert hasattr(obj, part), \
                f'SECTION_MAP anchor {qualname!r} broke: {module_name} has no {part!r}'
            obj = getattr(obj, part)
        assert callable(obj), f'{qualname} resolved to a non-callable'
        fn = inspect.unwrap(obj)
        real = getattr(fn, '__qualname__', qualname)
        assert real == qualname, \
            f'qualname drift: SECTION_MAP says {qualname!r}, code says {real!r}'


def test_section_vocabulary_matches_strategy_runner():
    src_path = os.path.join(_REPO_ROOT, 'Optimization', 'simdriver', 'strategy_runner.py')
    with open(src_path, encoding='utf-8') as fh:
        src = fh.read()

    # Every t_* accumulator the worker keeps maps into our SECTIONS vocabulary
    # (t_build is the derived sum of t_sample + t_task and deliberately not a section).
    accs = set(re.findall(r't_(reord|build|sample|task|pre|sim|extract|inv)_ckpt', src))
    assert accs, 'no section accumulators found in strategy_runner — regex or code moved'
    expected = {name[2:] for name in ct.SECTIONS} | {'build'}
    assert accs <= expected, f'strategy_runner grew unknown sections: {accs - expected}'
    missing = (expected - {'build', 'save'}) - accs
    assert not missing, f'strategy_runner lost sections the framework still maps: {missing}'

    # The checkpoint log line still carries the tokens bench_sections/macro parse.
    # (t_inv logs as 'cons=' — the conservation ledger; bench_sections accepts both
    # spellings since the rename.  kf=/gc= are the 2026-08-19 overlay tokens.)
    for token in ('reord=', 'smpl=', 'task=', 'pre=', 'sim=', 'extr=', 'cons=', 'db=',
                  'kf=', 'gc='):
        assert token in src, f'checkpoint log line lost {token!r} — macro adapter breaks'

    # And bench_sections' regexes must actually match the emitted shape end-to-end —
    # _SEC_RE silently rotted once when inv= became cons= (parse() returned zero rows).
    # The sample carries the appended overlay suffix to PROVE the append is non-breaking.
    import bench_sections as bsec
    sample = ('  Batch   10/100  | reord=1.0s build=2.0s (smpl=0.5s task=1.5s) '
              'pre=3.0s sim=4.0s extr=5.0s cons=6.0s kf=0.4s gc=0.12s')
    assert bsec._SEC_RE.search(sample), \
        "bench_sections._SEC_RE no longer parses strategy_runner's checkpoint line"
    assert bsec._KF_RE.search(sample) and bsec._GC_RE.search(sample), \
        'bench_sections overlay regexes (kf=/gc=) no longer match the emitted tokens'


def test_production_engine_identity():
    # The engine the framework traces must be the one strategy_runner actually runs.
    src_path = os.path.join(_REPO_ROOT, 'Optimization', 'simdriver', 'strategy_runner.py')
    with open(src_path, encoding='utf-8') as fh:
        src = fh.read()
    assert 'DeferredPickSimulation(' in src, \
        'strategy_runner no longer instantiates DeferredPickSimulation — retarget scenarios'

    from Warehouse.picking.fast_pick import DeferredPickSimulation
    assert scenarios.DeferredPickSimulation is DeferredPickSimulation


def test_scenario_reorders_actually_fire():
    # The cheap functional probe: a fresh minimal scenario must place reorder units.
    assets = scenarios.build_assets(n_skus=150, bins_per_aisle=40, n_pickers=3, seed=11,
                                    coverage=2.0, safety=0.4)   # fast depletion, prod-shaped rp/eq
    r = scenarios.run_meso(assets, n_batches=2, seed=11)
    assert r.placements > 0, \
        'scenario builder stopped firing reorders — every placement measurement is dead'


def test_default_strategy_still_registered():
    from Optimization.config.strategies import STRATEGY_BY_KEY
    assert scenarios.DEFAULT_STRATEGY in STRATEGY_BY_KEY, \
        f'{scenarios.DEFAULT_STRATEGY!r} left the registry — pick a new default arm'


# ── flow anchors ──────────────────────────────────────────────────────────────────

# Where each `_FLOW_COUNTS` tree-name lives. Same split as `_SECTION_MAP_HOME` and for the
# same reason: `calltree_growth` must never import product modules, so the resolution table
# lives here and this test resolves it against the real code.
#
# This gate matters MORE than SECTION_MAP's. A broken section anchor makes a section vanish
# from the report, which is visible. A broken flow anchor makes the flow report `0` — and `0`
# is exactly the reading ("the path never ran") that flows were added to prevent.
_FLOW_HOME = {
    'Assignment_Functions': 'Warehouse.placement.Assignment_Functions',
    'Inventory_Management': 'Warehouse.inventory.Inventory_Management',
    'put_queue'           : 'Warehouse.inventory.put_queue',
    'dock'                : 'Warehouse.inventory.dock',
}


def test_flow_anchors_resolve():
    """Every `_FLOW_COUNTS` name must still name a real callable."""
    import calltree_growth as cg

    assert len(cg._FLOW_COUNTS) >= 5, 'the flow table shrank; was it gutted rather than fixed?'

    for key, (name, parent) in cg._FLOW_COUNTS.items():
        for tree_name in (name, parent):
            if tree_name is None:
                continue
            mod_base, _, qualname = tree_name.partition(':')
            assert mod_base in _FLOW_HOME, (
                f'flow {key!r} names module {mod_base!r}, which is not in _FLOW_HOME — '
                f'update both together')
            mod = importlib.import_module(_FLOW_HOME[mod_base])
            obj = mod
            for part in qualname.split('.'):
                if part == '<locals>':
                    # A closure. Everything after this segment is created at CALL time and
                    # cannot be reached by an attribute walk, so resolving the enclosing
                    # function is the most this gate can do -- and that is still the name a
                    # refactor would change.
                    break
                assert hasattr(obj, part), (
                    f'flow anchor {tree_name!r} broke: {_FLOW_HOME[mod_base]} has no '
                    f'{part!r}. The flow would silently report 0, which reads as "the path '
                    f'never ran" — the exact misreading flows exist to prevent.')
                obj = getattr(obj, part)
            assert callable(obj), f'{tree_name} resolved to a non-callable'


def test_every_named_config_is_buildable():
    """A config whose overlay names a key `build_assets` does not take is accepted by argparse
    and then dies mid-ladder, minutes in. `calltree_growth` validates at import; this pins the
    other half — that the keys it allows are the ones the builder actually reads."""
    import inspect

    import calltree_growth as cg
    import calltree_scenarios as cs

    accepted = set(inspect.signature(cs.build_assets).parameters)
    accepted |= set(inspect.signature(cs.run_meso).parameters)
    accepted |= {'n_batches'}          # consumed by the ladder itself, not by build_assets
    for name, cfg in cg.CONFIGS.items():
        unknown = set(cfg.overlay) - accepted
        assert not unknown, f'config {name!r} sets {sorted(unknown)}, which nothing accepts'
        assert cfg.why, f'config {name!r} has no `why`; it prints on selection'
