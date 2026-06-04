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
from generate_inventory import load_inventory_from_db
from Inventory_Management import LoadParams, Inventory_Manager
from Pick import PickConfig
from Aisle_Dimensions import aisle_width_for, aisle_height_for, uniform_aisle_bins
from Storage_Primitive import viable_storage_units as _vsu
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import BatchConfig

from Picking_Data import create_run, init_run_db
from Workload import WorkloadParams

from strategy_runner import (
    run_strategies_parallel, load_worker_checkpoint, _run_strategy_worker,
)

# ── simulation constants ───────────────────────────────────────────────────────
SEED_WORLD       = 42
SEED_BATCHES     = 1337
N_BATCHES        = 100
K_PICKERS        = 25
_CHECKPOINT      = max(1, N_BATCHES // 10)
_WIN             = 50
_BATCH_MEAN_FRAC = 0.25
_BATCH_STD_FRAC  = 0.03
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
        'name'            : 'baseline',
        'pick_weight_coef': 1.1,
        'pick_volume_coef': 1e-3,
        'pick_intercept'  : 1.0,
        'cart_swap_coef'  : 10.0,
    },
    {
        'name'            : 'high_weight',
        'pick_weight_coef': 2.5,
        'pick_volume_coef': 1e-3,
        'pick_intercept'  : 1.0,
        'cart_swap_coef'  : 10.0,
    },
    {
        'name'            : 'high_cart_penalty',
        'pick_weight_coef': 1.1,
        'pick_volume_coef': 1e-3,
        'pick_intercept'  : 1.0,
        'cart_swap_coef'  : 25.0,
    },
    #{
    #    'name'            : 'high_cart_weight_penalty',
    #    'pick_weight_coef': 2.5,
    #    'pick_volume_coef': 1e-3,
    #    'pick_intercept'  : 1.0,
    #    'cart_swap_coef'  : 25.0,
    #},
    #{
    #    'name'            : 'high_cart_weight_volume_penalty',
    #    'pick_weight_coef': 2.5,
    #    'pick_volume_coef': 5e-3,
    #    'pick_intercept'  : 1.0,
    #    'cart_swap_coef'  : 25.0,
    #},
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


def _save_resume(run_dir: str, run_ids: dict,
                 next_A: int, next_B: int, next_C: int) -> None:
    """Persist per-strategy batch counters and run IDs for crash recovery."""
    state = {
        'run_ids'     : run_ids,
        'next_batch'  : {'A': next_A, 'B': next_B, 'C': next_C},
    }
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
        max_bins     = max_bins,
        max_aisles   = max_aisles,
        rng          = random.Random(SEED_WORLD + 1),
        log          = log,
    )
    inventory.cartons  = plan.sampled
    n_skus             = len(plan.sampled)
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
             f'  {n_skus:,} SKUs sampled  expected_fill={expected_fill:.1%}'
             f'  ({time.perf_counter()-t_size:.1f}s)')

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

    # ── persist warehouse stats and aisle distributions ───────────────────────
    if warehouse_db_path is not None:
        from Warehouse_Data import init_warehouse_db, save_warehouse_stats
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
        log.info(f'  Warehouse stats  → {warehouse_db_path}')

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
    )


# ── per-config runner ──────────────────────────────────────────────────────────

def _run_config_sim(cfg: dict, shared: dict, base_dir: str, log: logging.Logger) -> dict:
    """Run the A/B/C strategy simulation for one regression config (nested-pool path).

    Delegates to _prepare_config_run + run_strategies_parallel + _finalize_config_run.
    Returns the sim_result dict.  Used by --pair-workers/--config-workers paths.
    """
    strategy_args, sim_skeleton = _prepare_config_run(cfg, shared, base_dir, log)
    run_strategies_parallel(strategy_args, log)
    return _finalize_config_run(sim_skeleton)




def _run_pair_sims(
    label          : str,
    inv_db         : str,
    aff_db         : str,
    pair_dir       : str,
    config_workers : int,
    log            : logging.Logger,
    max_skus       : int | None = None,
    max_aisles     : int | None = None,
    max_bins       : int | None = None,
) -> tuple[str, dict, dict]:
    """Load one inventory+affinity pair and run all regression config simulations.

    Returns (label, shared, sim_results) so the caller can run analyses
    sequentially (matplotlib is not thread-safe).

    Designed to be submitted to a ThreadPoolExecutor so multiple pairs
    run their simulations in parallel.  Each pair's A/B/C process workers
    are also parallel (via run_strategies_parallel inside _run_config_sim).
    """
    log.info(f'\n{"="*64}')
    log.info(f'  Dataset : {label}')
    log.info(f'{"="*64}')
    shared = build_shared_assets(inv_db, aff_db, log,
                                 max_skus=max_skus, max_aisles=max_aisles,
                                 max_bins=max_bins,
                                 warehouse_db_path=os.path.join(pair_dir, 'warehouse.db'))

    sim_results: dict[str, dict] = {}
    if config_workers > 1:
        log.info(f'  Running {len(REGRESSION_CONFIGS)} configs with '
                 f'{config_workers} parallel simulation thread(s)...')
        with concurrent.futures.ThreadPoolExecutor(max_workers=config_workers) as pool:
            futs = {
                pool.submit(_run_config_sim, cfg, shared, pair_dir, log): cfg
                for cfg in REGRESSION_CONFIGS
            }
            for fut in concurrent.futures.as_completed(futs):
                cfg      = futs[fut]
                cfg_name = cfg.get('name', '?')
                try:
                    sim_results[cfg_name] = fut.result()
                    log.info(f'  Config [{cfg_name}] simulation complete')
                except Exception as exc:
                    log.error(f'  Config [{cfg_name}] FAILED: {exc}', exc_info=True)
    else:
        for cfg in REGRESSION_CONFIGS:
            cfg_name = cfg.get('name', '?')
            try:
                sim_results[cfg_name] = _run_config_sim(cfg, shared, pair_dir, log)
            except Exception as exc:
                log.error(f'  Config [{cfg_name}] FAILED: {exc}', exc_info=True)

    return label, shared, sim_results


# ── flat pool helpers ──────────────────────────────────────────────────────────

def _prepare_config_run(
    cfg     : dict,
    shared  : dict,
    pair_dir: str,
    log     : logging.Logger,
) -> tuple[list, dict]:
    """Pre-initialise one regression config: create DBs, get run_ids, build strategy_args.

    Extracted from _run_config_sim so the flat pool can pre-build ALL work units
    before submitting to ProcessPoolExecutor.

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
    )
    wp      = WorkloadParams.from_pick_config(pick_cfg)
    run_dir = os.path.join(pair_dir, name)
    db_path_A = os.path.join(run_dir, 'sim_A.db')
    db_path_B = os.path.join(run_dir, 'sim_B.db')
    db_path_C = os.path.join(run_dir, 'sim_C.db')
    os.makedirs(run_dir, exist_ok=True)

    inventory          = shared['inventory']
    batch_cfg          = shared['batch_cfg']
    load_params        = shared['load_params']
    warehouse_cfg      = shared['warehouse_cfg']
    total_aisles       = shared['total_aisles']
    total_bins         = shared['total_bins']
    total_units_needed = shared['total_units_needed']

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

    resume = _load_resume(run_dir)
    if resume:
        run_ids = resume['run_ids']
        run_a, run_b, run_c = run_ids['A'], run_ids['B'], run_ids['C']
        starts  = resume.get('next_batch', {})
        start_A = load_worker_checkpoint(run_dir, 'A') or starts.get('A', 0)
        start_B = load_worker_checkpoint(run_dir, 'B') or starts.get('B', 0)
        start_C = load_worker_checkpoint(run_dir, 'C') or starts.get('C', 0)
        log.info(f'  Resuming  A@{start_A}  B@{start_B}  C@{start_C}')
    else:
        for dp in (db_path_A, db_path_B, db_path_C):
            init_run_db(dp)
        run_a   = create_run(db_path_A, 'uniform_assignment')
        run_b   = create_run(db_path_B, 'trip_minimizing_assignment')
        run_c   = create_run(db_path_C, 'trip_maximizing_assignment')
        run_ids = {'A': run_a, 'B': run_b, 'C': run_c}
        start_A = start_B = start_C = 0
        log.info(f'  New run  run_ids A={run_a} B={run_b} C={run_c}')

    _save_resume(run_dir, run_ids, start_A, start_B, start_C)

    _shared = dict(
        inv_db        = shared['inv_db'],
        aff_db        = shared['aff_db'],
        run_dir       = run_dir,
        n_batches     = N_BATCHES,
        k_pickers     = K_PICKERS,
        seed_world    = SEED_WORLD,
        seed_batches  = SEED_BATCHES,
        checkpoint    = _CHECKPOINT,
        max_skus      = shared.get('max_skus'),
        sku_allowlist = shared.get('sku_allowlist'),
        warehouse_cfg = warehouse_cfg,
        pick_cfg      = pick_cfg,
        wp            = wp,
        load_params   = load_params,
        batch_cfg     = batch_cfg,
        # log_queue is NOT set here — injected by flat pool or run_strategies_parallel
    )
    strategy_args = [
        {**_shared, 'strategy': 'A', 'run_id': run_a, 'start_i': start_A, 'db_path': db_path_A},
        {**_shared, 'strategy': 'B', 'run_id': run_b, 'start_i': start_B, 'db_path': db_path_B},
        {**_shared, 'strategy': 'C', 'run_id': run_c, 'start_i': start_C, 'db_path': db_path_C},
    ]
    sim_skeleton = dict(
        name      = name,
        run_dir   = run_dir,
        db_path_A = db_path_A,
        db_path_B = db_path_B,
        db_path_C = db_path_C,
        run_a     = run_a,
        run_b     = run_b,
        run_c     = run_c,
        inv_db    = shared['inv_db'],
        aff_db    = shared['aff_db'],
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
    with open(os.path.join(run_dir, 'sim_meta.json'), 'w') as _f:
        json.dump(sim_skeleton, _f, indent=2)
    return {k: sim_skeleton[k] for k in
            ('name', 'run_dir', 'db_path_A', 'db_path_B', 'db_path_C',
             'run_a', 'run_b', 'run_c')}


def _run_workers_flat(
    pairs         : list,
    base_dir      : str,
    shared_by_pair: dict,
    max_workers   : int,
    log           : logging.Logger,
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
                try:
                    strategy_args, sim_skeleton = _prepare_config_run(
                        cfg, shared, pair_dir, log)
                    for sa in strategy_args:
                        sa['log_queue'] = log_queue   # inject shared queue
                        work_units.append((key, sa))
                    meta[key] = {'sim_skeleton': sim_skeleton, 'remaining': 3}
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

        log.info(f'  Flat pool: {total_jobs} jobs'
                 f' → ProcessPoolExecutor({max_workers})')

        # ── execute ───────────────────────────────────────────────────────────
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as pool:
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
    parser.add_argument('--config-workers', type=int, default=1,
                        help='Regression configs to simulate in parallel per pair '
                             '(each uses 3 A/B/C workers; default=1 = sequential)')
    parser.add_argument('--pair-workers', type=int, default=1,
                        help='DB pairs to simulate in parallel '
                             '(multiplies with --config-workers; be mindful of RAM)')
    parser.add_argument('--workers', type=int, default=None, metavar='N',
                        help='Flat ProcessPoolExecutor pool size — all (pair,config,strategy) '
                             'work units share one pool; no idle between A/B/C barriers. '
                             'Replaces --pair-workers/--config-workers when specified.')
    parser.add_argument('--max-skus', type=int, default=None, metavar='N',
                        help='Cap inventory to the first N SKUs (smaller warehouse for quick runs)')
    parser.add_argument('--max-aisles', type=int, default=None, metavar='N',
                        help='Cap total aisle count by proportionally scaling replica counts')
    parser.add_argument('--max-bins', type=int, default=None, metavar='N',
                        help='Cap total bins by trimming aisle replicas (floor: '
                             '1 replica/type ~= 30k bins). Combine with --max-aisles.')
    args = parser.parse_args()

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
    total_workers = len(pairs) * n_configs * 3
    if args.workers:
        log.info(
            f'Execution plan: {len(pairs)} pair(s) × {n_configs} config(s) × 3 strategies'
            f' = {total_workers} work units  |  flat pool workers={args.workers}'
        )
        # ── flat pool path (no idle between A/B/C barriers) ───────────────────
        shared_by_pair = {}
        for label, inv_db, aff_db in pairs:
            log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
            shared_by_pair[label] = build_shared_assets(
                inv_db, aff_db, log,
                max_skus=args.max_skus, max_aisles=args.max_aisles,
                max_bins=args.max_bins,
                warehouse_db_path=os.path.join(base_dir, label, 'warehouse.db'),
            )
        _run_workers_flat(pairs, base_dir, shared_by_pair, args.workers, log)
    elif args.pair_workers > 1:
        # ── nested path: parallel pairs ───────────────────────────────────────
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=args.pair_workers) as pair_pool:
            pair_futs = {
                pair_pool.submit(
                    _run_pair_sims,
                    label, inv_db, aff_db,
                    os.path.join(base_dir, label),
                    args.config_workers, log,
                    args.max_skus, args.max_aisles, args.max_bins,
                ): (label, inv_db, aff_db)
                for label, inv_db, aff_db in pairs
            }
            for fut in concurrent.futures.as_completed(pair_futs):
                orig = pair_futs[fut]
                try:
                    label, _, sim_results = fut.result()
                    log.info(f'  [{label}] all sims complete')
                except Exception as exc:
                    log.error(f'  Pair {orig[0]} FAILED: {exc}', exc_info=True)
    else:
        # ── sequential (default) ──────────────────────────────────────────────
        for label, inv_db, aff_db in pairs:
            pair_dir = os.path.join(base_dir, label)
            _run_pair_sims(
                label, inv_db, aff_db, pair_dir, args.config_workers, log,
                args.max_skus, args.max_aisles, args.max_bins,
            )

    log.info(f'\nAll {len(pairs)} dataset(s) × {n_configs} config(s) simulations complete.'
             f'  Root: {base_dir}'
             f'\n  Run graphs: python run_analysis.py {base_dir}')


if __name__ == '__main__':
    main()
