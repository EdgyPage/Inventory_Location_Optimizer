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

from Warehouse.Aisle_Storage import Aisle
from Warehouse.Affinity_Store import AffinityStore
from Warehouse.generation.generate_inventory import load_inventory_from_db, save_inventory_to_db
from Warehouse.Inventory_Management import LoadParams, Inventory_Manager
from Warehouse.Aisle_Dimensions import uniform_aisle_bins
from Warehouse.Storage_Primitive import viable_storage_units as _vsu
from Warehouse.Warehouse_Builder import Warehouse_Builder
from Warehouse.Workload_Builder import BatchConfig

from Optimization.sim_config import (
    CONFIG, SEED_WORLD, _AISLE_W, _AISLE_H, _CATEGORIES, _HANDLINGS, _INITIAL_FILL,
)

_HERE = os.path.dirname(os.path.abspath(__file__))   # recovered_params.json lives here


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
    keyframe_interval : int = 5,
    warehouse_db_path : str | None = None,
) -> dict:
    """Load inventory + affinity from DB and build warehouse A.

    Warehouse is sized so total bins ≥ N_SKUS × 1.1 (minimum replicas of the
    60-type layout satisfying that constraint).
    """
    log.info(f'  Loading inventory  : {inventory_db}'
             + (f'  (limit {max_skus:,} SKUs)' if max_skus else ''))
    t0        = time.perf_counter()
    inventory = load_inventory_from_db(inventory_db, limit=max_skus)
    n_skus    = len(inventory.orders)
    log.info(f'  {n_skus:,} orders  ({time.perf_counter()-t0:.2f}s)')

    # ── Warehouse sizing — delegated to Inventory_Manager.plan_warehouse ──────
    # Sizes per-(handling, category, size_tier, unit_type) uniform aisles from
    # the actual inventory (every bucket gets ≥1 aisle so every SKU is placeable),
    # then samples SKUs to fill to _INITIAL_FILL.  All sizing/sampling lives in
    # the Warehouse layer — run_simulation just supplies the shape + constraints.
    t_size = time.perf_counter()
    avg_eq = sum(c.equilibrium_qty for c in inventory.orders) / max(n_skus, 1)
    log.info(f'  Inventory model  : avg equilibrium_qty={avg_eq:.1f}'
             f'  avg reorder_point={sum(c.reorder_point for c in inventory.orders)/max(n_skus,1):.1f}'
             f'  avg lead_time={sum(getattr(c,"lead_time_mean",0.0) for c in inventory.orders)/max(n_skus,1):.2f}'
             f'  avg supply_cv={sum(getattr(c,"supply_cv",0.0) for c in inventory.orders)/max(n_skus,1):.3f}')

    plan = Inventory_Manager.plan_warehouse(
        inventory.orders,
        categories   = _CATEGORIES,
        handlings    = _HANDLINGS,
        aisle_width  = _AISLE_W,
        aisle_height = _AISLE_H,
        target_fill  = _INITIAL_FILL,
        min_bins     = min_bins,
        max_bins     = max_bins,
        max_aisles   = max_aisles,
        composition  = composition,
        regime_sizing= regime_sizing,
        # Analysis (no warehouse_db_path) only needs the warehouse shape + aisle
        # maps, so skip the expensive inventory re-stock in that path.
        sample       = warehouse_db_path is not None,
        rng          = random.Random(SEED_WORLD + 1),
        log          = log,
    )
    if plan.sampled:                 # empty when sample=False (analysis path)
        inventory.orders = plan.sampled
    n_skus             = len(inventory.orders)
    sku_allowlist      = plan.sku_allowlist
    warehouse_cfg      = plan.warehouse_cfg
    total_aisles       = plan.total_aisles
    total_bins         = plan.total_bins
    expected_fill      = plan.expected_fill

    # Per-bucket pallet/singleton totals (for the warehouse_stats DB row).
    total_pallet_needed    = sum(n for (h, c, s, u), n
                                 in plan.capacity.items() if u == 'pallet')
    total_singleton_needed = sum(n for (h, c, s, u), n
                                 in plan.capacity.items() if u == 'singleton')
    total_units_needed     = sum(
        len(_vsu(c, c.equilibrium_qty)) for c in plan.sampled)

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
    )

    # Build warehouse once in the main process only to extract aisle metadata maps
    # used by the analysis/plotting phase.  Workers rebuild from the same seed.
    Aisle.next_aisle_id = 1
    random.seed(SEED_WORLD)
    warehouse_meta = Warehouse_Builder().from_config(warehouse_cfg).build()

    # ── persist the PLANNED inventory (grown equilibrium_qty + multi-tier
    # stock_plan) so worker processes reproduce the exact cross-tier placement
    # the warehouse was sized for.  Workers reload from this DB instead of the
    # original, otherwise they palletize with the default scheme and the queue
    # explodes (tiers the warehouse was sized for never get filled). ───────────
    planned_inv_db: str | None = None
    if warehouse_db_path is not None:
        _pair_dir = os.path.dirname(os.path.abspath(warehouse_db_path))
        os.makedirs(_pair_dir, exist_ok=True)   # dir may not exist yet
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
        from Optimization.Warehouse_Data import (init_warehouse_db, save_warehouse_stats,
                                     save_aisle_layout, compute_warehouse_fingerprint)
        # One aisle_type_stats row per bucket (handling, category, size, unit_type).
        # Uniform aisles → the bucket's tier is 100%, others 0%.
        _PCT_COL = {'small': 0, 'medium': 1, 'large': 2, 'extra_large': 3}
        from Warehouse.Aisle_Dimensions import (catalog_aisle_bins, FULFILLMENT_BIN_WIDTH,
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
        avg_eq = sum(c.equilibrium_qty for c in inventory.orders) / max(n_skus, 1)
        avg_rp = sum(c.reorder_point   for c in inventory.orders) / max(n_skus, 1)
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
            target_fill   = _INITIAL_FILL,   # store fill headroom (the sizing target)
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
        # Store picker pool — read by run_analysis's slim EvalContext (Performance_Evaluations).
        k_pickers          = CONFIG['channels']['store']['num_pickers'],
        max_skus           = max_skus,
        max_aisles         = max_aisles,
        max_bins           = max_bins,
        sku_allowlist      = sku_allowlist,
        planned_inv_db     = planned_inv_db,
        keyframe_interval  = keyframe_interval,
        warehouse_meta     = warehouse_meta,
        warehouse_fingerprint = warehouse_fp,
    )
