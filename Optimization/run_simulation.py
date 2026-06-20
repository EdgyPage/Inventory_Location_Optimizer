"""
Warehouse assignment strategy simulation runner.

Runs A/B/C strategy workers for each (pair × regression-config) combination
and saves all results to per-strategy SQLite DBs.  Graph generation is a
separate step: run_analysis.py <base_dir>.

Modes:
  python run_simulation.py                          # new run, sequential
  python run_simulation.py --workers 15             # flat pool, no idle
  python run_simulation.py --resume <base_dir>      # resume a crashed run
"""

import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import multiprocessing
import os
import pickle
import random
import sys
import time
from datetime import datetime

# ── path setup ────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, _HERE)

# ── .env support ──────────────────────────────────────────────────────────────
# Reads <repo_root>/.env and injects KEY=VALUE pairs into os.environ.
# No external packages required.  Shell-set variables are never overwritten.
# Recognised variables:
#   COMPARISON_OUTPUT_DIR  — parent directory for comparison_<ts>/ output folders
#   PROFILE_INPUT_DIR      — root directory for inventory+affinity DB pairs
def _load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _, _val = _line.partition('=')
            _key = _key.strip()
            _val = _val.strip()
            # Strip optional r"..." / r'...' raw-string notation and plain quotes
            if _val.startswith(('r"', "r'")):
                _val = _val[2:].rstrip('"').rstrip("'")
            else:
                _val = _val.strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

_load_env(os.path.join(_REPO_ROOT, '.env'))

from Aisle_Storage import Aisle
from Affinity_Store import AffinityStore
from generation.generate_inventory import load_inventory_from_db, save_inventory_to_db
from Inventory_Management import LoadParams, Inventory_Manager
from strategies import STRATEGIES
from Pick import PickConfig, DEFAULT_HEIGHT_BRACKETS
from Aisle_Dimensions import aisle_width_for, aisle_height_for, uniform_aisle_bins
from Storage_Primitive import viable_storage_units as _vsu
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import BatchConfig

from Picking_Data import create_run, init_run_db
from Workload import WorkloadParams

from strategy_runner import (
    load_worker_checkpoint, _run_strategy_worker, _cleanup_checkpoints,
)

# ── simulation constants ───────────────────────────────────────────────────────
SEED_WORLD       = 42
SEED_BATCHES     = 1337
N_BATCHES        = 100
K_PICKERS        = 25
_CHECKPOINT      = max(1, N_BATCHES // 10)
_WIN             = 50
_BATCH_MEAN_FRAC = 0.15
_BATCH_STD_FRAC  = 0.05
_TARGET_FILL  = 0.85   # headroom fraction: size each aisle type to this utilization
_INITIAL_FILL = 0.85   # target fill when sampling inventory to fit a capped aisle count

# Physical aisle dimensions: 25 pallet-width columns × 30 extra_large-height levels.
# Actual bin counts per aisle depend on unit type and size distribution.
_AISLE_W = aisle_width_for(50)    # 50 × 48 = 2400 physical units
_AISLE_H = aisle_height_for(10)   # 10 × 48 = 480 physical units


def _clean_path(val: str) -> str:
    """Strip r\"...\" / r'...' notation or plain quotes from an env-var path value.

    Applied after os.getenv so that values set directly in the Windows session
    environment (with literal r\"...\" text) are normalised the same way as
    values parsed from the .env file.
    """
    if val.startswith(('r"', "r'")):
        return val[2:].rstrip('"').rstrip("'")
    return val.strip('"').strip("'")

_OUTPUT_DIR = _clean_path(os.getenv(
    'COMPARISON_OUTPUT_DIR',
    _HERE,
))
_DEFAULT_PROFILES_DIR = _clean_path(os.getenv(
    'PROFILE_INPUT_DIR',
    os.path.normpath(os.path.join(_REPO_ROOT, 'Warehouse', 'generated', 'profiles')),
))

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_HANDLINGS  = ['conveyable', 'non-conveyable']

# Warehouse layout is no longer a static table — Inventory_Manager.plan_warehouse
# builds per-(handling, category, size_tier, unit_type) uniform aisles sized to
# the actual inventory, guaranteeing every bucket exists (≥1 aisle) so every
# SKU is placeable.  See Warehouse/Inventory_Management.py.


REGRESSION_CONFIGS = [
    {
        'name'            : 'calibrated',
        'pick_intercept'  : 30,
        'pick_weight_coef': 0.233666,
        'pick_volume_coef': 0.0294014,
        'cart_swap_coef'  : 500,
        'x_speed'         : 0.0833333,
        'y_speed'         : 0.0416667,
        'height_brackets' : ((96.0, 1.0), (240.0, 1.6), (float('inf'), 2.4)),
    },
    {
        'name'            : 'calibrated_height_weight',
        'pick_intercept'  : 30,
        'pick_weight_coef': 0.233666*2,
        'pick_volume_coef': 0.0294014,
        'cart_swap_coef'  : 500,
        'x_speed'         : 0.0833333,
        'y_speed'         : 0.0416667,
        'height_brackets' : ((96.0, 1.0), (240.0, 1.6), (float('inf'), 2.4)),
    },
]


# ── logging ────────────────────────────────────────────────────────────────────

def _setup_logging(log_path: str) -> logging.Logger:
    log = logging.getLogger('comparison')
    log.setLevel(logging.INFO)
    # %(name)-14s gives a fixed-width column so A/B/C worker labels align with
    # the main-process 'comparison' label in the same log file.
    fmt = logging.Formatter(
        '%(asctime)s  %(name)-14s  %(message)s',
        datefmt='%H:%M:%S',
    )
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


# ── resume helpers ─────────────────────────────────────────────────────────────

def _resume_path(run_dir: str) -> str:
    return os.path.join(run_dir, 'resume.pkl')


def _save_resume(run_dir: str, run_ids: dict, starts: dict) -> None:
    """Persist per-strategy batch counters and run IDs for crash recovery.

    run_ids and starts are keyed by strategy key (e.g. 'uniform', 'trip_min').
    """
    state = {'run_ids': run_ids, 'next_batch': dict(starts)}
    with open(_resume_path(run_dir), 'wb') as f:
        pickle.dump(state, f)


def _load_resume(run_dir: str):
    path = _resume_path(run_dir)
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return pickle.load(f)


# ── DB helpers ─────────────────────────────────────────────────────────────────


def discover_db_pairs(profiles_dir: str) -> list[tuple[str, str, str]]:
    """Scan profiles_dir and return (label, inventory_db, affinity_db) for every valid pair."""
    pairs: list[tuple[str, str, str]] = []
    if not os.path.isdir(profiles_dir):
        return pairs
    for run_name in sorted(os.listdir(profiles_dir)):
        run_path = os.path.join(profiles_dir, run_name)
        if not os.path.isdir(run_path):
            continue
        for profile_name in sorted(os.listdir(run_path)):
            profile_path = os.path.join(run_path, profile_name)
            if not os.path.isdir(profile_path):
                continue
            inv_db = os.path.join(profile_path, 'inventory', 'inventory.db')
            aff_db = os.path.join(profile_path, 'affinity', 'affinity.db')
            if os.path.exists(inv_db) and os.path.exists(aff_db):
                pairs.append((f'{run_name}__{profile_name}', inv_db, aff_db))
    return pairs


def find_latest_db_pairs(profiles_dir: str) -> list[tuple[str, str, str]]:
    """Return DB pairs from the most recently generated profile run only.

    Profile run directories are named profile_YYYYMMDD_HHMMSS (or the legacy
    batch_YYYYMMDD_HHMMSS), so the last entry when sorted lexicographically is
    always the newest.  Walks backwards until a run with valid pairs is found.
    """
    if not os.path.isdir(profiles_dir):
        return []
    run_names = sorted([
        d for d in os.listdir(profiles_dir)
        if os.path.isdir(os.path.join(profiles_dir, d))
    ])
    for run_name in reversed(run_names):
        run_path = os.path.join(profiles_dir, run_name)
        pairs: list[tuple[str, str, str]] = []
        for profile_name in sorted(os.listdir(run_path)):
            profile_path = os.path.join(run_path, profile_name)
            if not os.path.isdir(profile_path):
                continue
            inv_db = os.path.join(profile_path, 'inventory', 'inventory.db')
            aff_db = os.path.join(profile_path, 'affinity', 'affinity.db')
            if os.path.exists(inv_db) and os.path.exists(aff_db):
                pairs.append((f'{run_name}__{profile_name}', inv_db, aff_db))
        if pairs:
            return pairs
    return []


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
    n_skus    = len(inventory.cartons)
    log.info(f'  {n_skus:,} cartons  ({time.perf_counter()-t0:.2f}s)')

    # ── Warehouse sizing — delegated to Inventory_Manager.plan_warehouse ──────
    # Sizes per-(handling, category, size_tier, unit_type) uniform aisles from
    # the actual inventory (every bucket gets ≥1 aisle so every SKU is placeable),
    # then samples SKUs to fill to _INITIAL_FILL.  All sizing/sampling lives in
    # the Warehouse layer — run_simulation just supplies the shape + constraints.
    t_size = time.perf_counter()
    avg_eq = sum(c.equilibrium_qty for c in inventory.cartons) / max(n_skus, 1)
    log.info(f'  Inventory model  : avg equilibrium_qty={avg_eq:.1f}'
             f'  avg reorder_point={sum(c.reorder_point for c in inventory.cartons)/max(n_skus,1):.1f}'
             f'  avg lead_time={sum(getattr(c,"lead_time_mean",0.0) for c in inventory.cartons)/max(n_skus,1):.2f}'
             f'  avg supply_cv={sum(getattr(c,"supply_cv",0.0) for c in inventory.cartons)/max(n_skus,1):.3f}')

    plan = Inventory_Manager.plan_warehouse(
        inventory.cartons,
        categories   = _CATEGORIES,
        handlings    = _HANDLINGS,
        aisle_width  = _AISLE_W,
        aisle_height = _AISLE_H,
        target_fill  = _INITIAL_FILL,
        min_bins     = min_bins,
        max_bins     = max_bins,
        max_aisles   = max_aisles,
        composition  = composition,
        # Analysis (no warehouse_db_path) only needs the warehouse shape + aisle
        # maps, so skip the expensive inventory re-stock in that path.
        sample       = warehouse_db_path is not None,
        rng          = random.Random(SEED_WORLD + 1),
        log          = log,
    )
    if plan.sampled:                 # empty when sample=False (analysis path)
        inventory.cartons = plan.sampled
    n_skus             = len(inventory.cartons)
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

    batch_cfg = BatchConfig(
        inventory_size = n_skus,
        mean_fraction  = _BATCH_MEAN_FRAC,
        std_fraction   = _BATCH_STD_FRAC,
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
    if warehouse_db_path is not None:
        from Warehouse_Data import init_warehouse_db, save_warehouse_stats, save_aisle_layout
        # One aisle_type_stats row per bucket (handling, category, size, unit_type).
        # Uniform aisles → the bucket's tier is 100%, others 0%.
        _PCT_COL = {'small': 0, 'medium': 1, 'large': 2, 'extra_large': 3}
        aisle_rows = []
        for (h, cat, size, unit_type), cap_bins in plan.capacity.items():
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
        avg_eq = sum(c.equilibrium_qty for c in inventory.cartons) / max(n_skus, 1)
        avg_rp = sum(c.reorder_point   for c in inventory.cartons) / max(n_skus, 1)
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
            target_fill   = _TARGET_FILL,
            max_aisles    = max_aisles,
            max_bins      = max_bins,
            avg_eq_qty    = avg_eq,
            avg_rp        = avg_rp,
            aisle_rows    = aisle_rows,
        )
        # Per-aisle physical layout for reconstruction/visualization (and DB-only
        # analysis maps).  warehouse_meta is built from the same seed the workers
        # use, so aisle_ids match the task_stats / picker_events they record.
        save_aisle_layout(warehouse_db_path, [
            dict(aisle_id      = a.aisle_id,
                 handling_type = a.handling_type,
                 category      = a.storage_type,
                 unit_type     = a.unit_type,
                 storage_size  = a.storage_size,
                 bay_x         = a.bayXPerAisle,
                 bay_y         = a.bayYPerAisle)
            for a in warehouse_meta.aisles
        ])
        log.info(f'  Warehouse stats  -> {warehouse_db_path}'
                 f'  ({len(warehouse_meta.aisles)} aisles in aisle_layout)')

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
        k_pickers          = K_PICKERS,
        max_skus           = max_skus,
        max_aisles         = max_aisles,
        max_bins           = max_bins,
        sku_allowlist      = sku_allowlist,
        planned_inv_db     = planned_inv_db,
        keyframe_interval  = keyframe_interval,
        warehouse_meta     = warehouse_meta,
    )


# ── flat pool helpers ──────────────────────────────────────────────────────────

def _prepare_config_run(
    cfg     : dict,
    shared  : dict,
    pair_dir: str,
    log     : logging.Logger,
) -> tuple[list, dict]:
    """Pre-initialise one regression config: create DBs, get run_ids, build strategy_args.

    Builds every per-strategy work unit for one config so the flat pool can submit
    them all to one ProcessPoolExecutor.

    Returns (strategy_args_list, sim_result_skeleton).
    strategy_args_list: 3 dicts (A, B, C) ready for _run_strategy_worker.
      log_queue is NOT yet set — the caller injects it before submission.
    sim_result_skeleton: dict with name/run_dir/db_paths/run_ids + inv_db/aff_db.
    """
    name = cfg.get('name') or (
        f"w{cfg.get('pick_weight_coef',1.1)}_v{cfg.get('pick_volume_coef',1e-3)}"
        f"_i{cfg.get('pick_intercept',1.0)}_c{cfg.get('cart_swap_coef',10.0)}"
    )
    pick_cfg = PickConfig(
        num_pickers      = K_PICKERS,
        x_speed          = cfg.get('x_speed',          1.0),
        y_speed          = cfg.get('y_speed',          0.5),
        pick_intercept   = cfg.get('pick_intercept',   1.0),
        pick_weight_coef = cfg.get('pick_weight_coef', 1.1),
        pick_volume_coef = cfg.get('pick_volume_coef', 1e-3),
        cart_swap_coef   = cfg.get('cart_swap_coef',   10.0),
        height_brackets  = cfg.get('height_brackets',  DEFAULT_HEIGHT_BRACKETS),
    )
    wp      = WorkloadParams.from_pick_config(pick_cfg)
    run_dir = os.path.join(pair_dir, name)
    db_path = {s.key: os.path.join(run_dir, f'sim_{s.key}.db') for s in STRATEGIES}
    os.makedirs(run_dir, exist_ok=True)

    inventory          = shared['inventory']
    batch_cfg          = shared['batch_cfg']
    load_params        = shared['load_params']
    warehouse_cfg      = shared['warehouse_cfg']
    total_aisles       = shared['total_aisles']
    total_bins         = shared['total_bins']
    total_units_needed = shared['total_units_needed']
    warehouse_meta     = shared.get('warehouse_meta')

    # Yardstick: minimal achievable Sigma f*D for THIS config's speeds (pure
    # global-W optimum).  Computed once over the shared warehouse; identical across
    # strategies, so the plots can report each strategy's realised Sigma f*D as a
    # fraction of this optimum.  Cheap (sort, no placement, no mutation).
    optimal_sigma_fd = 0.0
    optimal_work = 0.0
    if warehouse_meta is not None and inventory.cartons:
        _freq = {c.sku: c.demand.frequency for c in inventory.cartons}
        _qty  = {c.sku: c.demand.quantity_rate for c in inventory.cartons}
        _mgr  = Inventory_Manager(warehouse_meta, affinity=None)
        optimal_sigma_fd = _mgr.optimal_sigma_fd(
            inventory.cartons, _freq, pick_cfg.x_speed, pick_cfg.y_speed)
        # Full-labor floor W* (travel + height handling) — the minimal-work yardstick.
        optimal_work = _mgr.optimal_work(inventory.cartons, _freq, _qty, wp)
        log.info(f'  Optimal Sigma f*D (yardstick) = {optimal_sigma_fd:,.1f}')
        log.info(f'  Optimal work W* (floor)       = {optimal_work:,.1f}')

    log.info(f'{"="*64}')
    log.info(f'  Config : {name}')
    log.info(f'  w={pick_cfg.pick_weight_coef}  v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  c={pick_cfg.cart_swap_coef}')
    log.info(f'{"="*64}')

    config_record = {
        'name'            : name,
        'pick_weight_coef': pick_cfg.pick_weight_coef,
        'pick_volume_coef': pick_cfg.pick_volume_coef,
        'pick_intercept'  : pick_cfg.pick_intercept,
        'cart_swap_coef'  : pick_cfg.cart_swap_coef,
        'x_speed'         : pick_cfg.x_speed,
        'y_speed'         : pick_cfg.y_speed,
        'num_pickers'     : pick_cfg.num_pickers,
        'load_lambda'     : load_params.lambda_,
        'load_k'          : load_params.k,
        'load_gamma'      : load_params.gamma,
        'total_aisles'    : total_aisles,
        'total_bins'      : total_bins,
        'n_skus'          : len(inventory.cartons),
        'total_units'     : total_units_needed,
        'bin_slack_pct'   : round((total_bins / max(total_units_needed, 1) - 1) * 100, 2),
        'batch_mean_frac' : _BATCH_MEAN_FRAC,
        'n_batches'       : N_BATCHES,
        'seed_world'      : SEED_WORLD,
        'seed_batches'    : SEED_BATCHES,
        'avg_equilibrium_qty': round(sum(getattr(c, 'equilibrium_qty', 1)
                                         for c in inventory.cartons) / max(len(inventory.cartons), 1), 1),
        'avg_reorder_point'  : round(sum(getattr(c, 'reorder_point', 1)
                                         for c in inventory.cartons) / max(len(inventory.cartons), 1), 2),
        'avg_lead_time_mean' : round(sum(getattr(c, 'lead_time_mean', 0.0)
                                         for c in inventory.cartons) / max(len(inventory.cartons), 1), 3),
        'avg_supply_cv'      : round(sum(getattr(c, 'supply_cv', 0.0)
                                         for c in inventory.cartons) / max(len(inventory.cartons), 1), 3),
    }
    with open(os.path.join(run_dir, 'config.json'), 'w') as f:
        json.dump(config_record, f, indent=2)

    keyframe_interval = int(shared.get('keyframe_interval', 5) or 0)
    # Run configuration recorded per run for reconstruction/replay.
    run_params = dict(
        num_pickers       = pick_cfg.num_pickers,
        x_speed           = pick_cfg.x_speed,
        y_speed           = pick_cfg.y_speed,
        pick_intercept    = pick_cfg.pick_intercept,
        pick_weight_coef  = pick_cfg.pick_weight_coef,
        pick_volume_coef  = pick_cfg.pick_volume_coef,
        cart_swap_coef    = pick_cfg.cart_swap_coef,
        k_pickers         = K_PICKERS,
        n_batches         = N_BATCHES,
        seed_world        = SEED_WORLD,
        keyframe_interval = keyframe_interval,
        optimal_sigma_fd  = optimal_sigma_fd,
        optimal_work      = optimal_work,
    )

    resume = _load_resume(run_dir)
    if resume:
        run_ids = resume['run_ids']
        prev    = resume.get('next_batch', {})
        starts  = {s.key: (load_worker_checkpoint(run_dir, s.key) or prev.get(s.key, 0))
                   for s in STRATEGIES}
        log.info('  Resuming  ' + '  '.join(f'{s.key}@{starts[s.key]}' for s in STRATEGIES))
    else:
        run_ids = {}
        for s in STRATEGIES:
            init_run_db(db_path[s.key])
            run_ids[s.key] = create_run(db_path[s.key], s.run_type, run_params)
        starts = {s.key: 0 for s in STRATEGIES}
        log.info('  New run  ' + '  '.join(f'{s.key}={run_ids[s.key]}' for s in STRATEGIES))

    _save_resume(run_dir, run_ids, starts)

    # Workers load the PLANNED inventory DB (grown equilibrium_qty + cross-tier
    # stock plans) when available so they reproduce the placement the warehouse
    # was sized for.  That DB already holds only the sampled SKUs at the planned
    # stock levels, so neither the SKU allowlist nor max_skus apply to it.
    _planned_db   = shared.get('planned_inv_db')
    _worker_invdb = _planned_db or shared['inv_db']
    _worker_allow = None if _planned_db else shared.get('sku_allowlist')
    _worker_maxsk = None if _planned_db else shared.get('max_skus')
    _shared = dict(
        inv_db        = _worker_invdb,
        aff_db        = shared['aff_db'],
        run_dir       = run_dir,
        n_batches     = N_BATCHES,
        k_pickers     = K_PICKERS,
        seed_world    = SEED_WORLD,
        seed_batches  = SEED_BATCHES,
        checkpoint    = _CHECKPOINT,
        max_skus      = _worker_maxsk,
        sku_allowlist = _worker_allow,
        keyframe_interval = keyframe_interval,
        warehouse_cfg = warehouse_cfg,
        pick_cfg      = pick_cfg,
        wp            = wp,
        load_params   = load_params,
        batch_cfg     = batch_cfg,
        # log_queue is NOT set here — injected by the flat pool (_run_workers_flat)
    )
    strategy_args = [
        {**_shared, 'strategy': s.key, 'run_id': run_ids[s.key],
         'start_i': starts[s.key], 'db_path': db_path[s.key]}
        for s in STRATEGIES
    ]
    # Profile (inventory) label + per-strategy decomposition (initial | assignment |
    # reslot, split from the strategy label) so the graphs can title plots as
    # inventory_initial_assignment_reslot without re-parsing the registry.
    _profile = os.path.basename(pair_dir.rstrip('/\\')) or 'profile'

    def _decomp(lbl: str) -> dict:
        parts = (lbl.split('|') + ['', '', ''])[:3]
        return dict(initial=parts[0], assignment=parts[1], reslot=parts[2])

    sim_skeleton = dict(
        name       = name,
        inventory  = _profile,
        run_dir    = run_dir,
        strategies = [dict(key=s.key, label=s.label, color=s.color,
                           db_path=db_path[s.key], run_id=run_ids[s.key],
                           **_decomp(s.label))
                      for s in STRATEGIES],
        optimal_sigma_fd = optimal_sigma_fd,
        optimal_work     = optimal_work,
        inv_db     = shared['inv_db'],
        aff_db     = shared['aff_db'],
    )
    return strategy_args, sim_skeleton


def _finalize_config_run(sim_skeleton: dict) -> dict:
    """Post-completion: remove resume file and write sim_meta.json.

    Returns the sim_result dict (subset of sim_skeleton without inv_db/aff_db).
    """
    run_dir = sim_skeleton['run_dir']
    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)
    _cleanup_checkpoints(run_dir)   # config complete — clear its per-strategy _ckpt_*.pkl
    # Additive runs: if a sim_meta.json already exists (e.g. a --resume run adding
    # a NEW strategy into a prior comparison dir), MERGE the strategy lists instead
    # of overwriting, so run_analysis sees the previously-run strategies plus the
    # new one.  Strategies are de-duplicated by key (the new run wins on collision).
    meta_path = os.path.join(run_dir, 'sim_meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as _f:
                prev = json.load(_f)
        except (OSError, ValueError):
            prev = {}
        new_keys = {s['key'] for s in sim_skeleton.get('strategies', [])}
        kept     = [s for s in prev.get('strategies', []) if s.get('key') not in new_keys]
        sim_skeleton = {**prev, **sim_skeleton,
                        'strategies': kept + sim_skeleton.get('strategies', [])}
    with open(meta_path, 'w') as _f:
        json.dump(sim_skeleton, _f, indent=2)
    return {k: sim_skeleton[k]
            for k in ('name', 'inventory', 'run_dir', 'strategies',
                      'optimal_sigma_fd', 'optimal_work')
            if k in sim_skeleton}


def _run_workers_flat(
    pairs              : list,
    base_dir           : str,
    shared_by_pair     : dict,
    max_workers        : int,
    log                : logging.Logger,
    max_tasks_per_child: int | None = 1,
    skip_completed     : bool = False,
) -> None:
    """Flat ProcessPoolExecutor pool — zero idle time between A/B/C barriers.

    All (pair, config, strategy) work units are submitted to a single shared
    pool.  A free worker immediately picks up the next unit regardless of
    which pair or config it belongs to.  When all 3 strategies for a
    (pair, config) complete, sim_meta.json is written.

    Graph generation is decoupled: run run_analysis.py <base_dir> afterwards.
    """
    mp_manager = multiprocessing.Manager()
    log_queue  = mp_manager.Queue(-1)
    listener   = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True
    )
    listener.start()
    log.info('  Log listener started')

    try:
        # ── pre-init: build all work units in (pair, config) order ───────────
        work_units: list[tuple[tuple, dict]] = []   # ((label, cfg_name), args_dict)
        meta: dict[tuple, dict] = {}                # key → {sim_skeleton, remaining}

        for label, inv_db, aff_db in pairs:
            pair_dir = os.path.join(base_dir, label)
            shared   = shared_by_pair[label]

            for cfg in REGRESSION_CONFIGS:
                cfg_name = cfg.get('name', '?')
                key = (label, cfg_name)
                # On resume, a config that finalized has written sim_meta.json and had
                # its resume marker removed (_finalize_config_run).  Skip it so resume
                # never recomputes already-complete work — _prepare_config_run would
                # otherwise see no resume.pkl and restart it from batch 0.
                run_dir = os.path.join(pair_dir, cfg_name)
                if skip_completed and os.path.exists(os.path.join(run_dir, 'sim_meta.json')) \
                        and not os.path.exists(_resume_path(run_dir)):
                    log.info(f'  [{label}/{cfg_name}] already complete — skipping (resume)')
                    continue
                try:
                    strategy_args, sim_skeleton = _prepare_config_run(
                        cfg, shared, pair_dir, log)
                    for sa in strategy_args:
                        sa['log_queue'] = log_queue   # inject shared queue
                        work_units.append((key, sa))
                    meta[key] = {'sim_skeleton': sim_skeleton, 'remaining': len(STRATEGIES)}
                except Exception as exc:
                    log.error(f'  [{label}/{cfg_name}] prepare FAILED: {exc}',
                              exc_info=True)

        # Assign a 1-based job index + identity tag to each work unit so every
        # worker log line can show which job of the flat list is progressing.
        total_jobs = len(work_units)
        for idx, (key, sa) in enumerate(work_units, start=1):
            _label, _cfg_name = key
            sa['job_index'] = idx
            sa['job_total'] = total_jobs
            sa['job_tag']   = f'{_label}/{_cfg_name}/{sa["strategy"]}'

        # max_tasks_per_child recycles each worker process after this many jobs so
        # the OS reclaims its full RSS between simulations.  CPython rarely returns
        # freed arenas to the OS, so without recycling each long-lived worker's RSS
        # ratchets to its high-water mark and pins memory at 95%+, swap-thrashing
        # every worker's DB writes.  None = never recycle (legacy behaviour).
        recycle = max_tasks_per_child if max_tasks_per_child and max_tasks_per_child > 0 else None
        log.info(f'  Flat pool: {total_jobs} jobs'
                 f' -> ProcessPoolExecutor({max_workers}, '
                 f'max_tasks_per_child={recycle})')

        # ── execute ───────────────────────────────────────────────────────────
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=max_workers, max_tasks_per_child=recycle) as pool:
            futures = {
                pool.submit(_run_strategy_worker, args): key
                for key, args in work_units
            }
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                label, cfg_name = key
                try:
                    res = fut.result()
                    log.info(f'  [{label}/{cfg_name}] strategy-{res["strategy"]} done'
                             f'  batches={res["done"]}  wall={res["elapsed"]:.1f}s')
                except Exception as exc:
                    log.error(f'  [{label}/{cfg_name}] strategy FAILED: {exc}',
                              exc_info=True)

                if key in meta:
                    meta[key]['remaining'] -= 1
                    if meta[key]['remaining'] <= 0:
                        try:
                            _finalize_config_run(meta[key]['sim_skeleton'])
                            log.info(f'  [{label}/{cfg_name}] sim_meta.json written')
                        except Exception as exc:
                            log.error(f'  [{label}/{cfg_name}] finalize FAILED: {exc}',
                                      exc_info=True)
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Warehouse assignment comparison — uses the newest generated inventory+affinity pair.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--profiles-dir', default=_DEFAULT_PROFILES_DIR,
                        help='Root directory produced by generate_profile_suite.py')
    parser.add_argument('--all-profiles', action='store_true',
                        help='Run every profile pair instead of only the newest')
    parser.add_argument('--resume', metavar='BASE_DIR', default=None,
                        help='Resume a previous run by passing its base directory')
    parser.add_argument('--workers', type=int, default=1, metavar='N',
                        help='Flat ProcessPoolExecutor pool size — every '
                             '(pair,config,strategy) work unit shares one pool. '
                             'Default 1 (sequential).')
    parser.add_argument('--max-tasks-per-child', type=int, default=1, metavar='N',
                        help='Recycle each pool worker after N jobs so the OS reclaims '
                             'its full memory between simulations (default 1 = fresh '
                             'process per job, the cleanest flush). Workers reload all '
                             'assets per job anyway, so spawn cost is negligible vs '
                             'multi-hour jobs. Raise it for fewer spawns at the cost of '
                             'flush completeness; 0 disables recycling (legacy).')
    parser.add_argument('--max-skus', type=int, default=None, metavar='N',
                        help='Cap inventory to the first N SKUs (smaller warehouse for quick runs)')
    parser.add_argument('--max-aisles', type=int, default=None, metavar='N',
                        help='Cap total aisle count by proportionally scaling replica counts')
    parser.add_argument('--max-bins', type=int, default=None, metavar='N',
                        help='Cap total bins by trimming aisle replicas (floor: '
                             '1 replica/type ~= 30k bins). Combine with --max-aisles.')
    parser.add_argument('--min-bins', type=int, default=None, metavar='N',
                        help='Require AT LEAST N total bins; replicas scale up to '
                             'meet it (min wins over --max-bins if they conflict).')
    parser.add_argument('--composition', type=str, default=None, metavar='JSON',
                        help='Path to a JSON file (or inline JSON) giving a factored '
                             'basis vector of bin ratios. Keys: handling, category, '
                             'size, unit — each a {value: weight} map. Bins are '
                             'allocated proportionally; scale comes from --min-bins.')
    parser.add_argument('--keyframe-interval', type=int, default=5, metavar='K',
                        help='Write a full bin snapshot to <run>.keyframes.db every K '
                             'batches so the visualizer can jump between batches '
                             '(0 disables). A keyframe = all occupied bins; raise K for '
                             'very large warehouses. Default 5.')
    args = parser.parse_args()

    composition = None
    if args.composition:
        if os.path.exists(args.composition):
            with open(args.composition) as _cf:
                composition = json.load(_cf)
        else:
            composition = json.loads(args.composition)

    if args.resume:
        base_dir = args.resume if os.path.isabs(args.resume) else os.path.join(_OUTPUT_DIR, args.resume)
        if not os.path.isdir(base_dir):
            sys.exit(f'Resume directory not found: {base_dir}')
    else:
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_dir = os.path.join(_OUTPUT_DIR, f'comparison_{ts}')
        os.makedirs(base_dir, exist_ok=True)

    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    log.info(f'Output directory : {base_dir}')
    log.info(f'Profiles dir     : {args.profiles_dir}')
    log.info(f'Mode             : {"all profiles" if args.all_profiles else "latest profile only"}')

    if args.all_profiles:
        pairs = discover_db_pairs(args.profiles_dir)
    else:
        pairs = find_latest_db_pairs(args.profiles_dir)

    if not pairs:
        sys.exit(f'No inventory+affinity DB pairs found in: {args.profiles_dir}')

    # Sanity-check: each inv_db path must be unique.  Duplicate paths indicate
    # a broken profile directory structure and would cause misleading results.
    seen_inv: dict[str, str] = {}
    for label, inv_db, _aff in pairs:
        if inv_db in seen_inv:
            log.warning(f'  DUPLICATE inv_db detected!')
            log.warning(f'    first seen as : {seen_inv[inv_db]}')
            log.warning(f'    repeated as   : {label}')
            log.warning(f'    path          : {inv_db}')
        else:
            seen_inv[inv_db] = label

    n_unique = len(seen_inv)
    log.info(f'Discovered {len(pairs)} DB pair(s)  ({n_unique} unique inventories):')
    for label, inv_db, aff_db in pairs:
        log.info(f'  {label}')
        log.info(f'    inv : {inv_db}')
        log.info(f'    aff : {aff_db}')

    n_configs = len(REGRESSION_CONFIGS)
    n_strats  = len(STRATEGIES)
    total_workers = len(pairs) * n_configs * n_strats
    workers = args.workers or 1
    log.info(
        f'Execution plan: {len(pairs)} pair(s) × {n_configs} config(s) × {n_strats} strategies'
        f' = {total_workers} work units  |  flat pool workers={workers}'
    )
    # ── flat ProcessPoolExecutor: every (pair,config,strategy) unit shares one pool ──
    shared_by_pair = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
        shared_by_pair[label] = build_shared_assets(
            inv_db, aff_db, log,
            max_skus=args.max_skus, max_aisles=args.max_aisles,
            max_bins=args.max_bins, min_bins=args.min_bins,
            composition=composition, keyframe_interval=args.keyframe_interval,
            warehouse_db_path=os.path.join(base_dir, label, 'warehouse.db'),
        )
    _run_workers_flat(pairs, base_dir, shared_by_pair, workers, log,
                      max_tasks_per_child=args.max_tasks_per_child,
                      skip_completed=bool(args.resume))

    log.info(f'\nAll {len(pairs)} dataset(s) × {n_configs} config(s) simulations complete.'
             f'  Root: {base_dir}'
             f'\n  Run graphs: python run_analysis.py {base_dir}')


if __name__ == '__main__':
    main()
