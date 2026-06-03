"""
Warehouse assignment strategy comparison — standalone runner.

Replicates assignment_comparison.ipynb but runs as a plain Python process:
  - No Jupyter kernel / no GUI (matplotlib Agg backend)
  - Logs progress to both stdout and a log file in the output directory
  - Checkpoints random state every 100 batches so a crash can be resumed:
      python run_comparison.py                        # new run
      python run_comparison.py --resume <base_dir>    # continue crashed run
"""

import argparse
import concurrent.futures
import json
import logging
import math
import os
import pickle
import random
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

from Comparison_Plots import run_config_analysis as _run_config_analysis

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
from Inventory_Management import LoadParams
from Pick import PickConfig
from Aisle_Dimensions import (
    aisle_width_for, aisle_height_for, SIZE_HEIGHTS,
    SINGLETON_BIN_HEIGHT as _SINGLETON_BIN_HEIGHT,
    unit_bin_width as _unit_bin_width,
)
from Storage_Primitive import viable_storage_units as _vsu
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import BatchConfig

from Picking_Data import (
    create_run, init_run_db,
    load_batch_stats, load_task_stats,
)
from Simulation_Analytics import flag_batch_outliers, flag_task_outliers
from Workload import WorkloadParams

from strategy_runner import run_strategies_parallel, load_worker_checkpoint

# ── simulation constants ───────────────────────────────────────────────────────
SEED_WORLD       = 42
SEED_BATCHES     = 1337
N_BATCHES        = 100
K_PICKERS        = 25
_CHECKPOINT      = max(1, N_BATCHES // 10)
_WIN             = 50
_BATCH_MEAN_FRAC = 0.25
_BATCH_STD_FRAC  = 0.03
_TARGET_FILL = 0.80   # headroom fraction: size each aisle type to this utilization

# Physical aisle dimensions: 25 pallet-width columns × 30 extra_large-height levels.
# Actual bin counts per aisle depend on unit type and size distribution.
_AISLE_W = aisle_width_for(50)    # 50 × 48 = 2400 physical units
_AISLE_H = aisle_height_for(10)   # 10 × 48 = 480 physical units


def _effective_bins_per_aisle(cfg: AisleConfig) -> int:
    """Actual bin count for one aisle replica of *cfg* after density expansion.

    Pallet aisles: x-density from pallet width (48); y-density from size-tier heights.
    Singleton aisles: x-density from singleton width (16); y-density from fixed
    SINGLETON_BIN_HEIGHT (48) — no size tiers.
    """
    unit_w = _unit_bin_width(cfg.unit_type)
    n_cols = cfg.aisle_width // unit_w
    if cfg.unit_type == 'singleton':
        return n_cols * (cfg.aisle_height // _SINGLETON_BIN_HEIGHT)
    probs  = cfg.size_probabilities or [1.0 / len(cfg.storage_sizes)] * len(cfg.storage_sizes)
    n_rows = sum(
        round(p * cfg.aisle_height) // SIZE_HEIGHTS[s]
        for s, p in zip(cfg.storage_sizes, probs)
    )
    return n_cols * n_rows

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

_CATEGORIES  = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']

# ── pallet aisle bin-size distribution ────────────────────────────────────────
# All four sizes with equal probability so every carton can land somewhere.
_ALL_SIZES  = ['small', 'medium', 'large', 'extra_large']
_PALL_PROBS = [0.25, 0.25, 0.25, 0.25]

# ── warehouse configuration ────────────────────────────────────────────────────
# 12 pallet aisle types (conveyable + non-conveyable × 6 categories), all sizes.
# 12 singleton aisle types (same split) — singleton bins have no size tiers;
# storage_sizes=['singleton'] is a placeholder that routes through the no-tier
# path in Aisle_Storage and Inventory_Management.
_AISLE_CFGS = []
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES, _PALL_PROBS))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES, _PALL_PROBS))
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))


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
    {
        'name'            : 'high_cart_weight_penalty',
        'pick_weight_coef': 2.5,
        'pick_volume_coef': 1e-3,
        'pick_intercept'  : 1.0,
        'cart_swap_coef'  : 25.0,
    },
    {
        'name'            : 'high_cart_weight_volume_penalty',
        'pick_weight_coef': 2.5,
        'pick_volume_coef': 5e-3,
        'pick_intercept'  : 1.0,
        'cart_swap_coef'  : 25.0,
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
    inventory_db: str,
    affinity_db : str,
    log         : logging.Logger,
) -> dict:
    """Load inventory + affinity from DB and build warehouse A.

    Warehouse is sized so total bins ≥ N_SKUS × 1.1 (minimum replicas of the
    60-type layout satisfying that constraint).
    """
    log.info(f'  Loading inventory  : {inventory_db}')
    t0        = time.perf_counter()
    inventory = load_inventory_from_db(inventory_db)
    n_skus    = len(inventory.cartons)
    log.info(f'  {n_skus:,} cartons  ({time.perf_counter()-t0:.2f}s)')

    # ── Warehouse sizing (data-driven from actual bin requirements) ───────────
    # Call viable_storage_units(carton, equilibrium_qty) for every carton to
    # count exactly how many pallet and singleton bins each (handling, category)
    # pair needs.  Each of the 24 aisle types is sized independently so the
    # warehouse reaches _TARGET_FILL utilization per type.

    t_size = time.perf_counter()
    _pallet_needs:    dict[tuple, int] = {}
    _singleton_needs: dict[tuple, int] = {}

    avg_eq = sum(c.equilibrium_qty for c in inventory.cartons) / max(n_skus, 1)
    log.info(f'  Inventory model  : avg equilibrium_qty={avg_eq:.1f}'
             f'  avg reorder_point={sum(c.reorder_point for c in inventory.cartons)/max(n_skus,1):.1f}'
             f'  avg lead_time={sum(getattr(c,"lead_time_mean",0.0) for c in inventory.cartons)/max(n_skus,1):.2f}'
             f'  avg supply_cv={sum(getattr(c,"supply_cv",0.0) for c in inventory.cartons)/max(n_skus,1):.3f}')

    for c in inventory.cartons:
        key = (c.storage_handle_config.handling, c.storage_handle_config.category)
        for unit in _vsu(c, c.equilibrium_qty):
            if unit.unit_category == 'pallet':
                _pallet_needs[key] = _pallet_needs.get(key, 0) + 1
            else:
                _singleton_needs[key] = _singleton_needs.get(key, 0) + 1

    total_pallet_needed    = sum(_pallet_needs.values())
    total_singleton_needed = sum(_singleton_needs.values())
    total_units_needed     = total_pallet_needed + total_singleton_needed

    # One replica count per aisle type; minimum 1 so every type has at least
    # one aisle even when no cartons map to it.
    aisle_replicas = []
    for cfg in _AISLE_CFGS:
        needs    = (_pallet_needs if cfg.unit_type == 'pallet' else _singleton_needs)
        needed   = needs.get((cfg.handling_type, cfg.storage_type), 0)
        eff_bins = _effective_bins_per_aisle(cfg)
        aisle_replicas.append(max(1, math.ceil(needed / (eff_bins * _TARGET_FILL))))

    total_aisles  = sum(aisle_replicas)
    total_bins    = sum(rep * _effective_bins_per_aisle(cfg)
                        for rep, cfg in zip(aisle_replicas, _AISLE_CFGS))
    aisle_splits  = [r / total_aisles for r in aisle_replicas]
    expected_fill = total_units_needed / total_bins if total_bins else 0.0

    log.info(f'  Bin requirements : {total_pallet_needed:,} pallet'
             f' + {total_singleton_needed:,} singleton'
             f' = {total_units_needed:,} total'
             f'  ({total_units_needed/max(n_skus,1):.1f}/SKU)'
             f'  ({time.perf_counter()-t_size:.1f}s)')
    log.info(f'  Warehouse : {total_aisles} aisles / {total_bins:,} bins'
             f'  expected_fill={expected_fill:.1%}  target={_TARGET_FILL:.0%}')

    warehouse_cfg = WarehouseConfig(
        total_aisles  = total_aisles,
        aisle_splits  = aisle_splits,
        aisle_configs = _AISLE_CFGS,
    )

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
    )


# ── per-config runner ──────────────────────────────────────────────────────────

def _run_config_sim(cfg: dict, shared: dict, base_dir: str, log: logging.Logger) -> dict:
    """Run the A/B/C strategy simulation for one regression config.

    Returns a sim_result dict consumed by _run_config_analysis.
    Separating simulation from analysis lets multiple configs run their
    simulations concurrently (thread-safe) while analyses stay sequential
    (matplotlib pyplot is not thread-safe).
    """
    name = cfg.get('name') or (
        f"w{cfg.get('pick_weight_coef',1.1)}_v{cfg.get('pick_volume_coef',1e-3)}"
        f"_i{cfg.get('pick_intercept',1.0)}_c{cfg.get('cart_swap_coef',10.0)}"
    )
    pick_cfg = PickConfig(
        num_pickers      = K_PICKERS,
        x_speed      = cfg.get('x_speed',      1.0),
        y_speed      = cfg.get('y_speed',      0.5),
        pick_intercept   = cfg.get('pick_intercept',   1.0),
        pick_weight_coef = cfg.get('pick_weight_coef', 1.1),
        pick_volume_coef = cfg.get('pick_volume_coef', 1e-3),
        cart_swap_coef   = cfg.get('cart_swap_coef',   10.0),
    )
    wp      = WorkloadParams.from_pick_config(pick_cfg)
    run_dir = os.path.join(base_dir, name)
    # Separate DB per strategy so workers write concurrently without WAL contention.
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
    # aisle_size/unittype/handling maps are only needed for analysis — not unpacked here

    log.info(f'{"="*64}')
    log.info(f'  Config : {name}')
    log.info(f'  w={pick_cfg.pick_weight_coef}  v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  c={pick_cfg.cart_swap_coef}')
    log.info(f'{"="*64}')

    config_record = {
        # pick model (regression config)
        'name'            : name,
        'pick_weight_coef': pick_cfg.pick_weight_coef,
        'pick_volume_coef': pick_cfg.pick_volume_coef,
        'pick_intercept'  : pick_cfg.pick_intercept,
        'cart_swap_coef'  : pick_cfg.cart_swap_coef,
        'x_speed'     : pick_cfg.x_speed,
        'y_speed'     : pick_cfg.y_speed,
        'num_pickers'     : pick_cfg.num_pickers,
        # load model (λ / k / γ)
        'load_lambda'     : load_params.lambda_,
        'load_k'          : load_params.k,
        'load_gamma'      : load_params.gamma,
        # warehouse / simulation
        'total_aisles'    : total_aisles,
        'total_bins'      : total_bins,
        'n_skus'          : len(inventory.cartons),
        'total_units'     : total_units_needed,
        'bin_slack_pct'   : round((total_bins / max(total_units_needed, 1) - 1) * 100, 2),
        'batch_mean_frac' : _BATCH_MEAN_FRAC,
        'n_batches'       : N_BATCHES,
        'seed_world'      : SEED_WORLD,
        'seed_batches'    : SEED_BATCHES,
        # inventory schema (OUP equilibrium model)
        'avg_equilibrium_qty' : round(sum(getattr(c, 'equilibrium_qty', 1)
                                          for c in inventory.cartons) / max(len(inventory.cartons), 1), 1),
        'avg_reorder_point'   : round(sum(getattr(c, 'reorder_point', 1)
                                          for c in inventory.cartons) / max(len(inventory.cartons), 1), 2),
        'avg_lead_time_mean'  : round(sum(getattr(c, 'lead_time_mean', 0.0)
                                          for c in inventory.cartons) / max(len(inventory.cartons), 1), 3),
        'avg_supply_cv'       : round(sum(getattr(c, 'supply_cv', 0.0)
                                          for c in inventory.cartons) / max(len(inventory.cartons), 1), 3),
    }
    with open(os.path.join(run_dir, 'config.json'), 'w') as f:
        json.dump(config_record, f, indent=2)

    # ── DB init / resume ──────────────────────────────────────────────────────
    resume = _load_resume(run_dir)
    if resume:
        run_ids = resume['run_ids']
        run_a, run_b, run_c = run_ids['A'], run_ids['B'], run_ids['C']
        starts  = resume.get('next_batch', {})
        start_A = load_worker_checkpoint(run_dir, 'A') or starts.get('A', 0)
        start_B = load_worker_checkpoint(run_dir, 'B') or starts.get('B', 0)
        start_C = load_worker_checkpoint(run_dir, 'C') or starts.get('C', 0)
        log.info(f'  Resuming  A@{start_A}  B@{start_B}  C@{start_C}'
                 f'  (run_ids {run_a}/{run_b}/{run_c})')
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

    # ── build per-strategy arg dicts and run in parallel ──────────────────────
    _shared = dict(
        inv_db        = shared['inv_db'],
        aff_db        = shared['aff_db'],
        run_dir       = run_dir,
        n_batches     = N_BATCHES,
        k_pickers     = K_PICKERS,
        seed_world    = SEED_WORLD,
        seed_batches  = SEED_BATCHES,
        checkpoint    = _CHECKPOINT,
        target_fill   = _TARGET_FILL,
        warehouse_cfg = warehouse_cfg,
        pick_cfg      = pick_cfg,
        wp            = wp,
        load_params   = load_params,
        batch_cfg     = batch_cfg,
    )
    strategy_args = [
        {**_shared, 'strategy': 'A', 'run_id': run_a, 'start_i': start_A, 'db_path': db_path_A},
        {**_shared, 'strategy': 'B', 'run_id': run_b, 'start_i': start_B, 'db_path': db_path_B},
        {**_shared, 'strategy': 'C', 'run_id': run_c, 'start_i': start_C, 'db_path': db_path_C},
    ]

    run_strategies_parallel(strategy_args, log)

    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)

    sim_result = dict(
        name      = name,
        run_dir   = run_dir,
        db_path_A = db_path_A,
        db_path_B = db_path_B,
        db_path_C = db_path_C,
        run_a     = run_a,
        run_b     = run_b,
        run_c     = run_c,
    )

    # Write sim_meta.json so --analyze-only can reconstruct this result later.
    # Presence of this file signals the simulation completed successfully.
    with open(os.path.join(run_dir, 'sim_meta.json'), 'w') as _f:
        json.dump({**sim_result,
                   'inv_db': shared['inv_db'],
                   'aff_db': shared['aff_db']}, _f, indent=2)

    return sim_result


def run_config(cfg: dict, shared: dict, base_dir: str, log: logging.Logger) -> None:
    """Backward-compatible wrapper: simulate then analyse one regression config."""
    sim_result = _run_config_sim(cfg, shared, base_dir, log)
    _run_config_analysis(sim_result, shared, log)


def _run_pair_sims(
    label          : str,
    inv_db         : str,
    aff_db         : str,
    pair_dir       : str,
    config_workers : int,
    log            : logging.Logger,
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
    shared = build_shared_assets(inv_db, aff_db, log)

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


# ── standalone analysis ────────────────────────────────────────────────────────

def _run_analysis_only(base_dir: str, log: logging.Logger) -> None:
    """Re-run analysis (plots only) on all completed sims under *base_dir*.

    Scans the two-level structure base_dir/pair_label/config_name/ for
    sim_meta.json files written by _run_config_sim.  Calls build_shared_assets
    once per pair (to get aisle maps) then _run_config_analysis per config.
    """
    for pair_name in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair_name)
        if not os.path.isdir(pair_dir):
            continue

        config_metas: list[dict] = []
        inv_db = aff_db = None
        for cfg_name in sorted(os.listdir(pair_dir)):
            meta_path = os.path.join(pair_dir, cfg_name, 'sim_meta.json')
            if not os.path.exists(meta_path):
                continue
            with open(meta_path) as f:
                meta = json.load(f)
            config_metas.append(meta)
            if inv_db is None:
                inv_db = meta.get('inv_db')
                aff_db = meta.get('aff_db')

        if not config_metas or inv_db is None or aff_db is None:
            continue

        log.info(f'  Pair: {pair_name}  ({len(config_metas)} config(s))')
        try:
            shared = build_shared_assets(inv_db, aff_db, log)
        except Exception as exc:
            log.error(f'  build_shared_assets failed for {pair_name}: {exc}', exc_info=True)
            continue

        for meta in config_metas:
            cfg_name = meta.get('name', '?')
            try:
                sim_result = {k: meta[k] for k in
                              ('name', 'run_dir', 'db_path_A', 'db_path_B', 'db_path_C',
                               'run_a', 'run_b', 'run_c')}
                _run_config_analysis(sim_result, shared, log)
            except Exception as exc:
                log.error(f'  Analysis failed for {cfg_name}: {exc}', exc_info=True)


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
    parser.add_argument('--analyze-only', metavar='BASE_DIR', default=None,
                        help='Skip simulation; re-run analysis plots on an existing '
                             'comparison directory (uses sim_meta.json written by each run)')
    args = parser.parse_args()

    if args.analyze_only:
        base_dir = (args.analyze_only if os.path.isabs(args.analyze_only)
                    else os.path.join(_OUTPUT_DIR, args.analyze_only))
        if not os.path.isdir(base_dir):
            sys.exit(f'Analysis directory not found: {base_dir}')
        log = _setup_logging(os.path.join(base_dir, 'reanalysis.log'))
        log.info(f'--analyze-only  dir: {base_dir}')
        _run_analysis_only(base_dir, log)
        return

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
    workers_per_pair = n_configs * 3          # configs × A/B/C strategies
    pair_mode = (f'{args.pair_workers} pairs in parallel'
                 if args.pair_workers > 1 else 'pairs sequential')
    log.info(
        f'Execution plan: {len(pairs)} pair(s) × {n_configs} config(s) × 3 strategies'
        f' = {len(pairs) * workers_per_pair} total process-worker runs'
        f'  |  pair_workers={args.pair_workers}  config_workers={args.config_workers}'
        f'  |  {pair_mode}'
    )

    # ── pair dispatch ─────────────────────────────────────────────────────────
    # Simulations (CPU-bound process workers) run in parallel across pairs when
    # --pair-workers > 1.  Analyses (matplotlib) always run sequentially after
    # all simulations for a pair are done — pyplot is not thread-safe.
    if args.pair_workers > 1:
        # Submit all pairs simultaneously; collect (shared, sim_results) as they
        # finish and run the analysis phase immediately in the main thread.
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=args.pair_workers) as pair_pool:
            pair_futs: dict[concurrent.futures.Future, tuple] = {
                pair_pool.submit(
                    _run_pair_sims,
                    label, inv_db, aff_db,
                    os.path.join(base_dir, label),
                    args.config_workers,
                    log,
                ): (label, inv_db, aff_db)
                for label, inv_db, aff_db in pairs
            }
            for fut in concurrent.futures.as_completed(pair_futs):
                orig = pair_futs[fut]
                try:
                    label, shared, sim_results = fut.result()
                    log.info(f'  [{label}] all sims done — running analyses...')
                    for cfg in REGRESSION_CONFIGS:
                        cfg_name = cfg.get('name', '?')
                        if cfg_name in sim_results:
                            _run_config_analysis(sim_results[cfg_name], shared, log)
                except Exception as exc:
                    log.error(f'  Pair {orig[0]} FAILED: {exc}', exc_info=True)
    else:
        # Sequential: one pair at a time (original behaviour).
        for label, inv_db, aff_db in pairs:
            pair_dir = os.path.join(base_dir, label)
            _, shared, sim_results = _run_pair_sims(
                label, inv_db, aff_db, pair_dir, args.config_workers, log
            )
            log.info('  All simulations done — running analyses sequentially...')
            for cfg in REGRESSION_CONFIGS:
                cfg_name = cfg.get('name', '?')
                if cfg_name in sim_results:
                    _run_config_analysis(sim_results[cfg_name], shared, log)

    log.info(f'\nAll {len(pairs)} dataset(s) × {len(REGRESSION_CONFIGS)} config(s) complete.'
             f'  Root: {base_dir}')


if __name__ == '__main__':
    main()
