"""
calltree_scenarios.py — seeded, deterministic scenario tiers for the calltree framework.

Every scenario here fires REAL reorder-time placement. That sentence earns its caps:
`Tests/bench/perf_simulation.py::_build_inventory` hands back a pure CATALOGUE — geometry
and demand, the four stock-level slots UNSET (ADR-0002: a level is a run's declaration,
never a SKU's fact). A tool built directly on it and never declaring one used to get
silence (`_notify_pick` read `reorder_point` through a `getattr` default of None, flagged
nothing, and the entire t_reord section — every assignment function — never executed);
today it gets `inventory_common.UndeclaredStock` from the first pick instead. This module
wraps the same builders and then DECLARES a production-shaped level per order
(`set_reorder_fields` -> `Order.declare_stock`) and stocks to that equilibrium, so
depletion actually crosses the reorder point. The production engine is
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

from Inbound.dock import Dock as _Dock, DockSpec as _DockSpec
# The standing-yard construction set.  Imported at module level rather than lazily because
# `Inbound/__init__.py` already imports `gain` for its registration side effect, so the cost is
# paid by the first `import Inbound.dock` above regardless -- a lazy import here would buy
# nothing and hide the dependency.  `Tests/` may import `Inbound/` freely; the architecture rule
# runs the other way (no Warehouse module may import Inbound).
from Inbound.gain import GAIN_POLICIES as _GAIN_POLICIES, OneOwnerBundle as _OneOwnerBundle
from Inbound.pack import packer as _inbound_packer
from Inbound.receiving import SiteReceiving as _SiteReceiving
from Inbound.space import SpaceTimeline as _SpaceTimeline
from Inbound.trailer import TRAILER_TYPES as _TRAILER_TYPES
from Inbound.transit import YardTransit as _YardTransit
from Warehouse.inventory.put_queue import (
    PutQueueSet as _PutQueueSet, PutQueueSpec as _PutQueueSpec,
    store_and_fulfillment as _store_and_fulfillment)
from Warehouse.kernel.cost_model import SpeedProfile as _SpeedProfile
from Warehouse.layout.Storage_Primitive import (
    FulfillmentCart as _FulfillmentCart, StoreCart as _StoreCart)
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task, drain_sku as _drain_sku
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
    """DECLARE the PRODUCTION-SHAPED equilibrium/reorder level on every order so placement
    fires with production scaling.

    THIS SCENARIO IS THE RUN. The catalogue carries no levels (ADR-0002), so nothing is
    stocked, sized or reordered until something declares one; in production that is the
    coverage derivation at setup (`Optimization/simconfig/coverage.rescale_section`, driven
    by `simdriver/era_coverage.fixed_point`), and here it is this function. The numbers are
    the ones the generator used to author, i.e. the retired
    `EQUILIBRIUM_COVERAGE_BATCHES`/`REORDER_SAFETY_BATCHES` model:
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

    The write goes through `Order.declare_stock`, the ONE mutation site for the four level
    slots, which owns the clamps this function used to spell out itself (Q ≥ 1; rp ≥ 1 and
    at most Q−1, with rp = 1 the only option when Q = 1) and clears `stock_plan` /
    `pipeline_qty`. Same arithmetic, same numbers, one writer — 18 test files reach this
    helper through `build_assets(coverage=, safety=)`, so the signature and the levels it
    fields are load-bearing and must not drift.

    `expected_batch_demand`, `lead_time_mean` and `supply_cv` stay direct assignments: they
    are demand and supply facts of the SKU, not a level, and `Order.__init__` (the
    construction path `_build_inventory` uses) leaves all three unset.

    The seed parameter is kept for API stability (production draws no randomness here).
    """
    del seed  # deterministic: a pure function of each order's demand
    for c in orders:
        expected = c.demand.relative_frequency * c.demand.quantity_rate
        eq = max(1, round(coverage * expected))
        c.expected_batch_demand = expected
        c.lead_time_mean        = float(lead_time)
        c.supply_cv             = float(supply_cv)
        c.declare_stock(eq, round(expected * (lead_time + safety)))


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


def _warm_the_fit_caches(orders) -> None:
    """Fill `Storage_Primitive`'s geometry `lru_cache`s for every SKU x every tier, before
    any tracer starts.

    THE FRAMEWORK'S DETERMINISM GATE DEPENDS ON THIS, and used to get it by accident.  The
    tracer counts real calls, and `_tiered_fit_dims` / `_singleton_fit_dims` are cached — so
    a call counted in one capture is a cache hit (uncounted) in the next, and two same-seed
    captures disagree.  The retired `sample_to_capacity._reachable` enumerated every SKU
    against every tier while planning, which warmed the caches broadly before the first
    capture; `field_requirement` touches only the tiers each SKU actually lands in, so the
    warming has to be asked for.  Doing it here keeps the counts REAL (the alternative,
    excluding the cached leaves from `counts_fingerprint`, hides work the tracer exists to
    measure).
    """
    from Warehouse.layout.Storage_Primitive import (      # noqa: E402
        Pallet, Singleton, FulfillmentBin, _max_qty_fits)
    from Warehouse.inventory.inventory_common import (    # noqa: E402
        _max_qty_fitting_size, _SIZES_DESCENDING, _FF_SIZES_DESCENDING)
    from Warehouse.kernel.regime import FULFILLMENT, regime_of   # noqa: E402
    for c in orders:
        if regime_of(c) == FULFILLMENT:
            for size in _FF_SIZES_DESCENDING:
                _max_qty_fitting_size(c, size, FULFILLMENT)
            _max_qty_fits(c, FulfillmentBin)
            continue
        for size in _SIZES_DESCENDING:
            _max_qty_fitting_size(c, size, 'pallet')
        _max_qty_fits(c, Pallet)
        _max_qty_fits(c, Singleton)


def build_assets(*, n_skus: int = 2_000, bins_per_aisle: int = 100,
                 n_pickers: int = 10, seed: int = 42, target_fill: float = 0.85,
                 strategy: str = DEFAULT_STRATEGY,
                 coverage: float = 10.0, safety: float = 2.0,
                 put_timing: bool = False, put_split: bool = False,
                 put_staging: int | None = None, put_crew: int = 1,
                 recv_crew: int = 0,
                 inbound: bool = False, trailer_type: str = '53', dock_doors: int = 4,
                 lead_minutes: float = 0.0, lead_spread: float = 0.0, lead_seed: int = 0,
                 yard_policy: str = 'fifo', dock_policy: str = 'fifo',
                 local_policy: str = 'fifo', trailer_bound: int | None = None,
                 crew_allocation: str = 'split', door_team: int | None = None,
                 fee_threshold_days: float = 2.0,
                 urgency_horizon_days: float = 0.0) -> ScenarioAssets:
    """Deterministic single-arm assets with production placement wiring.

    Mirrors Diagnostics/trace_lifecycle.py's recipe (plan_warehouse to a target fill,
    StrategyContext + the strategies-registry build), with the stock level DECLARED BEFORE
    sampling so every sampled order carries one. coverage/safety default to production's
    equilibrium model (10/2); fast tests shrink coverage AND scale safety with it
    (safety ≈ 2·coverage/10) to keep the rp/eq fraction production-shaped.

    The order of the next two statements is a CONTRACT, not a style: `plan_warehouse`'s very
    first act is `bucket_requirements`, which runs every order through
    `inventory_common._equilibrium_qty` to size each bin bucket — and that raises
    `UndeclaredStock` on an order no run has declared a level for (ADR-0002). Declaring
    after the plan would therefore not merely mis-size the warehouse, it would not build one
    at all. `field_requirement` then records the packing on each order, at the quantity it
    was declared for -- it neither grows nor shrinks the level, so the rp/eq ratio set here
    is the one the run fields.
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
        target_fill=target_fill)
    _warm_the_fit_caches(plan.sampled)
    inventory = Inventory(plan.sampled)
    affinity  = _build_affinity_store(inventory, top_k=20, seed=seed)

    pick_cfg = PickConfig(num_pickers=n_pickers, x_speed=1.0, y_speed=0.5,
                          pick_intercept=1.0, pick_weight_coef=1.1,
                          pick_volume_coef=1e-3, cart_swap_coef=10.0)
    wp = WorkloadParams.from_pick_config(pick_cfg)
    for c in inventory.orders:
        c.compute_labor_cost(wp.pick_intercept, wp.pick_weight_coef, wp.pick_volume_coef,
                             pick_per_item=wp.pick_per_item)

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
        # `sources` matters once a trailer pipeline is bound: the driver widens it to
        # ('reorder', 'trailer') whenever an inbound spec exists (strategy_runner.py:1646),
        # and the default ('reorder',) would leave trailer-sourced items uncaught.
        mgr.enable_receiving(_Dock(_DockSpec(
            size=recv_crew,
            sources=('reorder', 'trailer') if inbound else ('reorder',))))

    # ── the STANDING YARD, off by default ─────────────────────────────────────────
    # Mirrors the driver's own single-leaf construction site (strategy_runner.py:1703-1791)
    # rather than paraphrasing it: transit, timeline, packer, coordinator, and the gain bundle
    # only when a gain policy is actually named.  The off state is "none of this was built",
    # the same structural no-op the put-away block above uses.
    #
    # WHY THIS REACHES ANYTHING AT ALL: `_receive` reroutes to the coordinator whenever the
    # bound transit carries `STANDING` (inventory_reorder.py:675), and that branch takes ONE
    # leaf -- `self.receiving.receive((self,), deadline)`.  So a single uncoupled manager
    # driving `check_reorders` executes the whole standing drain: plans-at-arrival, the ctx
    # freeze, the yard and dock rankings, `plan_order`, the unload and the handoff.  No
    # coupling, no `bind`, so `site_scoped` stays False and the per-batch accessors the meso
    # loop calls keep working.
    if inbound:
        if not recv_crew:
            raise ValueError(
                'inbound=True needs a receiving crew: a standing yard nobody can unload '
                'stands merchandise forever, the run completes, and nothing raises. '
                '`sim_config.inbound_spec` refuses the same combination for the same reason')
        if lead_spread > 0.0 and lead_minutes <= 0.0:
            raise ValueError(
                'lead_spread > 0 with lead_minutes == 0 is SILENTLY INERT: the draw is '
                'median * exp(sigma*Z) and 0 * anything is 0, so the spread would be '
                'configured and never observable (run_simulation.py:819 refuses it too)')
        mgr.transit = _YardTransit(
            _TRAILER_TYPES[trailer_type],
            lead_s=lead_minutes * 60.0,      # authored in MINUTES, stored in SECONDS, once
            lead_sigma=lead_spread, lead_seed=lead_seed,
            doors=dock_doors, yard_policy=yard_policy, dock_policy=dock_policy,
            local_policy=local_policy, bound=trailer_bound,
            allocation=crew_allocation, door_team=door_team)
        # The timeline rides the yard unconditionally, as it does in the driver: every drain's
        # DockContext carries a frozen SpaceView even under fifo/fifo, which is what keeps a
        # policy comparison a comparison of POLICIES.  The drain rule is injected because the
        # import edge Inbound -> wh_picking is forbidden.
        _SpaceTimeline(_drain_sku).attach(mgr)
        mgr.packer = _inbound_packer
        mgr.receiving = _SiteReceiving(mgr._dock, mgr.transit)
        # The gain bundle only when an arm consumes it -- the seeded fifo/lifo keys never read
        # it, so building it unconditionally would be unconsumed infra.  `_gain_bundle_for` is
        # the DRIVER's builder, imported rather than reimplemented: a second implementation
        # would drift from the arm it claims to be faithful to, which is the whole premise of
        # the gain family.  It reads only these two keys off its spec argument.
        if {yard_policy, dock_policy} & _GAIN_POLICIES:
            from Optimization.simdriver.strategy_runner import _gain_bundle_for
            mgr.transit.gain_bundle = _OneOwnerBundle(_gain_bundle_for(
                strat, mgr, ctx, wp, _SpeedProfile(2.0, 4.0),
                {'fee_threshold_days': fee_threshold_days,
                 'urgency_horizon_days': urgency_horizon_days}))

    return ScenarioAssets(
        inventory=inventory, affinity=affinity, warehouse=warehouse, mgr=mgr,
        pick_cfg=pick_cfg, wp=wp, batch_cfg=batch_cfg, strategy=strategy,
        sizes={'n_skus': n_skus, 'n_skus_sampled': len(inventory.orders),
               'bins_per_aisle': bins_per_aisle, 'n_bins': len(warehouse.bins),
               'n_aisles': len(warehouse.aisles), 'n_pickers': n_pickers,
               'target_fill': target_fill,
               'inbound': bool(inbound), 'trailer_type': trailer_type if inbound else None,
               'dock_doors': dock_doors if inbound else None,
               'yard_policy': yard_policy if inbound else None,
               'dock_policy': dock_policy if inbound else None,
               # Recorded because a bound makes `plan_order` CONSTANT-TIME (priorities.py:208
               # slices the candidates), so a ladder run under one measures the bound and not
               # the greedy.  A reader of an archived artifact has to be able to tell.
               'trailer_bound': trailer_bound if inbound else None,
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
    # THE ABSOLUTE CLOCK, and it is a CORRECTNESS requirement, not a nicety.
    #
    # `check_reorders` assigns `self._now_s = now_s` unconditionally (inventory_reorder.py:876)
    # and `now_s` defaults to None.  This loop used to omit it, so `mgr._now_s` was None on every
    # drain — which is harmless while the bound transit is a `BatchTransit`, and silently fatal
    # the moment a trailer pipeline is bound:
    #   * `Trailer.arrived` returns False whenever `now_s is None` (trailer.py:193), so a trailer
    #     with ANY positive lead NEVER arrives.  Measured before this fix: 73 trailers stuck in
    #     `_in_transit` over 10 batches, zero unloads, and a ladder that reported no cost at all.
    #   * a lead-0 trailer does arrive, but stamps `arrived_s = None` — and every standing
    #     priority key reads that stamp.  `_fifo_standing` / `_lifo_standing` (priorities.py:93,
    #     :105) both degenerate to a constant, making fifo and lifo a byte-identical unload
    #     stream, and `gain_gated`'s urgency filter `t.arrived_s is not None` (gain.py:1121)
    #     admits nobody at any threshold.
    # Either way the fixture looks healthy and measures nothing, which is this repo's documented
    # worst failure mode.  Advancing by the batch makespan mirrors the driver closely enough for
    # arrival ordering; it is NOT a release schedule, so absolute-day quantities (the yard fee)
    # are not answerable from this loop.
    arm_clock = 0.0

    for i in range(n_batches):
        with sec('t_reord'):
            triggered = mgr.check_reorders(put_deadline=put_deadline,
                                           recv_deadline=recv_deadline,
                                           now_s=arm_clock)
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
            bs = extract_batch_stats(events, batch_id=i,
                                     k_pickers=assets.pick_cfg.num_pickers,
                                     run_id='calltree')
            extract_task_stats(events, tasks, batch_id=i, affinity=assets.affinity,
                               wp=assets.wp, run_id='calltree', lift_cache=lift_cache)
            extract_picker_events(events, batch_id=i, run_id='calltree')
            picks_b = extract_picks(events, batch_id=i, run_id='calltree')
        picks_total += sum(p.quantity for p in picks_b)
        # `duration` is the batch MAKESPAN (first picker starting to last finishing), which is
        # the span this batch actually occupied.  Advancing by it is what lets the next drain's
        # arrivals be ordered against the previous one.  A skipped batch (`continue` above)
        # advances nothing, which is right: no work happened.
        arm_clock += float(bs.duration or 0.0)

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

def _catalogue_skus(inv_db: str) -> int | None:
    """How many SKUs this catalogue DECLARES, from its own run_metadata.

    The generator stamps `num_skus` into `run_metadata.params_json`, so this reads the
    declaration rather than counting rows. That matters beyond speed: the declaration is
    what the catalogue was ASKED for, and a count would silently agree with a truncated or
    half-written table. Falls back to a count only when the stamp is absent (pre-contract
    catalogues), and to None when even that fails -- a None means "unknown", which callers
    must not read as "big enough".
    """
    import json
    import sqlite3
    try:
        con = sqlite3.connect('file:' + inv_db.replace(os.sep, '/') + '?mode=ro', uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "select value from run_metadata where key = 'params_json'").fetchone()
        if row:
            n = (json.loads(row[0]) or {}).get('num_skus')
            if isinstance(n, int) and n > 0:
                return n
        return con.execute('select count(distinct sku) from cartons').fetchone()[0]
    except (sqlite3.Error, ValueError, TypeError, IndexError):
        return None
    finally:
        con.close()


def _pick_pair(rs, min_catalogue: int | None, log):
    """(label, inv_db, aff_db, declared_skus) -- the pair a rung will actually bind.

    `min_catalogue=None` is the historical behaviour EXACTLY: the latest profile run first
    pair, whatever size it is. That keeps every existing caller byte-identical.

    With a floor, the DECLARATION picks the fixture instead of the fixture truncating the
    declaration. This was not a hypothetical: the latest catalogue holds 40,000 SKUs, and a
    coupled ladder run at rungs 40k / 60k / 80k produced three runs identical to the row --
    132 place_load calls, 1,282 pools, yard depth 2.25, drain within 3% -- and printed them
    as three rungs of a growth ladder. `max_skus` above the catalogue is not an error and
    not a warning; it simply takes everything, which is indistinguishable in the output from
    a subsystem that stopped growing. Refusing is the only honest answer, and it is the same
    rule the planner already applies to a bin cap that binds.

    Runs are searched newest-first, so the smallest sufficient catalogue is NOT preferred --
    the most RECENT sufficient one is. Size is a capability here, not the thing being
    selected on, and silently reaching for an older catalogue would change the generator
    vintage underneath a comparison.
    """
    from Schema.profile_resolver import ProfileTree
    if min_catalogue is None:
        pairs = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)
        if not pairs:
            raise ScenarioUnavailable('no generated profile DB pair under PROFILE_INPUT_DIR')
        label, inv_db, aff_db = pairs[0]
        return label, inv_db, aff_db, _catalogue_skus(inv_db)

    pt = ProfileTree(rs._DEFAULT_PROFILES_DIR)
    seen = []
    for run in sorted(pt.runs(), reverse=True):
        for label, inv_db, aff_db in pt.pairs(run) or []:
            n = _catalogue_skus(inv_db)
            seen.append((label, n))
            if n is not None and n >= min_catalogue:
                if log is not None:
                    log.info('catalogue %s declares %s SKUs (rung floor %s)',
                             label, n, min_catalogue)
                return label, inv_db, aff_db, n
    best = max((n for _l, n in seen if n is not None), default=None)
    have = f'the largest declares {best:,}' if best else 'no catalogue declares a SKU count'
    raise ScenarioUnavailable(
        f'no catalogue under PROFILE_INPUT_DIR serves {min_catalogue:,} SKUs -- {have}. '
        f'Generate one, or lower the top rung: a rung above the catalogue silently '
        f're-runs the rung at it.')


def run_fullfid(*, tracer=None, n_batches: int = 4, max_skus: int = 300,
                strategy: str | None = None, coupled: bool = False,
                min_catalogue: int | None = None, log=None) -> dict:
    """Trace sr._run_strategy_worker itself (coverage_e2e driver shape, one arm).

    Requires a generated (inventory.db, affinity.db) pair under PROFILE_INPUT_DIR;
    raises ScenarioUnavailable otherwise. Section attribution for this tier comes from
    calltree_tracer.SECTION_MAP (the worker owns its own loop)."""
    import logging
    import queue as _queue
    import tempfile

    from Optimization import run_simulation as rs
    from Optimization.simdriver import strategy_runner as sr

    rs.CONFIG['global']['n_batches'] = n_batches
    label, inv_db, aff_db, cat_skus = _pick_pair(rs, min_catalogue, log)
    base     = tempfile.mkdtemp(prefix='calltree_ff_')
    pair_dir = os.path.join(base, label)
    os.makedirs(pair_dir, exist_ok=True)
    log = log or logging.getLogger('calltree.fullfid')

    # NO `max_bins` / `min_bins`.  A cap that binds below what the run's DECLARED levels need
    # REFUSES the plan (`UnfieldableRequirement`) rather than fielding less — the same edit the
    # e2e fixtures took when levels became a declaration.  This tier carried `max_bins=20000`
    # and had therefore been DEAD since that change, silently, because nothing gates it.
    #
    # And no cap value would have saved it.  `era_coverage.fixed_point` (sim_assets.py:190)
    # declares before it converges, and its SEED round sizes ~11x the plan it settles on —
    # 898,700 bins before settling at 77,500 at max_skus=300 — so any cap under ~900k refuses on
    # round 1 whatever the final warehouse costs.  `coverage_days` and `max_skus` are the size
    # knobs here; a bin cap is not one.
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=max_skus,
        keyframe_interval=0,
        warehouse_db_path=os.path.join(pair_dir, 'warehouse.db'))

    q = _queue.Queue()
    _mixed, _channel_runs = rs._channel_runs_for(shared['inventory'])
    # THE CALIBRATED ERA, STAGE B.  `build_shared_assets` above already runs stage A — the
    # coverage fixed point — in every mode, which is why the era LOOKED reachable from this
    # tier.  It is not: the crews are derived separately, and `_build_work_units` does it right
    # here (workunits.py:1360-1363).  This tier calls `_prepare_channel_run` directly and so
    # skipped the block entirely, leaving `shared['staffing']` without a derived block; the
    # worker's `_check_declared_crew` then asked `channel_crew` for a crew that is in neither
    # the derived block nor the declared key, and `staffing.py:383` refused every era arm with
    #     KeyError: the staffing record carries no picking crew for channel 'store'
    #
    # WHY THIS MATTERS ENOUGH TO MIRROR THE DRIVER RATHER THAN SKIP THE ERA: the era is what
    # supplies the RECEIVING DAY.  `strategy_runner.py:1676-1682` takes
    # `_recv_day = _WorkDay(length=_wd['seconds'] or _shift_seconds)` on the `_drain_or_cap`
    # branch and never consults `RECV_DAY_SECONDS` — so without stage B there is no whistle,
    # and without a whistle `_unload_split`'s single non-exhaustion exit empties the yard
    # inside every drain.  Every yard depth this tier reported before this line existed was a
    # NO-WHISTLE FLOOR, not a measurement of the configuration the campaign runs.
    #
    # Called, not paraphrased: a second implementation of a staffing derivation is the exact
    # drift this repo punishes, and the crews are site totals over BOTH channels' scripts.
    if rs.era_on():
        from Optimization.simdriver import workunits as _wu
        _channel_runs, _derived, _cal = _wu._derive_staffing_for_pair(
            shared, _channel_runs, _mixed, pair_dir, log)
        shared['staffing'] = {'derived': _derived, 'calibration': _cal}
    if coupled:
        # THE SITE MODEL, which is what the campaign actually runs: one dock, one receiving
        # crew and one pool of putters over TWO channels.  `_prepare_site_run` returns the same
        # shape `_prepare_channel_run` does, so nothing below changes -- what it adds is the
        # pairing, the site crews lifted to unit scope, and both group keys on the unit.
        #
        # UNCOUPLED IS THE QUIETER HALF OF THE SITE, and that is why this option exists: the
        # default path takes `_channel_runs[0]`, which `workunits.py:1256` guarantees is the
        # STORE -- and store binds 14-17 of 75 drains against fulfillment's 46-60.  Every
        # inbound number this tier produced before `coupled=True` existed described that half.
        #
        # Its refusals are loud and worth knowing: it needs exactly the store and fulfillment
        # channels (so a single-channel catalogue raises), and exactly one config per channel --
        # pairing two config SETS is an undecided question it will not answer with a `zip`.
        from Optimization.simdriver import workunits as _wu
        unit_args, skeletons = _wu._prepare_site_run(
            _channel_runs, _mixed, shared, pair_dir, log)
        if strategy is not None:
            # Under coupling an arm is a PAIR and the leaves carry their own strategies, so
            # match on either leaf; falling back to the first unit keeps the tier runnable
            # rather than empty when a name does not appear.
            unit_args = [u for u in unit_args
                         if any(lf.get('strategy') == strategy
                                for lf in (u.get('leaves') or [u]))] or unit_args[:1]
        else:
            unit_args = unit_args[:1]
        a = unit_args[0]
    else:
        ch, cfg = _channel_runs[0]
        strategy_args, skeletons = rs._prepare_channel_run(ch, cfg, _mixed, shared,
                                                          pair_dir, log)
        if strategy is not None:
            strategy_args = [x for x in strategy_args
                             if x.get('strategy') == strategy] or strategy_args[:1]
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
    # THE DERIVED STAFFING BLOCK comes back with the result, because rho is the only honest
    # x axis for an inbound ladder and it is not recoverable afterwards: `staffing.derive`
    # solved `crew_size(load, S, rho_recv)` for THIS pair, the answer lives on `shared`, and
    # `shared` dies with this call.  Recomputing it in the caller would be a second
    # implementation of a derivation this repo already punishes drifting.
    return {'base': base, 'arm': a.get('strategy'), 'worker_result': result,
            'staffing': shared.get('staffing'),
            'shift_seconds': rs.CONFIG['global'].get('shift_seconds')
                             or rs.CONFIG['global'].get('work_day_seconds'),
            'coupled': bool(coupled),
            # The catalogue is part of the measurement, not the environment: a rung above
            # it is a re-run of the rung at it, and nothing downstream can tell without
            # these two.
            'catalogue': label, 'catalogue_skus': cat_skus,
            'saturated': bool(cat_skus is not None and max_skus > cat_skus)}


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
