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
    'CheckpointBuffer._write'         : 'Optimization.persistence.checkpoint_buffer',
    'save_worker_checkpoint'          : 'Optimization.simdriver.strategy_runner',
}


# Where each CARVE_MAP qualname lives. Same split, same reason — and this gate matters for the
# same reason the flow gate below does: a broken carve anchor does not raise, it makes
# `t_inbound` read 0.0, and 0.0 reads as "the inbound drain never ran".
_CARVE_MAP_HOME = {
    'ReorderMixin._receive'  : 'Warehouse.inventory.inventory_reorder',
    'SiteReceiving.drain'    : 'Inbound.receiving',
    'PutawayPool.drain'      : 'Inbound.putaway_pool',
}


def test_carve_map_symbols_resolve():
    # NON-VACUITY: the two maps must cover each other exactly, and be non-trivial.
    assert set(_CARVE_MAP_HOME) == set(ct.CARVE_MAP), \
        'CARVE_MAP and its home table diverged — update both together'
    assert len(ct.CARVE_MAP) >= 3

    for qualname, module_name in _CARVE_MAP_HOME.items():
        mod = importlib.import_module(module_name)
        obj = mod
        for part in qualname.split('.'):
            assert hasattr(obj, part), \
                f'CARVE_MAP anchor {qualname!r} broke: {module_name} has no {part!r}'
            obj = getattr(obj, part)
        assert callable(obj), f'{qualname} resolved to a non-callable'
        fn = inspect.unwrap(obj)
        real = getattr(fn, '__qualname__', qualname)
        assert real == qualname, \
            f'qualname drift: CARVE_MAP says {qualname!r}, code says {real!r}'


def test_a_carve_section_is_not_in_the_runner_vocabulary():
    """`CARVE_SECTIONS` must stay OUT of `SECTIONS`.

    `SECTIONS` is strategy_runner's own `t_*` vocabulary and
    `test_section_vocabulary_matches_strategy_runner` pins it against the source. A carve is a
    read-side regrouping with no worker accumulator behind it, so putting one in `SECTIONS`
    would fail that gate — and, worse, would invite someone to add a `t_inbound` column to
    `runtime_metrics.SECTIONS`, which is a PARTITION for the stacked graph and would then
    double-count.
    """
    assert not set(ct.CARVE_SECTIONS) & set(ct.SECTIONS), \
        'a carve section leaked into SECTIONS — see attribute_sections for why they differ'


def test_the_carve_is_a_partition_and_is_inert_when_empty():
    """The two properties the carve has to have, asserted on a tree shaped like a real one.

    Non-vacuity is the second half: with an empty CARVE_MAP the function must reproduce the
    pre-carve attribution exactly, or every archived capture silently re-attributes.
    """
    def node(name, cum, kind='fn', children=()):
        n = ct.Node(name, '', kind)
        n.cum_s = cum
        for c in children:
            n.children[c.name] = c
        return n

    inbound = node('inventory_reorder:ReorderMixin._receive', 0.30)
    reord   = node('t_reord', 1.00, 'section', [
        node('Inventory_Management:ReorderMixin.check_reorders', 0.90, 'fn', [inbound])])
    root    = node('root', 3.00, 'fn', [reord, node('t_sim', 2.00, 'section')])

    got = ct.attribute_sections(root)
    assert abs(got['t_inbound'] - 0.30) < 1e-12, got['t_inbound']
    assert abs(got['t_reord'] - 0.70) < 1e-12, got['t_reord']
    assert abs(sum(got.values()) - 3.00) < 1e-12, \
        f'the carve broke the partition: {sum(got.values())} != 3.00'

    saved = dict(ct.CARVE_MAP)
    ct.CARVE_MAP.clear()
    try:
        inert = ct.attribute_sections(root)
    finally:
        ct.CARVE_MAP.update(saved)
    assert abs(inert['t_reord'] - 1.00) < 1e-12 and inert['t_inbound'] == 0.0, \
        'an empty CARVE_MAP must leave attribution exactly as it was before carves existed'


def test_section_map_symbols_resolve():
    # NON-VACUITY: the two maps must cover each other exactly, and be non-trivial.
    assert set(_SECTION_MAP_HOME) == set(ct.SECTION_MAP), \
        'SECTION_MAP and its home table diverged — update both together'
    # 14, not the 21 this floor was written against: ticket 07 collapsed eight `t_save`
    # anchors into one (`CheckpointBuffer._write`) because it collapsed eight writers into
    # one insert site. A floor still ratchets -- the map cannot be quietly emptied -- but it
    # is a floor on the TABLE, and the table shrank because the code did.
    assert len(ct.SECTION_MAP) >= 14

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

    # The worker's vocabulary is a SYMBOL now, not twenty-three closure variables a regex
    # had to recognise: `SectionTimers.SECTIONS`.  Resolved rather than scraped, so a
    # section renamed in the product is a mismatch here instead of an empty regex match
    # that used to read as "the code moved" and could just as easily have read as "no
    # sections exist".
    from Optimization.simdriver.section_timers import SectionTimers
    accs = set(SectionTimers.SECTIONS)
    assert accs, 'SectionTimers declares no sections'

    traced = {name[2:] for name in ct.SECTIONS}       # the tracer's names, minus the t_
    # Sections the WORKER keeps that the tracer deliberately does not map: t_build is the
    # derived sum of sample + task; kf is a sub-span of pre (an overlay, not a partition
    # member); p1/p2 are fast_pick's internal phase split, which the tracer sees inside
    # t_sim rather than beside it.
    # The inbound carve (2026-09-24): overlays of reord charged from the inbound probe
    # (`Warehouse.kernel.perf_probe`), which the tracer sees inside t_reord.
    OVERLAY = {'build', 'kf', 'p1', 'p2', 'inb_pre', 'inb_freeze', 'inb_pack', 'inb_yplan',
               'inb_dplan', 'inb_unload', 'inb_handoff', 'put', 'put_open'}
    assert traced <= accs, \
        f'the framework maps sections the worker no longer keeps: {sorted(traced - accs)}'
    assert accs - traced <= OVERLAY, \
        f'strategy_runner grew unknown sections: {sorted(accs - traced - OVERLAY)}'

    # RESOLVING THE NAME IS NOT ENOUGH -- the relationship has to hold.  A vocabulary the
    # worker imports and never accumulates into would satisfy every assertion above while
    # measuring nothing, which is the exact shape of defect this repo keeps finding in
    # gates that only check that symbols exist.  So: every declared section must have a
    # real `timers.add(...)` site in the batch loop.
    # Two accumulation forms, and both are real: `split()` closes a lap and charges one or
    # more sections; `add()` charges a section from a LOCAL stopwatch, which is what an
    # overlay like `kf` (a sub-span of `pre`) must use so it does not advance the lap
    # cursor and carve its stretch out of the section containing it.  Parsed as CALLS
    # rather than matched as text, so a section named only in a comment does not count.
    accumulated = set()
    for call in re.findall(r'timers[.](?:add|split)[(]([^)]*)[)]', src):
        accumulated.update(re.findall(r"'([a-z_0-9]+)'", call))
    missing = sorted(set(SectionTimers.SECTIONS) - accumulated)
    assert not missing, (
        f'{missing} are declared sections that strategy_runner never accumulates into via '
        f'timers.add() or timers.split(); a vocabulary nothing charges measures nothing')

    # The checkpoint log line still carries the tokens bench_sections/macro parse.
    # (t_inv logs as 'cons=' — the conservation ledger; bench_sections accepts both
    # spellings since the rename.  kf=/gc= are the 2026-08-19 overlay tokens.)
    for token in ('reord=', 'smpl=', 'task=', 'pre=', 'sim=', 'extr=', 'cons=', 'db=',
                  'kf=', 'gc=',
                  # The save decomposition (2026-09-17).  sql+pkl+drn == db, and rows= is the
                  # denominator that separates "each write got more expensive" from "there are
                  # more writes".  Unpinned, a rename here silently zeroes `macro_sections`'
                  # whole overlay and census with every test still green.
                  'sql=', 'pkl=', 'drn=', 'rows=', 'dbmb=', 'walmb='):
        assert token in src, f'checkpoint log line lost {token!r} — macro adapter breaks'

    # The two PER-ARM save lines.  They are not on a checkpoint line, so `_SEC_RE` never sees
    # them — which is exactly why they need pinning here: the runner charges both to `save`, so
    # `runtime_metrics.save_s` contains them and the ladder's `t_save` would not.
    for token in ('[save] index build', '[save] run-end close'):
        assert token in src, f'strategy_runner lost {token!r} — save_tail reads nothing'

    # And bench_sections' regexes must actually match the emitted shape end-to-end —
    # _SEC_RE silently rotted once when inv= became cons= (parse() returned zero rows).
    # The sample carries the appended overlay suffix to PROVE the append is non-breaking.
    import bench_sections as bsec
    # FOUR DECIMALS, because that is what the runner emits since 2026-09-18: `pre` is ~0.035 s
    # per batch and `:.1f` rounded it to 0.0, which was read for two whole ladders as "the
    # section is not measured" while `runtime_metrics.pre_s` sat at 90 s per arm.
    sample = ('  Batch   10/100  db=2.5000s | reord=1.0000s build=2.0000s '
              '(smpl=0.5000s task=0.0006s) pre=0.0355s sim=4.0000s extr=5.0000s cons=6.0000s '
              'kf=0.4000s gc=0.1200s sql=2.1000s pkl=0.3000s drn=0.1000s '
              'rows=166078 dbmb=23.1 walmb=4.2')
    assert bsec._SEC_RE.search(sample), \
        "bench_sections._SEC_RE no longer parses strategy_runner's checkpoint line"
    assert bsec._KF_RE.search(sample) and bsec._GC_RE.search(sample), \
        'bench_sections overlay regexes (kf=/gc=) no longer match the emitted tokens'

    # A sub-0.05 s section must survive the round trip. Under the old `:.1f` it read 0.0.
    _m = bsec._SEC_RE.search(sample)
    assert float(_m['pre']) == 0.0355, (
        f"a 35 ms section parsed as {_m['pre']} — the emitter is rounding it away again")
    assert float(_m['task']) == 0.0006, 'the noise anchor must be visible, not rounded to zero'

    # Every decomposition regex matches, and the sub-partition sums to the section.
    for name, rx in (('sql', bsec._SQL_RE), ('pkl', bsec._PKL_RE), ('drn', bsec._DRN_RE),
                     ('rows', bsec._ROWS_RE), ('dbmb', bsec._DBMB_RE),
                     ('walmb', bsec._WALMB_RE)):
        assert rx.search(sample), f'bench_sections lost the {name}= regex'
    _parts = sum(float(rx.search(sample)[1])
                 for rx in (bsec._SQL_RE, bsec._PKL_RE, bsec._DRN_RE))
    assert abs(_parts - float(bsec._DB_RE.search(sample)[1])) < 1e-9, \
        'sql+pkl+drn must equal db — they are a SUB-partition of it, not neighbours'

    # The macro adapter's short names must be names `parse` actually PRODUCES -- run it, do not
    # hardcode the list, or this test agrees with itself rather than with the parser. Nothing
    # checked this before, so a typo there read zero for every rung with every test green.
    import calltree_scenarios as _sc
    import tempfile
    _p = os.path.join(tempfile.mkdtemp(), 'run.log')
    with open(_p, 'w', encoding='utf-8') as _fh:
        _fh.write('12:00:00  x  ' + sample.strip() + '\n')
        _fh.write('12:00:01  x    [save] run-end close 0.01s rows=5 dbmb=1.0 walmb=0.0\n')
        _fh.write('12:00:02  x    [save] index build 0.24s dbmb=1.0\n')
        _fh.write('12:00:09  x  done\n')
    _produced = set(bsec.parse(_p)[0][1])
    for short in list(_sc._MACRO_OVERLAY) + list(_sc._MACRO_CENSUS):
        assert short in _produced, (
            f'macro_sections maps {short!r}, which bench_sections.parse never emits')

    # And the per-arm tail reader must actually see both [save] lines and split the wall.
    _tail = bsec.save_tail(_p)
    assert _tail['index_build_s'] == 0.24 and _tail['run_end_close_s'] == 0.01, _tail
    assert _tail['analysis_s'] == 7.0, (
        f"the analysis half should be the 7 s after the last save line, got {_tail}")


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
    'dock'                : 'Inbound.dock',
    # The inbound path. Every one of these was structurally dead in every runnable rung until
    # `build_assets(inbound=True)` existed, so the ladder reported the subsystem as costless.
    'receiving'           : 'Inbound.receiving',
    'space'               : 'Inbound.space',
    'site_space'          : 'Inbound.site_space',
    'transit'             : 'Inbound.transit',
    'gain'                : 'Inbound.gain',
}


#: Flow keys that MUST be non-zero in a small inbound cell. Declared explicitly rather than
#: asserted blanket-wise, because several anchors are legitimately zero in this configuration
#: (`window_aggs` needs futuresight, `tier_sorts`/`avail_builds` belong to the merge adapter,
#: `held_appends` needs a staging floor) and a blanket assertion would have to be weakened until
#: it proved nothing.
_FLOWS_LIVE_IN_AN_INBOUND_CELL = (
    'drains', 'freezes', 'view_composes', 'trailer_plans', 'unload_prices',
    'plan_orders', 'yard_plans', 'dock_plans', 'place_loads', 'pool_rebuilds',
)


def test_every_flow_anchor_that_should_fire_does_fire():
    """The gate `test_flow_anchors_resolve` STRUCTURALLY cannot be.

    That test resolves each anchor's symbol against its module, which catches a rename. It never
    looks at a TREE, so it cannot catch a wrong RELATIONSHIP — and a parent-form anchor whose
    parent is real, whose name is real, and whose relationship is wrong reports 0. Zero reads as
    "the path never ran", which is the exact misreading the flow table exists to prevent.

    It has already happened here. `yard_plans` and `dock_plans` were declared as
    `('gain:plan_order', 'transit:YardTransit.yard_order')` — direct-child form — while the real
    chain runs `yard_order -> priorities.bounded_order -> <the arm's registry entry> ->
    plan_order`. Both symbols were in the tree (4 calls each) and both flows read 0 for their
    whole life. Only a tree could tell.  It would have happened AGAIN when the yard plan went
    lazy (O1, 2026-09-24): a drain stopped calling `yard_order` at all, and this test is what
    refused the old anchor.
    """
    import calltree_growth as cg
    from calltree_tracer import CallTreeTracer

    assets = scenarios.build_assets(
        n_skus=200, bins_per_aisle=100, coverage=10.0, safety=2.0, seed=11,
        strategy='uni_rank_labor_norsl', put_timing=True, recv_crew=2,
        inbound=True, trailer_type='53', dock_doors=4,
        yard_policy='gain_forecast', dock_policy='gain_forecast')
    tr = CallTreeTracer(track_c_calls=False)
    tr.start()
    scenarios.run_meso(assets, n_batches=3, seed=11, recv_deadline=20.0)
    tr.stop()
    tree = tr.tree().to_dict()
    flows = cg._flows(tree, cg._flat_counts(tree))

    dead = [k for k in _FLOWS_LIVE_IN_AN_INBOUND_CELL if not flows.get(k)]
    assert not dead, (
        f'flow anchor(s) {dead} read ZERO in a cell that exercises them — the symbol resolves '
        f'(test_flow_anchors_resolve passes) but the anchor does not match the tree. A '
        f'parent-form anchor reached through a dispatcher needs the DEEP form: a 3-tuple '
        f'`(name, parent, True)`. Flows measured: '
        f'{ {k: flows.get(k) for k in _FLOWS_LIVE_IN_AN_INBOUND_CELL} }')

    # NON-VACUITY: the assertion above is only meaningful if this cell can produce a zero at all.
    assert flows.get('window_aggs') == 0, (
        'window_aggs fired in a cell with no futuresight arm — either the cell changed or the '
        'anchor now matches something it should not, and either way the check above no longer '
        'distinguishes a live anchor from a dead one')


def test_flow_anchors_resolve():
    """Every `_FLOW_COUNTS` name must still name a real callable."""
    import calltree_growth as cg

    assert len(cg._FLOW_COUNTS) >= 5, 'the flow table shrank; was it gutted rather than fixed?'

    # A 3-tuple `(name, parent, True)` is the DEEP form -- the anchor is a descendant of its
    # parent rather than a direct child. It resolves the same two symbols, so this gate is
    # unchanged by it; what the deep form needs is a TREE, which the test below supplies.
    for key, spec in cg._FLOW_COUNTS.items():
        name, parent = spec[0], spec[1]
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


# ── the scenario must run what PRODUCTION runs ────────────────────────────────────────

def test_the_scenario_samples_with_the_era_not_the_frozen_default():
    """The framework's job is to describe what production costs, so its fixture must run
    production's configuration.

    `BatchConfig.sampler` defaults to `'v1'` deliberately — `Optimization/config/channels.py`
    records the reason: non-runner constructions "keep their frozen historical meaning; the
    runner passes the era in". That is right for a unit test pinning an old batch sequence and
    wrong for an instrument, and `build_assets` was silently taking the default.

    It mattered. `Batch.__init__` is SECTION_MAP's anchor for `t_sample`, and measured directly
    at k=0.15*N over 500..8,000: v1 fits k=1.477 (r²=0.993), v3 fits k=0.822 (r²=0.901), 9.6x
    apart at N=8,000. The archived ladders fit `t_sample` at k=1.522 — so every `t_sample`
    exponent in `out/archive/` is the RETIRED sampler, measured on code no run executes.
    """
    from Optimization.config import settings

    assets = scenarios.build_assets(n_skus=150, bins_per_aisle=40, n_pickers=3, seed=11,
                                    coverage=2.0, safety=0.4)
    assert assets.batch_cfg.sampler == settings.SAMPLER, (
        f'the scenario samples with {assets.batch_cfg.sampler!r} while the era declares '
        f'{settings.SAMPLER!r} — every exponent this instrument fits is about a different '
        f'batch sequence than production draws')


def test_the_sampler_can_still_be_pinned_for_an_archived_comparison():
    """The frozen-historical default existed to protect something real: reproducing an
    archived artifact. Following the era by default must not take that away, or the archive
    becomes unreadable rather than merely superseded."""
    assets = scenarios.build_assets(n_skus=150, bins_per_aisle=40, n_pickers=3, seed=11,
                                    coverage=2.0, safety=0.4, sampler='v1')
    assert assets.batch_cfg.sampler == 'v1', (
        'an explicit sampler no longer overrides the era, so no archived ladder can be '
        'reproduced')


def test_switching_the_scenario_sampler_moves_no_measurement_at_fixture_scale():
    """The check that makes the era switch SAFE, and it corrects a claim in the source.

    `Workload_Builder.py` says v3 "moves every batch sequence", and the run harness treats it
    as a results era (`batch_precompute` fingerprints non-v1 samplers apart). On THIS fixture
    that is not so: at 2,000 SKUs over ten seeds, v1 and v3 draw the same SKUs, the same
    quantities, and in the same order.

    The two agree because both implement the same selection -- "first index whose cumulative
    weight passes the draw" -- and consume one uniform per draw. They part company only where
    v1's float accumulation does, which is the ~1e26 weight dynamic range of the production
    catalogue, not a synthetic fixture whose weights are benign.

    So pointing the scenarios at the era moved the COST of drawing a batch (k 1.477 -> 0.822,
    9.6x at N=8,000) and not the batch. That is what makes the switch a measurement fix rather
    than a new baseline: count exponents stay comparable with the archive, and only the
    spurious `t_sample` wall exponent goes away.

    If this ever fails, the era switch HAS become a comparability break at fixture scale and
    the archived count comparisons stop being valid -- which is a finding, not a broken test.
    """
    import random

    from Warehouse.picking.Workload_Builder import Batch

    kw = dict(n_skus=2_000, bins_per_aisle=40, n_pickers=3, seed=11,
              coverage=2.0, safety=0.4)
    a1 = scenarios.build_assets(sampler='v1', **kw)
    a3 = scenarios.build_assets(sampler='v3', **kw)

    drew = 0
    for s in range(4):
        b1 = Batch(a1.batch_cfg, a1.inventory, a1.affinity, rng=random.Random(s))
        b3 = Batch(a3.batch_cfg, a3.inventory, a3.affinity, rng=random.Random(s))
        drew += len(b1.items)
        assert b1.items == b3.items, (
            f'seed {s}: v1 and v3 drew different batches at fixture scale, so switching the '
            f'scenario default to the era HAS moved what the ladder measures')
        assert list(b1.items) == list(b3.items), (
            f'seed {s}: same SKUs, different draw ORDER -- insertion order is the draw '
            f'sequence and downstream task building reads it')

    assert drew > 0, 'no SKUs drawn at all, so the comparison above was between empty batches'


# ── a resolvable name is not a called one ─────────────────────────────────────────

def test_a_traced_arm_actually_spends_time_in_t_save():
    """THE GAP THIS FILE HAD, and it hid a dead section for over a month.

    Every test above asks whether an anchor RESOLVES. All of them resolved, and this
    instrument's `t_save` still read **0.000000 in every archived capture** -- while
    `runtime_metrics.save_s`, a stopwatch measuring the same subject, read ~31% of the arm.
    Two instruments, one name, and only the one nobody reads was dead. The cause: its eight
    `save_<table>` anchors had
    zero production callers (the write path was `save_checkpoint_bundle`, never anchored) and
    its ninth only fires at a checkpoint boundary a short capture never reaches. Memory
    `symbol-table-relationship-not-verified-by-symbols`: a gate that resolves NAMES cannot
    catch a wrong RELATIONSHIP, and the fix shape is to exercise it and assert it produced
    something.

    So this one WRITES, through the production path, and asserts the section moved.
    """
    import os
    import tempfile

    from Optimization.persistence.Picking_Data import BatchStats, create_run, init_run_db
    from Optimization.persistence.checkpoint_buffer import CheckpointBuffer

    path = os.path.join(tempfile.mkdtemp(), 'sim.db')
    init_run_db(path)
    rid = create_run(path, 'uni_fifo_norsl')

    tracer = ct.CallTreeTracer()
    tracer.start()
    try:
        buf = CheckpointBuffer()
        buf.append('batch_stats', BatchStats(
            run_id=rid, batch_id=1, duration=1.0, num_tasks=1, total_items=2,
            avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5))
        buf.close(path, rid)
    finally:
        tracer.stop()

    got = ct.attribute_sections(tracer.tree())
    assert got.get('t_save', 0.0) > 0.0, (
        't_save is still 0.0 after a real checkpoint write -- the anchor does not name '
        'anything the write path calls, which is exactly how it read zero for a month')

# -- an arm the run does not carry must REFUSE, never measure another ------------------

def test_run_fullfid_refuses_an_unknown_arm_instead_of_running_the_first():
    """`run_fullfid(strategy=...)` used to fall back to the first prepared unit when the key
    matched nothing -- "keeps the tier runnable".  On 2026-09-18 a profile asked for
    `uni_rank_random` (the key is `uni_rank_random_norsl`), ran `fifo` instead, and reported
    the priced drain at 0.2 s.  The helper must raise with the keys the run actually
    prepared, on both the coupled shape (leaves) and the flat one."""
    coupled = [{'leaves': [{'strategy': 'uni_fifo_norsl'}, {'strategy': 'uni_fifo_norsl'}]},
               {'leaves': [{'strategy': 'uni_rank_random_norsl'},
                           {'strategy': 'uni_rank_popularity_norsl'}]}]
    flat = [{'strategy': 'uni_fifo_norsl'}, {'strategy': 'uni_rank_random_norsl'}]
    assert scenarios._all_arm_keys(coupled) == ['uni_fifo_norsl', 'uni_rank_random_norsl',
                                                'uni_rank_popularity_norsl']
    assert scenarios._all_arm_keys(flat) == ['uni_fifo_norsl', 'uni_rank_random_norsl']
    # a match passes silently
    scenarios._refuse_unknown_arm('uni_rank_random_norsl', [flat[1]], scenarios._all_arm_keys(flat))
    # no match raises, naming the bare-rule mistake and the keys that exist
    import pytest
    with pytest.raises(scenarios.ScenarioUnavailable) as ei:
        scenarios._refuse_unknown_arm('uni_rank_random', [], scenarios._all_arm_keys(coupled))
    msg = str(ei.value)
    assert "'uni_rank_random'" in msg and 'uni_rank_random_norsl' in msg and '_norsl' in msg
    # and the source no longer carries the fallback
    src = inspect.getsource(scenarios.run_fullfid)
    assert 'or unit_args[:1]' not in src and 'or strategy_args[:1]' not in src,         'the first-arm fallback is back in run_fullfid'

