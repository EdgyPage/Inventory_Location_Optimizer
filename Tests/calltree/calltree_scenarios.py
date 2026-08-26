"""
calltree_scenarios.py — seeded, deterministic scenario tiers for the calltree framework.

Every scenario here fires REAL reorder-time placement. That sentence earns its caps:
`Tests/bench/perf_simulation.py::_build_inventory` builds orders with no
equilibrium_qty/reorder_point, so `_notify_pick` never flags a SKU and the entire t_reord
section — every assignment function — silently never executes in tools built on it
(`profile_lifecycle.py` also runs the non-production `PickSimulation` engine). This module
wraps the same builders, then sets real reorder fields per order and stocks to equilibrium,
so depletion actually crosses the reorder point. The production engine is
`fast_pick.DeferredPickSimulation`, same as `strategy_runner`.

Tiers
-----
  micro    direct calls of one hot phase at a time (placement wave, batch sample,
           task build, pick sim, pre-snapshot, extractors) on shared fixed assets.
  meso     a single-arm batch loop mirroring strategy_runner's section structure
           (t_reord / t_sample / t_task / t_pre / t_inv / t_sim / t_extract), using
           the real strategies registry (STRATEGY_BY_KEY) for placement wiring.
  fullfid  trace sr._run_strategy_worker itself in-process (coverage_e2e driver shape);
           requires generated profile DBs — raises ScenarioUnavailable otherwise.
  macro    no new instrumentation: parse a real run's checkpoint log line via
           Tests/bench/bench_sections and emit section walls with an empty tree.

Not collected by pytest (no test_ prefix). Consumed by calltree_capture.py /
calltree_growth.py / test_calltree_smoke.py.
"""
from __future__ import annotations

import os
import random
import sys
import time
from dataclasses import dataclass, field

# ── path setup (script-safe; conftest.py does this for pytest runs) ──────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Tests/<sub>/ -> repo root
_BENCH     = os.path.join(os.path.dirname(_HERE), 'bench')
for _p in (_REPO_ROOT, _BENCH):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

from perf_simulation import (_CATEGORIES, _build_affinity_store, _build_inventory)

from Warehouse.catalog.Inventory_Builder import Inventory
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.picking.fast_pick import DeferredPickSimulation
from Warehouse.picking.Pick import PickConfig
from collections import namedtuple as _namedtuple

from Warehouse.inventory.dock import DockSpec as _DockSpec
from Warehouse.inventory.put_queue import (
    PutQueueSet as _PutQueueSet, PutQueueSpec as _PutQueueSpec,
    store_and_fulfillment as _store_and_fulfillment)
from Warehouse.kernel.cost_model import SpeedProfile as _SpeedProfile
from Warehouse.layout.Storage_Primitive import (
    FulfillmentCart as _FulfillmentCart, StoreCart as _StoreCart)
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
from Optimization.config.strategies import STRATEGY_BY_KEY, StrategyContext
from Optimization.metrics.Simulation_Analytics import (
    fused_pre_snapshot, extract_batch_stats, extract_picker_events, extract_picks,
    extract_task_stats, snapshot_aisle_metrics)
from Optimization.metrics.Workload import WorkloadParams

_HANDLINGS = ['conveyable', 'non-conveyable']

DEFAULT_STRATEGY = 'uni_rank_labor_norsl'   # production store winner, uniform (cheap) stock


class ScenarioUnavailable(RuntimeError):
    """Raised when a tier's external prerequisites (profile DBs, run logs) are absent."""


# ── reorder-enabled inventory ─────────────────────────────────────────────────

def set_reorder_fields(orders, seed: int, *, coverage: float = 10.0, safety: float = 2.0,
                       lead_time: float = 0.0, supply_cv: float = 0.0) -> None:
    """Give every order the PRODUCTION-SHAPED equilibrium/reorder model so placement fires
    with production scaling.

    Mirrors Warehouse/generation/generate_inventory.py::build_inventory_equilibrium:
        expected = f · q                    (relative_frequency × quantity_rate — NOT ×k;
                                             production absorbs batch size into coverage)
        eq  = max(1, round(coverage · expected))
        rp  = max(1, min(eq − 1, round(expected · (lead + safety))))   [1 when eq == 1]
    Demand-proportional stock depth is the property the deep ladder exposed and flat
    randint(3,8) equilibria hid: per-SKU bin multiplicity grows with demand mass, which is
    the second factor of Task.from_batch's quadratic. coverage=10/safety=2 = production;
    smoke tests pass coverage≈2 with safety scaled by coverage/10 so the rp/eq fraction
    stays ≈ production's ~0.2 (safety left at 2 with small coverage degenerates to
    rp = eq−1 — reorder-nearly-every-batch, which would inflate t_reord in every
    measurement). lead_time=0 releases reorders in the same check_reorders pass.

    The seed parameter is kept for API stability (production draws no randomness here).
    """
    del seed  # deterministic: a pure function of each order's demand
    for c in orders:
        expected = c.demand.relative_frequency * c.demand.quantity_rate
        eq = max(1, round(coverage * expected))
        c.expected_batch_demand = expected
        c.equilibrium_qty       = eq
        c.reorder_point         = (max(1, min(eq - 1, round(expected * (lead_time + safety))))
                                   if eq > 1 else 1)
        c.lead_time_mean        = float(lead_time)
        c.supply_cv             = float(supply_cv)


# ── assets ────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioAssets:
    """Everything one arm needs, built deterministically from (sizes, seed)."""
    inventory : Inventory
    affinity  : object
    warehouse : object
    mgr       : Inventory_Manager
    pick_cfg  : PickConfig
    wp        : WorkloadParams
    batch_cfg : BatchConfig
    strategy  : str
    sizes     : dict = field(default_factory=dict)


def build_assets(*, n_skus: int = 2_000, bins_per_aisle: int = 100,
                 n_pickers: int = 10, seed: int = 42, target_fill: float = 0.85,
                 strategy: str = DEFAULT_STRATEGY,
                 coverage: float = 10.0, safety: float = 2.0,
                 put_timing: bool = False, put_split: bool = False,
                 put_staging: int | None = None, put_crew: int = 1,
                 recv_crew: int = 0) -> ScenarioAssets:
    """Deterministic single-arm assets with production placement wiring.

    Mirrors Diagnostics/trace_lifecycle.py's recipe (plan_warehouse to a target fill,
    StrategyContext + the strategies-registry build), with the reorder fields set BEFORE
    sampling so every sampled order carries them. coverage/safety default to production's
    equilibrium model (10/2); fast tests shrink coverage AND scale safety with it
    (safety ≈ 2·coverage/10) to keep the rp/eq fraction production-shaped.
    """
    strat = STRATEGY_BY_KEY[strategy]

    random.seed(seed)
    np.random.seed(seed)
    pool = _build_inventory(n_skus, seed)
    set_reorder_fields(pool.orders, seed, coverage=coverage, safety=safety)

    n_cols = max(1, bins_per_aisle // 20)
    plan = Inventory_Manager.plan_warehouse(
        pool.orders, categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=n_cols * 48, aisle_height=20 * 48,
        target_fill=target_fill, rng=random.Random(seed + 1))
    inventory = Inventory(plan.sampled)
    affinity  = _build_affinity_store(inventory, top_k=20, seed=seed)

    pick_cfg = PickConfig(num_pickers=n_pickers, x_speed=1.0, y_speed=0.5,
                          pick_intercept=1.0, pick_weight_coef=1.1,
                          pick_volume_coef=1e-3, cart_swap_coef=10.0)
    wp = WorkloadParams.from_pick_config(pick_cfg)
    for c in inventory.orders:
        c.compute_labor_cost(wp.pick_intercept, wp.pick_weight_coef, wp.pick_volume_coef)

    freq_by_sku = {c.sku: c.demand.relative_frequency for c in inventory.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate      for c in inventory.orders}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.relative_frequency
                   for c in inventory.orders if c.sku in affinity._sku_to_idx}
    batch_cfg = BatchConfig(inventory_size=len(inventory.orders),
                            mean_fraction=0.15, std_fraction=0.05)
    ctx = StrategyContext(
        affinity=affinity, wp=wp, freq_by_idx=freq_by_idx,
        freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku,
        beta=1.0, orders=inventory.orders,
        expected_batch_skus=batch_cfg.mean_fraction * batch_cfg.inventory_size)

    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse = Warehouse_Builder().from_config(plan.warehouse_cfg).build()

    random.seed(seed + 100)
    mgr = Inventory_Manager(warehouse, affinity=affinity if strat.needs_affinity else None)
    if strat.needs_affinity:
        mgr._affinity = affinity
        mgr.init_lift_state(affinity)
    if strat.needs_demand:
        mgr.init_demand_state(inventory, wp)
    if strat.uses_aisle_index:
        mgr.init_travel_costs(wp)
    strat.build(mgr, ctx)
    # Uniform initial stock at each order's equilibrium_qty (quantity=None), so
    # position starts above the reorder point and picking depletes across it.
    random.seed(seed + 1)
    mgr.enqueue_all(inventory.orders)

    # ── the put-away / receiving machinery, all OFF by default ────────────────────
    # Their off-state is "the binder was never called", so a scenario that does not ask
    # for them is byte-identical to one built before these parameters existed. Enabled
    # here rather than by the caller because `enqueue_all` above must run FIRST: initial
    # intake is not a receipt and must not be diverted onto a dock.
    if put_timing or put_split or recv_crew:
        mgr.enable_putaway_timing(_SpeedProfile(2.0, 4.0), size=put_crew)
    if put_split:
        # Crews passed explicitly for the same reason the runner does it: the default
        # fallback would give three queues the FULL crew size, i.e. 3x the putters, and
        # a split-vs-single comparison would measure headcount rather than routing.
        _c = _namedtuple('_PutCrew', 'speed size')(_SpeedProfile(2.0, 4.0), put_crew)
        mgr.put_queues = _store_and_fulfillment(
            cart_crew=_c, pallet_crew=_c, ff_crew=_c,
            cart_staging=put_staging, pallet_staging=put_staging,
            ff_staging=put_staging,
            store_cart=_StoreCart, ff_cart=_FulfillmentCart, swap_coef=10.0)
    elif put_staging is not None:
        # Staging without the split: one queue with a floor limit. The cheapest way to
        # make the held list and the refill loop execute at all.
        mgr.put_queues = _PutQueueSet([_PutQueueSpec('all', staging=put_staging)])
    if recv_crew:
        mgr.enable_receiving(_DockSpec(size=recv_crew))

    return ScenarioAssets(
        inventory=inventory, affinity=affinity, warehouse=warehouse, mgr=mgr,
        pick_cfg=pick_cfg, wp=wp, batch_cfg=batch_cfg, strategy=strategy,
        sizes={'n_skus': n_skus, 'n_skus_sampled': len(inventory.orders),
               'bins_per_aisle': bins_per_aisle, 'n_bins': len(warehouse.bins),
               'n_aisles': len(warehouse.aisles), 'n_pickers': n_pickers,
               'target_fill': target_fill,
               'put_timing': bool(put_timing or put_split or recv_crew),
               'put_split': put_split, 'put_staging': put_staging,
               'put_crew': put_crew, 'recv_crew': recv_crew})


# ── meso: the single-arm batch loop ──────────────────────────────────────────

@dataclass
class MesoResult:
    sections   : dict            # t_* -> wall seconds (perf_counter, this pass)
    batches    : int
    skipped    : int
    picks      : int             # units picked (Σ pick quantities)
    placements : int             # reorder-time units placed (Σ batch_rp)
    reorders   : int             # distinct SKUs that triggered reorders


def run_meso(assets: ScenarioAssets, *, n_batches: int = 20, seed: int = 42,
             tracer=None, put_deadline: float | None = None,
             recv_deadline: float | None = None) -> MesoResult:
    """One arm's batch loop, phase-for-phase with strategy_runner L489-690.

    With a tracer: each phase runs inside tracer.section(t_*), so tree↔section alignment
    is by construction. Without: the same section names accumulate plain perf_counter
    walls (the untraced pass of a two-pass capture).
    """
    mgr, warehouse = assets.mgr, assets.warehouse
    sections = {k: 0.0 for k in
                ('t_reord', 't_sample', 't_task', 't_pre', 't_inv', 't_sim', 't_extract')}
    from contextlib import contextmanager

    @contextmanager
    def sec(name: str):
        if tracer is not None:
            with tracer.section(name):
                yield
        else:
            t0 = time.perf_counter()
            try:
                yield
            finally:
                sections[name] += time.perf_counter() - t0

    lift_cache: dict = {}
    picks_total = placements_total = reorders_total = skipped = 0
    rng_batches = seed + 1000

    for i in range(n_batches):
        with sec('t_reord'):
            triggered = mgr.check_reorders(put_deadline=put_deadline,
                                           recv_deadline=recv_deadline)
            _rm, batch_rp = mgr.pop_churn()
            # THE FOUR PER-BATCH SURFACES the runner calls inside its own `t_reord`, and
            # the reason this loop can measure the put-away/receiving work at all. Three of
            # them walk a STANDING BACKLOG rather than this batch's work, so leaving them
            # out would hide exactly the growth a ladder exists to find. Their results are
            # discarded here -- the runner turns them into DB rows; what is being measured
            # is the cost of producing them.
            mgr.queue_contents()
            mgr.queue_state_rows(i)
            mgr.carryover_rows(i)
            mgr.receiving_snapshot()
            mgr.drain_receiving_records()
        reorders_total   += len(triggered)
        placements_total += batch_rp

        with sec('t_sample'):
            batch = Batch(assets.batch_cfg, assets.inventory, affinity=None,
                          rng=random.Random(rng_batches + i))
        with sec('t_task'):
            # `from_batch_with_shortfall`, matching the runner: the plain `from_batch`
            # discards the demand no bin could serve, which is one of the three carry
            # causes and the one a starved configuration produces most of.
            tasks, _short = Task.from_batch_with_shortfall(
                batch, warehouse, manager=mgr, cart=assets.pick_cfg.cart)

        # Mirrors the runner's fused pass (occupancy accumulated inside t_pre; keyframe
        # rows not requested — the meso loop writes no keyframes).  t_inv keeps its slot
        # in the partition with the residual bookkeeping the runner still does there.
        with sec('t_pre'):
            _occupancy, _ = fused_pre_snapshot(mgr, False)
            am = snapshot_aisle_metrics(mgr, batch_id=i, run_id='calltree')
        with sec('t_inv'):
            _residual = _occupancy - _occupancy   # shape-only stand-in for the ledger

        if not tasks:
            skipped += 1
            continue

        with sec('t_sim'):
            sim    = DeferredPickSimulation(tasks, assets.pick_cfg, manager=mgr)
            events = sim.run()

        with sec('t_extract'):
            extract_batch_stats(events, batch_id=i, k_pickers=assets.pick_cfg.num_pickers,
                                run_id='calltree')
            extract_task_stats(events, tasks, batch_id=i, affinity=assets.affinity,
                               wp=assets.wp, run_id='calltree', lift_cache=lift_cache)
            extract_picker_events(events, batch_id=i, run_id='calltree')
            picks_b = extract_picks(events, batch_id=i, run_id='calltree')
        picks_total += sum(p.quantity for p in picks_b)

    if tracer is not None:
        # Section walls belong to the untraced pass; report zeros here so nobody
        # mistakes traced walls for the real thing (capture stores them separately).
        sections = {k: 0.0 for k in sections}
    return MesoResult(sections=sections, batches=n_batches, skipped=skipped,
                      picks=picks_total, placements=placements_total,
                      reorders=reorders_total)


# ── micro: one phase at a time ────────────────────────────────────────────────

def run_micro(assets: ScenarioAssets, *, n_batches: int = 5, seed: int = 42,
              tracer=None) -> MesoResult:
    """Same loop, but intended for small n_batches with per-phase inspection — micro is
    meso with the sizes turned down; the tier exists so captures are labeled honestly."""
    return run_meso(assets, n_batches=n_batches, seed=seed, tracer=tracer)


# ── fullfid: the real worker, in-process ─────────────────────────────────────

def run_fullfid(*, tracer=None, n_batches: int = 4, max_skus: int = 300,
                strategy: str | None = None, log=None) -> dict:
    """Trace sr._run_strategy_worker itself (coverage_e2e driver shape, one arm).

    Requires a generated (inventory.db, affinity.db) pair under PROFILE_INPUT_DIR;
    raises ScenarioUnavailable otherwise. Section attribution for this tier comes from
    calltree_tracer.SECTION_MAP (the worker owns its own loop)."""
    import logging
    import queue as _queue
    import tempfile

    from Optimization import run_simulation as rs
    from Optimization.simdriver import strategy_runner as sr

    try:
        pairs = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)
    except Exception:
        pairs = []
    if not pairs:
        raise ScenarioUnavailable('no generated profile DB pair under PROFILE_INPUT_DIR')

    rs.CONFIG['global']['n_batches'] = n_batches
    label, inv_db, aff_db = pairs[0]
    base     = tempfile.mkdtemp(prefix='calltree_ff_')
    pair_dir = os.path.join(base, label)
    os.makedirs(pair_dir, exist_ok=True)
    log = log or logging.getLogger('calltree.fullfid')

    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=max_skus, max_bins=20000, min_bins=5000,
        keyframe_interval=0,
        warehouse_db_path=os.path.join(pair_dir, 'warehouse.db'))

    q = _queue.Queue()
    _mixed, _channel_runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = _channel_runs[0]
    strategy_args, skeletons = rs._prepare_channel_run(ch, cfg, _mixed, shared, pair_dir, log)
    if strategy is not None:
        strategy_args = [a for a in strategy_args if a.get('strategy') == strategy] \
            or strategy_args[:1]
    else:
        strategy_args = strategy_args[:1]
    a = strategy_args[0]
    a['log_queue'] = q

    if tracer is not None:
        tracer.start()
    try:
        result = sr._run_strategy_worker(a)
    finally:
        if tracer is not None:
            tracer.stop()
    for sk in skeletons:
        rs._finalize_config_run(sk)
    return {'base': base, 'arm': a.get('strategy'), 'worker_result': result}


# ── macro: a real run's own numbers ──────────────────────────────────────────

# bench_sections' short names -> the t_* vocabulary. 'build' is smpl+task and is dropped
# so the sum stays a partition (no double count).
_MACRO_KEYMAP = {'reord': 't_reord', 'smpl': 't_sample', 'task': 't_task', 'pre': 't_pre',
                 'inv': 't_inv', 'sim': 't_sim', 'extr': 't_extract', 'db': 't_save'}


def macro_sections(run_log: str | None = None) -> dict:
    """Per-checkpoint mean section walls from a real run's log (bench_sections parser).

    No tree — real runs are not traced. Returns {'sections': {t_*: mean_s},
    'checkpoints': N, 'source': path}. Raises ScenarioUnavailable without a log."""
    import statistics as _st

    import bench_sections as bsec   # Tests/bench, on sys.path via the bootstrap above

    log_path = run_log or bsec._latest_log()
    if log_path is None or not os.path.isfile(log_path):
        raise ScenarioUnavailable('no comparison run.log found — pass --run-log explicitly')
    rows = bsec.parse(log_path)
    if not rows:
        raise ScenarioUnavailable(f'no checkpoint section lines parsed from {log_path}')
    sections = {t_name: _st.fmean(d[short] for _, d in rows)
                for short, t_name in _MACRO_KEYMAP.items()}
    return {'sections': sections, 'checkpoints': len(rows),
            'source': os.path.basename(os.path.dirname(log_path)) + '/run.log'}
