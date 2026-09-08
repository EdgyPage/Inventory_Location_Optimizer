"""sim_assets.py — per-pair shared-asset construction for a run.

build_shared_assets loads the inventory+affinity pair once, sizes the warehouse
(Inventory_Manager.plan_warehouse with the per-regime sizing from CONFIG), persists
the planned inventory + warehouse stats (rename-proof fingerprint), and returns the
picklable shared dict every strategy worker receives.  Split out of run_simulation
so the runner holds orchestration only; run_analysis imports this directly for its
shape-only rebuild.
"""
import json
import logging
import os
import random
import time

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.generation.generate_inventory import load_inventory_from_db, save_inventory_to_db
from Warehouse.inventory.Inventory_Management import LoadParams, Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import uniform_aisle_bins
from Warehouse.layout.Storage_Primitive import viable_storage_units as _vsu
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.picking.Workload_Builder import BatchConfig

from Optimization.config.sim_config import (
    CONFIG, seed_world, _AISLE_W, _AISLE_H, _CATEGORIES, _HANDLINGS, store_fill,
    staffing_spec, work_day_spec,
)

_HERE = os.path.dirname(os.path.abspath(__file__))   # recovered_params.json lives here


def load_run_inventory(path: str, limit: int | None = None):
    """The ONE loader both the parent (`build_shared_assets`) and the workers
    (`strategy_runner`) use for a run's inventory.

    It is a thin pass-through today, and the reason it survives as a named seam is the reason
    it stopped branching.  It used to CLEAR the `pipeline_qty` stamp on a flag-off load, so the
    manager's `rp x lead / (lead + 1)` heuristic stood byte for byte whatever file was handed
    in.  That guard existed because the era was the only regime that declared its own levels;
    flag-off inherited the catalogue's authored ones and had to stay byte-identical with the
    archive.  Since ADR-0002 there is ONE planner contract: every run declares its levels at
    setup, in days, and stamps the lead pipeline that goes with them ("Field the floor",
    decision 6).  Honouring the stamp in one mode and discarding it in the other would field a
    level whose reorder point encodes a LINE while pricing its pipeline by a heuristic that
    assumes the reorder point encodes lead-time demand -- the exact defect the stamp was
    introduced to fix.  The era flag now decides only whether the clock cuts and caps.
    """
    return load_inventory_from_db(path, limit=limit)


# ── shared asset loader ────────────────────────────────────────────────────────

def build_shared_assets(
    inventory_db      : str,
    affinity_db       : str,
    log               : logging.Logger,
    max_skus          : int | None = None,
    max_aisles        : int | None = None,
    max_bins          : int | None = None,
    min_bins          : int | None = None,
    composition       : dict | None = None,
    regime_sizing     : dict | None = None,
    keyframe_interval : int = CONFIG['global']['keyframe_interval'],
    warehouse_db_path : str | None = None,
    frozen_inventory_db : str | None = None,
    coverage_record   : dict | None = None,
) -> dict:
    """Load inventory + affinity from DB and build warehouse A.

    Warehouse is sized so total bins ≥ N_SKUS × 1.1 (minimum replicas of the
    60-type layout satisfying that constraint).

    frozen_inventory_db (what-if harness): when set, load THIS already-planned inventory and
    DO NOT re-sample — every scenario/cell shares one frozen sampled inventory, so the batch
    fingerprint (which depends on the sampled SKUs' freq/qty) is identical across layout cells.
    The warehouse SHAPE still comes from regime_sizing (the cell's aisle_split/zoning), only the
    sampled inventory is held fixed.  None ⇒ current behavior (byte-identical).

    coverage_record (the REBUILD path): one finished run's own
    `staffing.calibration[<pair>].coverage` block.  A rebuild loads that run's CATALOGUE, which
    since ADR-0002 carries no stock level, and the warehouse is sized from levels on every path
    -- so a rebuild must re-declare before it plans, and must declare what the RUN declared.
    Passing the record re-declares from it exactly (`era_coverage.declare_from_record`); passing
    None on a path that samples is normal (the run derives its own), and on a path that does not
    means "the orders already carry a declaration", which is true of a frozen inventory.
    """
    _src_db = frozen_inventory_db or inventory_db
    log.info(f'  Loading inventory  : {_src_db}'
             + ('  (frozen)' if frozen_inventory_db else '')
             + (f'  (limit {max_skus:,} SKUs)' if max_skus else ''))
    t0        = time.perf_counter()
    inventory = load_run_inventory(_src_db, limit=max_skus)
    n_skus    = len(inventory.orders)
    log.info(f'  {n_skus:,} orders  ({time.perf_counter()-t0:.2f}s)')

    # ── Warehouse sizing — delegated to Inventory_Manager.plan_warehouse ──────
    # Sizes per-(handling, category, size_tier, unit_type) uniform aisles from
    # the actual inventory (every bucket gets ≥1 aisle so every SKU is placeable),
    # then samples SKUs to fill to store_fill().  All sizing/sampling lives in
    # the Warehouse layer — run_simulation just supplies the shape + constraints.
    t_size = time.perf_counter()
    # The SUPPLY side only: a loaded catalogue carries no stock level to average (ADR-0002).
    # The levels this run DECLARES are logged by the coverage loop below and averaged onto
    # `warehouse_stats` further down, off the planned orders.
    log.info(f'  Inventory model  : {n_skus:,} SKUs'
             f'  avg lead_time={sum(getattr(c,"lead_time_mean",0.0) for c in inventory.orders)/max(n_skus,1):.2f}'
             f'  avg supply_cv={sum(getattr(c,"supply_cv",0.0) for c in inventory.orders)/max(n_skus,1):.3f}')

    _sample = warehouse_db_path is not None and frozen_inventory_db is None

    def _plan():
        return Inventory_Manager.plan_warehouse(
            inventory.orders,
            categories   = _CATEGORIES,
            handlings    = _HANDLINGS,
            aisle_width  = _AISLE_W,
            aisle_height = _AISLE_H,
            target_fill  = store_fill(),
            min_bins     = min_bins,
            max_bins     = max_bins,
            max_aisles   = max_aisles,
            composition  = composition,
            regime_sizing= regime_sizing,
            # Analysis (no warehouse_db_path) only needs the warehouse shape + aisle
            # maps, so skip the expensive inventory re-stock in that path.  A frozen inventory is
            # already sampled, so we only need the SHAPE (sample=False) and keep the frozen orders.
            sample       = _sample,
            rng          = random.Random(seed_world() + 1),
            log          = log,
        )

    def _build(cfg):
        # Build warehouse once in the main process only to extract aisle metadata maps
        # used by the analysis/plotting phase.  Workers rebuild from the same seed.
        Aisle.next_aisle_id = 1
        random.seed(seed_world())
        return Warehouse_Builder().from_config(cfg).build()

    # ── THE RUN'S STOCK DECLARATION: coverage in days, a pair-level fixed point ────────
    # Every SKU's stock levels are derived from its DAILY demand, which needs the fixed-point
    # line count, which needs the built geometry, which is sized from the stock levels -- so
    # plan/build/price iterate here (`era_coverage.fixed_point`; .scratch/department-calibration,
    # "Rescale stock coverage at setup").  In EVERY mode, not only under the era: the catalogue
    # carries no level to fall back on (ADR-0002), and the era flag decides only whether the
    # clock cuts and caps.  Flag-off the day the loop declares against is the reporting frame,
    # which `work_day_spec()['seconds']` already falls back to.
    #
    # Only where a plan SAMPLES.  An analysis-shape rebuild and a frozen inventory do not
    # declare: the frozen file carries the declaration its freeze made, and an analysis rebuild
    # re-declares from the run's OWN recorded line count instead of re-running the loop
    # (`declare_from_record`), because a rebuild must reproduce the run's warehouse, not
    # re-derive one.
    warehouse_meta = None
    era_stage_a: dict | None = None
    coverage: dict | None = None
    if _sample:
        if coverage_record is not None:
            raise ValueError(
                'a coverage_record was handed to a build that SAMPLES, which derives its own '
                'declaration and would silently ignore the record. A rebuild passes the record '
                'and no warehouse_db_path; a run passes a warehouse_db_path and no record.')
        from Optimization.simdriver import era_coverage as _era_cov          # noqa: E402
        _inputs = staffing_spec()
        _mixed, _specs = _era_cov.channel_specs(inventory)
        plan, warehouse_meta, _sa, coverage = _era_cov.fixed_point(
            inventory.orders, lambda: (lambda p: (p, _build(p.warehouse_cfg)))(_plan()),
            _specs, coverage_days=float(_inputs['coverage_days']),
            safety_days=float(_inputs['safety_days']),
            floor_lines=float(_inputs['floor_lines']), inputs=_inputs,
            day_seconds=float(work_day_spec()['seconds']), log=log)
        era_stage_a = {'channels': _sa, 'n_orders': len(plan.sampled or inventory.orders),
                       'aisles': len(warehouse_meta.aisles)}
    else:
        # A rebuild re-declares from the run's own record before planning; a frozen inventory
        # already carries the declaration its freeze wrote.  Neither re-derives.
        if coverage_record is not None:
            from Optimization.simdriver import era_coverage as _era_cov      # noqa: E402
            _mixed, _specs = _era_cov.channel_specs(inventory)
            _era_cov.declare_from_record(inventory.orders, _specs, coverage_record, log=log)
        elif not any(c.stock_declared() for c in inventory.orders):
            raise RuntimeError(
                f'{_src_db}: this inventory carries no stock declaration and none was handed '
                f'in, so the warehouse would be sized from nothing (ADR-0002 -- a catalogue '
                f"holds no level). A rebuild must pass `coverage_record=` (the run's own "
                f'`staffing.calibration[<pair>].coverage`); a run that samples derives its own.')
        plan = _plan()
    if plan.sampled:                 # empty when sample=False (analysis / frozen path)
        inventory.orders = plan.sampled
    n_skus             = len(inventory.orders)
    # Frozen inventory is already the sampled set ⇒ every SKU is stocked (allowlist = all).
    sku_allowlist      = ({c.sku for c in inventory.orders} if frozen_inventory_db
                          else plan.sku_allowlist)
    warehouse_cfg      = plan.warehouse_cfg
    total_aisles       = plan.total_aisles
    total_bins         = plan.total_bins
    expected_fill      = plan.expected_fill

    # Per-bucket pallet/singleton totals (for the warehouse_stats DB row).
    total_pallet_needed    = sum(n for (h, c, s, u), n
                                 in plan.capacity.items() if u == 'pallet')
    total_singleton_needed = sum(n for (h, c, s, u), n
                                 in plan.capacity.items() if u == 'singleton')
    # Storage units the fielded levels occupy -- only over orders a run has DECLARED a level
    # on (ADR-0002).  A shape-only rebuild off a catalogue declares none and the sum is 0,
    # which is what "no re-stock" means; the log line below already says so.
    total_units_needed     = sum(
        len(_vsu(c, c.equilibrium_qty))
        for c in (plan.sampled or inventory.orders) if c.stock_declared())

    log.info(f'  Warehouse : {total_aisles} aisles / {total_bins:,} bins'
             + (f'  {n_skus:,} SKUs sampled  expected_fill={expected_fill:.1%}'
                if plan.sampled else '  (shape only — analysis, no re-stock)')
             + f'  ({time.perf_counter()-t_size:.1f}s)')

    log.info(f'  Loading affinity DB : {affinity_db}')
    t0             = time.perf_counter()
    affinity_store = AffinityStore(affinity_db)
    n_aff_rows     = affinity_store._matrix.nnz if affinity_store._matrix is not None else 0
    mb             = (0 if affinity_store._matrix is None else
                      (affinity_store._matrix.data.nbytes +
                       affinity_store._matrix.indices.nbytes +
                       affinity_store._matrix.indptr.nbytes) / 1_048_576)
    log.info(f'  Affinity CSR ready : {n_aff_rows:,} entries  {mb:.0f} MB  '
             f'({time.perf_counter()-t0:.1f}s)')

    param_path = os.path.join(_HERE, 'recovered_params.json')
    if os.path.exists(param_path):
        with open(param_path) as _pf:
            p = json.load(_pf)
        load_params = LoadParams(lambda_=p['lambda_'], k=1.0, gamma=p['gamma'])
        log.info(f'  Params  λ={load_params.lambda_:.4f}  γ={load_params.gamma:.4f}')
    else:
        load_params = LoadParams(lambda_=1.1, k=1.0, gamma=1.5)
        log.info('  recovered_params.json not found — using defaults (λ=1.1  γ=1.5)')

    # Shared batch_cfg for the store-only path (= the store channel, so store's batch shape).
    # The mixed path builds each channel's own BatchConfig from its Channel fractions.
    _store_batch = CONFIG['channels']['store']['batch']
    batch_cfg = BatchConfig(
        inventory_size = n_skus,
        mean_fraction  = _store_batch['mean'],
        std_fraction   = _store_batch['std'],
        sampler        = CONFIG['global']['sampler'],
    )

    # Build warehouse once in the main process only to extract aisle metadata maps
    # used by the analysis/plotting phase.  Workers rebuild from the same seed.  The
    # coverage loop above already built the plan it settled on; nothing else has.
    if warehouse_meta is None:
        warehouse_meta = _build(warehouse_cfg)

    # ── persist the PLANNED inventory: THE RUN'S OWN STOCK DECLARATION (the levels this
    # run derived and the multi-tier stock_plan the planner packed them into) so worker
    # processes reproduce the exact cross-tier placement the warehouse was sized for.
    # Workers reload from this DB and NOT from the catalogue -- which since ADR-0002 carries
    # no level at all, so a worker reading it would have nothing to stock, where before it
    # would merely palletize with the default scheme and explode the queue. ───────────
    planned_inv_db: str | None = None
    _pair_dir = (os.path.dirname(os.path.abspath(warehouse_db_path))
                 if warehouse_db_path is not None else None)
    if _pair_dir is not None:
        os.makedirs(_pair_dir, exist_ok=True)   # dir may not exist yet (also used by stats below)
    if frozen_inventory_db is not None:
        # Frozen: every cell reuses the SAME already-planned inventory DB so workers load an
        # identical inventory ⇒ identical batch fingerprint across layout cells.
        planned_inv_db = frozen_inventory_db
        log.info(f'  Planned inventory -> {planned_inv_db}  (frozen, shared across cells)')
    elif warehouse_db_path is not None:
        planned_inv_db = os.path.join(_pair_dir, 'planned_inventory.db')
        if os.path.exists(planned_inv_db):
            os.remove(planned_inv_db)   # rewrite fresh each plan
        save_inventory_to_db(inventory, planned_inv_db,
                             {'source_inventory_db': inventory_db,
                              'planned': True})
        log.info(f'  Planned inventory -> {planned_inv_db}  ({n_skus:,} SKUs, '
                 f'cross-tier stock plans)')

    # ── persist warehouse stats and aisle distributions ───────────────────────
    warehouse_fp: str | None = None
    if warehouse_db_path is not None:
        from Optimization.persistence.Warehouse_Data import (init_warehouse_db, save_warehouse_stats,
                                     save_aisle_layout, compute_warehouse_fingerprint)
        # One aisle_type_stats row per bucket (handling, category, size, unit_type).
        # Uniform aisles → the bucket's tier is 100%, others 0%.
        _PCT_COL = {'small': 0, 'medium': 1, 'large': 2, 'extra_large': 3}
        from Warehouse.layout.Aisle_Dimensions import (catalog_aisle_bins, FULFILLMENT_BIN_WIDTH,
                                      FF_TIER_HEIGHTS, FULFILLMENT_AISLE_HEIGHT)
        aisle_rows = []
        for (h, cat, size, unit_type), cap_bins in plan.capacity.items():
            # Fulfillment buckets use the short-shelf catalog geometry (mirrors plan_warehouse._eff);
            # store buckets use the pallet/singleton geometry.
            if unit_type == 'fulfillment':
                eff = catalog_aisle_bins(FULFILLMENT_BIN_WIDTH, FF_TIER_HEIGHTS[size],
                                         _AISLE_W, FULFILLMENT_AISLE_HEIGHT)
            else:
                eff = uniform_aisle_bins(unit_type, size, _AISLE_W, _AISLE_H)
            rep = cap_bins // eff if eff else 0
            pcts = [0.0, 0.0, 0.0, 0.0]
            if unit_type == 'pallet' and size in _PCT_COL:
                pcts[_PCT_COL[size]] = 1.0
            aisle_rows.append(dict(
                handling_type      = h,
                category           = cat,
                unit_type          = unit_type,
                replica_count      = rep,
                eff_bins_per_aisle = eff,
                total_bins         = cap_bins,
                size_small_pct     = pcts[0],
                size_medium_pct    = pcts[1],
                size_large_pct     = pcts[2],
                size_xlarge_pct    = pcts[3],
            ))
        # The levels this run DECLARED, averaged for the stats row.  Undeclared orders (a
        # shape-only rebuild) contribute nothing rather than a fabricated 1.
        _declared = [c for c in inventory.orders if c.stock_declared()]
        avg_eq = sum(c.equilibrium_qty for c in _declared) / max(len(_declared), 1)
        avg_rp = sum(c.reorder_point   for c in _declared) / max(len(_declared), 1)
        # Per-aisle physical layout for reconstruction/visualization (and DB-only
        # analysis maps).  warehouse_meta is built from the same seed the workers
        # use, so aisle_ids match the task_stats / picker_events they record.  The same
        # rows seed the rename-proof fingerprint stamped on warehouse_stats AND every run.
        layout_rows = [
            dict(aisle_id      = a.aisle_id,
                 handling_type = a.handling_type,
                 category      = a.storage_type,
                 unit_type     = a.unit_type,
                 storage_size  = a.storage_size,
                 bay_x         = a.bayXPerAisle,
                 bay_y         = a.bayYPerAisle)
            for a in warehouse_meta.aisles
        ]
        warehouse_fp = compute_warehouse_fingerprint(
            layout_rows, os.path.basename(_pair_dir))
        # Effective caps for the stats row: when the per-regime path is used the legacy
        # max_bins/max_aisles are None (the real caps live in regime_sizing), so record the
        # summed per-regime caps instead (all-unset -> None).
        def _agg_cap(key):
            if regime_sizing:
                vals = [v for r in regime_sizing if (v := regime_sizing[r].get(key))]
                return sum(vals) if vals else None
            return {'max_bins': max_bins, 'max_aisles': max_aisles}[key]
        init_warehouse_db(warehouse_db_path)
        save_warehouse_stats(
            warehouse_db_path,
            inventory_db  = inventory_db,
            n_skus        = n_skus,
            n_pallet      = total_pallet_needed,
            n_singleton   = total_singleton_needed,
            total_aisles  = total_aisles,
            total_bins    = total_bins,
            expected_fill = expected_fill,
            target_fill   = store_fill(),    # store fill headroom (the sizing target)
            max_aisles    = _agg_cap('max_aisles'),
            max_bins      = _agg_cap('max_bins'),
            avg_eq_qty    = avg_eq,
            avg_rp        = avg_rp,
            aisle_rows    = aisle_rows,
            warehouse_fingerprint = warehouse_fp,
        )
        save_aisle_layout(warehouse_db_path, layout_rows)
        log.info(f'  Warehouse stats  -> {warehouse_db_path}'
                 f'  ({len(warehouse_meta.aisles)} aisles, fp={warehouse_fp})')

    return dict(
        inventory          = inventory,
        inv_db             = inventory_db,
        aff_db             = affinity_db,
        affinity_store     = affinity_store,
        batch_cfg          = batch_cfg,
        load_params        = load_params,
        warehouse_cfg      = warehouse_cfg,
        total_aisles       = total_aisles,
        total_bins         = total_bins,
        total_units_needed = total_units_needed,
        aisle_unittype_map = {a.aisle_id: a.unit_type     for a in warehouse_meta.aisles},
        aisle_handling_map = {a.aisle_id: a.handling_type for a in warehouse_meta.aisles},
        # No picker count here: the analysis reads its crew off the staffing record stamped
        # onto sim_result (run_analysis._sim_result_from_meta), per channel -- a store-only
        # slice of shared assets was the wrong number for every fulfillment leaf.
        max_skus           = max_skus,
        max_aisles         = max_aisles,
        max_bins           = max_bins,
        sku_allowlist      = sku_allowlist,
        planned_inv_db     = planned_inv_db,
        keyframe_interval  = keyframe_interval,
        warehouse_meta     = warehouse_meta,
        warehouse_fingerprint = warehouse_fp,
        # The coverage loop's record and its last stage-A pricing (era only; both None
        # flag-off, on a frozen inventory and on an analysis-shape rebuild).  The
        # derivation reuses the pricing and records the loop under `calibration`.
        coverage           = coverage,
        era_stage_a        = era_stage_a,
    )
