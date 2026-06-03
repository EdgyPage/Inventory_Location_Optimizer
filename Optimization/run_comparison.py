"""
Warehouse assignment strategy comparison — standalone runner.

Replicates assignment_comparison.ipynb but runs as a plain Python process:
  - No Jupyter kernel / no GUI (matplotlib Agg backend)
  - Logs progress to both stdout and a log file in the output directory
  - Checkpoints random state every 100 batches so a crash can be resumed:
      python run_comparison.py                        # new run
      python run_comparison.py --resume <base_dir>    # continue crashed run
"""

import matplotlib
matplotlib.use('Agg')  # must come before pyplot import

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

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

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

_A_COL      = '#5b9bd5'
_B_COL      = '#f4a030'
_C_COL      = '#70ad47'
_TRAVEL_COL = '#a9a9a9'

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


# ── dataframe helpers ──────────────────────────────────────────────────────────

def _bdf(stats):
    return pd.DataFrame([{
        'batch_id'              : s.batch_id,
        'duration'              : s.duration,
        'num_tasks'             : s.num_tasks,
        'total_items'           : s.total_items,
        'completion_rate'       : s.total_items / s.duration if s.duration > 0 else 0.0,
        'avg_concurrent_pickers': s.avg_concurrent_pickers,
        'picking_pct'           : s.picking_pct   * 100,
        'traveling_pct'         : s.traveling_pct * 100,
    } for s in stats])


def _tdf(stats, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W_a'        : s.W_a,
        'lift_sum'   : s.lift_sum,
        'num_bins'   : s.num_bins_visited,
        'total_items': s.total_items,
        'unit_type'  : aisle_unittype_map.get(s.aisle_id),
        'handling'   : aisle_handling_map.get(s.aisle_id),
    } for s in stats])





def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


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
    # Call viable_storage_units(carton, stock_qty) for every carton to count
    # exactly how many pallet and singleton bins each (handling, category) pair
    # needs after initial stocking.  Each of the 24 aisle types is then sized
    # independently so the warehouse reaches _TARGET_FILL utilization per type,
    # eliminating the large initial queue that arose from undersized aisles.

    t_size = time.perf_counter()
    _pallet_needs:    dict[tuple, int] = {}
    _singleton_needs: dict[tuple, int] = {}

    for c in inventory.cartons:
        # Size for equilibrium_qty (OUP target); fall back to legacy stock_qty.
        qty = getattr(c, 'equilibrium_qty', getattr(c, 'stock_qty', 1))
        key = (c.storage_handle_config.handling, c.storage_handle_config.category)
        for unit in _vsu(c, qty):
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
             f' = {total_units_needed:,} total  ({time.perf_counter()-t_size:.1f}s)')
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
    )


# ── plot helpers ───────────────────────────────────────────────────────────────

def _kde_plot(ax, data, color, bins):
    ax.hist(data, bins=bins, color=color, alpha=0.65, edgecolor='white')
    if len(data) > 1 and data.max() > data.min():
        kde = gaussian_kde(data, bw_method='silverman')
        xs  = np.linspace(data.min(), data.max(), 400)
        ax.plot(xs, kde(xs) * len(data) * (data.max() - data.min()) / bins, color=color, lw=2)
    ax.axvline(data.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {data.mean():.1f}')
    ax.axvline(np.median(data), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(data):.1f}')


def _save_close(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


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

    return dict(
        name      = name,
        run_dir   = run_dir,
        db_path_A = db_path_A,
        db_path_B = db_path_B,
        db_path_C = db_path_C,
        run_a     = run_a,
        run_b     = run_b,
        run_c     = run_c,
    )


def _run_config_analysis(sim_result: dict, shared: dict, log: logging.Logger) -> None:
    """Run the analysis + plotting phase for one regression config.

    Must be called sequentially (matplotlib pyplot is not thread-safe).
    Reads sim_result returned by _run_config_sim.
    """
    name               = sim_result['name']
    run_dir            = sim_result['run_dir']
    db_path_A          = sim_result['db_path_A']
    db_path_B          = sim_result['db_path_B']
    db_path_C          = sim_result['db_path_C']
    run_a              = sim_result['run_a']
    run_b              = sim_result['run_b']
    run_c              = sim_result['run_c']
    aisle_unittype_map = shared['aisle_unittype_map']
    aisle_handling_map = shared['aisle_handling_map']
    total_aisles       = shared['total_aisles']

    # ── analysis ──────────────────────────────────────────────────────────────
    bs_fA = flag_batch_outliers(load_batch_stats(db_path_A, run_a))
    bs_fB = flag_batch_outliers(load_batch_stats(db_path_B, run_b))
    bs_fC = flag_batch_outliers(load_batch_stats(db_path_C, run_c))
    ts_fA = flag_task_outliers(load_task_stats(db_path_A, run_a))
    ts_fB = flag_task_outliers(load_task_stats(db_path_B, run_b))
    ts_fC = flag_task_outliers(load_task_stats(db_path_C, run_c))

    df_bA = _bdf([s for s in bs_fA if not s.is_outlier])
    df_bB = _bdf([s for s in bs_fB if not s.is_outlier])
    df_bC = _bdf([s for s in bs_fC if not s.is_outlier])
    df_tA = _tdf([s for s in ts_fA if not s.is_outlier], aisle_unittype_map, aisle_handling_map)
    df_tB = _tdf([s for s in ts_fB if not s.is_outlier], aisle_unittype_map, aisle_handling_map)
    df_tC = _tdf([s for s in ts_fC if not s.is_outlier], aisle_unittype_map, aisle_handling_map)

    # summary CSVs
    bcols  = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols  = ['duration', 'W_a', 'lift_sum', 'num_bins']
    summ_b = pd.concat(
        [df_bA[bcols].agg(['mean','median','std']).T,
         df_bB[bcols].agg(['mean','median','std']).T,
         df_bC[bcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)']).round(3)
    summ_t = pd.concat(
        [df_tA[tcols].agg(['mean','median','std']).T,
         df_tB[tcols].agg(['mean','median','std']).T,
         df_tC[tcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)']).round(3)
    summ_b.to_csv(os.path.join(run_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(run_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    # ── plot 1: batch duration ─────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Batch Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['duration'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['duration'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_bC['duration'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=40)
        ax.set_xlabel('Batch duration  (sim time units)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot1_batch_duration.png'))

    # ── plot 2: task duration ──────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Task (Aisle) Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_tA['duration'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_tB['duration'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_tC['duration'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=50)
        ax.set_xlabel('Task duration  (sim time units)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot2_task_duration.png'))

    # ── plot 3: completion rate + batch duration ──────────────────────────────
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))
    fig.suptitle(f'Batch Completion  (dots = per-batch, line = rolling {_WIN}-batch mean)  [{name}]',
                 fontsize=13, fontweight='bold')

    for df, c, lbl in [(df_bA, _A_COL, 'Uniform (A)'),
                       (df_bB, _B_COL, 'Trip-Min (B)'),
                       (df_bC, _C_COL, 'Trip-Max (C)')]:
        x = df.sort_values('batch_id')['batch_id'].values
        ax1.plot(x, _roll(df, 'completion_rate', _WIN), color=c, lw=2, label=lbl)
    ax1.set_ylabel('Items / time unit', fontsize=10)
    ax1.set_title('Throughput rate', fontsize=10)
    ax1.legend(fontsize=9);  ax1.grid(alpha=0.3)

    for df, c, lbl in [(df_bA, _A_COL, 'Uniform (A)'),
                       (df_bB, _B_COL, 'Trip-Min (B)'),
                       (df_bC, _C_COL, 'Trip-Max (C)')]:
        x     = df.sort_values('batch_id')['batch_id'].values
        y_raw = df.sort_values('batch_id')['duration'].values
        ax2.scatter(x, y_raw, color=c, alpha=0.25, s=10, zorder=2)
        ax2.plot(x, _roll(df, 'duration', _WIN), color=c, lw=2, label=lbl, zorder=3)
    ax2.set_xlabel('Batch ID', fontsize=10)
    ax2.set_ylabel('Duration (sim time units)', fontsize=10)
    ax2.set_title('Batch completion time', fontsize=10)
    ax2.legend(fontsize=9);  ax2.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot3_completion_rate.png'))

    # ── plot 4: picker concurrency ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Picker Concurrency  [{name}]', fontsize=12, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['avg_concurrent_pickers'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['avg_concurrent_pickers'].values, 'B — Trip-Minimizing', _B_COL),
        (axes[2], df_bC['avg_concurrent_pickers'].values, 'C — Trip-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=35)
        ax.axvline(K_PICKERS, color='grey', lw=1.0, linestyle='-.', label=f'Max ({K_PICKERS})')
        ax.set_xlabel('Avg concurrent pickers', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot4_picker_concurrency.png'))

    # ── plot 5: picker utilisation ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    fig.suptitle(f'Picker Utilisation Breakdown  [{name}]', fontsize=13, fontweight='bold')
    bp = axes[0].boxplot(
        [df_bA['picking_pct'].values,   df_bB['picking_pct'].values,   df_bC['picking_pct'].values,
         df_bA['traveling_pct'].values, df_bB['traveling_pct'].values, df_bC['traveling_pct'].values],
        labels=['Pick A', 'Pick B', 'Pick C', 'Travel A', 'Travel B', 'Travel C'],
        patch_artist=True, medianprops=dict(color='black', lw=2),
    )
    for patch, c in zip(bp['boxes'], [_A_COL, _B_COL, _C_COL, _A_COL, _B_COL, _C_COL]):
        patch.set_facecolor(c);  patch.set_alpha(0.7)
    axes[0].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[0].set_title('Picking vs Traveling %', fontsize=10);  axes[0].grid(axis='y', alpha=0.3)
    for dfb, c, lbl in [(df_bA, _A_COL, 'Uniform'), (df_bB, _B_COL, 'Trip-Min'), (df_bC, _C_COL, 'Trip-Max')]:
        axes[1].hist(dfb['picking_pct'].values, bins=30, color=c, alpha=0.55, edgecolor='white',
                     label=f'{lbl}  μ={dfb["picking_pct"].mean():.1f}%')
    axes[1].xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[1].set_xlabel('Picking %', fontsize=10);  axes[1].set_ylabel('Count', fontsize=10)
    axes[1].set_title('Picking % — overlaid', fontsize=10)
    axes[1].legend(fontsize=8);  axes[1].grid(axis='y', alpha=0.3)
    x  = np.arange(3)
    pk = [df_bA['picking_pct'].mean(),   df_bB['picking_pct'].mean(),   df_bC['picking_pct'].mean()]
    tr = [df_bA['traveling_pct'].mean(), df_bB['traveling_pct'].mean(), df_bC['traveling_pct'].mean()]
    axes[2].bar(x, pk, width=0.5, color=[_A_COL, _B_COL, _C_COL], alpha=0.85, label='Picking')
    axes[2].bar(x, tr, width=0.5, bottom=pk, color=_TRAVEL_COL, alpha=0.85, label='Traveling')
    axes[2].set_xticks(x);  axes[2].set_xticklabels(['Uniform (A)', 'Trip-Min (B)', 'Trip-Max (C)'])
    axes[2].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[2].set_ylabel('Mean fraction (%)', fontsize=10);  axes[2].set_title('Aggregate mean split', fontsize=10)
    axes[2].legend(fontsize=8);  axes[2].grid(axis='x', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot5_picker_utilisation.png'))

    # ── plot 6: task duration by aisle type (handling × unit-type) ───────────
    # Replaces former plots 6a/6b (pallet-size breakdown) which became empty
    # after pallet aisles switched to multi-size distribution
    # (Aisle.from_size_distribution sets aisle.storage_size = None).
    # handling_type and unit_type are always non-None strings on every aisle.
    _AISLE_GROUPS  = [('conveyable', 'pallet'), ('conveyable', 'singleton'),
                      ('non-conveyable', 'pallet'), ('non-conveyable', 'singleton')]
    _GROUP_LABELS  = ['Conv\nPallet', 'Conv\nSingleton',
                      'Non-Conv\nPallet', 'Non-Conv\nSingleton']

    def _mean_by_group(df, h, u):
        v = df[(df['handling'] == h) & (df['unit_type'] == u)]['duration']
        return float(v.mean()) if len(v) > 0 else 0.0

    x6  = np.arange(len(_AISLE_GROUPS));  w6 = 0.25
    mA6 = [_mean_by_group(df_tA, h, u) for h, u in _AISLE_GROUPS]
    mB6 = [_mean_by_group(df_tB, h, u) for h, u in _AISLE_GROUPS]
    mC6 = [_mean_by_group(df_tC, h, u) for h, u in _AISLE_GROUPS]

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(f'Mean Task Duration by Aisle Type  [{name}]', fontsize=13, fontweight='bold')

    axes[0].bar(x6 - w6, mA6, width=w6, color=_A_COL, alpha=0.85, label='Uniform (A)')
    axes[0].bar(x6,       mB6, width=w6, color=_B_COL, alpha=0.85, label='Trip-Min (B)')
    axes[0].bar(x6 + w6, mC6, width=w6, color=_C_COL, alpha=0.85, label='Trip-Max (C)')
    axes[0].set_xticks(x6);  axes[0].set_xticklabels(_GROUP_LABELS, fontsize=9)
    axes[0].set_ylabel('Mean task duration', fontsize=10)
    axes[0].set_title('Mean task duration by handling × unit type', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    dB6 = [(b - a) / abs(a) * 100 if a else 0 for a, b in zip(mA6, mB6)]
    dC6 = [(c - a) / abs(a) * 100 if a else 0 for a, c in zip(mA6, mC6)]
    axes[1].bar(x6 - w6/2, dB6, width=w6,
                color=[_B_COL if d < 0 else '#c00000' for d in dB6], alpha=0.85, label='B vs A')
    axes[1].bar(x6 + w6/2, dC6, width=w6,
                color=[_C_COL if d > 0 else '#c00000' for d in dC6], alpha=0.85, label='C vs A')
    axes[1].axhline(0, color='black', lw=1)
    for j, (dB, dC) in enumerate(zip(dB6, dC6)):
        if abs(dB) > 0.1:
            axes[1].text(j - w6/2, dB + (0.3 if dB >= 0 else -0.8), f'{dB:.1f}%',
                         ha='center', fontsize=8)
        if abs(dC) > 0.1:
            axes[1].text(j + w6/2, dC + (0.3 if dC >= 0 else -0.8), f'{dC:.1f}%',
                         ha='center', fontsize=8)
    axes[1].set_xticks(x6);  axes[1].set_xticklabels(_GROUP_LABELS, fontsize=9)
    axes[1].set_ylabel('Δ (X − A) / A  %', fontsize=10)
    axes[1].set_title('Duration delta vs Uniform (A)', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter())
    axes[1].legend(fontsize=8);  axes[1].grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot6_aisle_type.png'))

    # ── plot 7: per-aisle ──────────────────────────────────────────────────────
    acmp = pd.concat([
        df_tA.groupby('aisle_id')['duration'].mean().rename('A'),
        df_tB.groupby('aisle_id')['duration'].mean().rename('B'),
        df_tC.groupby('aisle_id')['duration'].mean().rename('C'),
    ], axis=1).dropna()
    acmp['dB'] = (acmp['B'] - acmp['A']) / acmp['A'].abs() * 100
    acmp['dC'] = (acmp['C'] - acmp['A']) / acmp['A'].abs() * 100

    fig, axes = plt.subplots(1, 3, figsize=(19, 5))
    fig.suptitle(f'Per-Aisle Mean Task Duration  [{name}]', fontsize=13, fontweight='bold')
    for v, lbl, c in [(acmp['A'], 'Uniform (A)',   _A_COL),
                      (acmp['B'], 'Trip-Min (B)', _B_COL),
                      (acmp['C'], 'Trip-Max (C)', _C_COL)]:
        axes[0].hist(v, bins=50, color=c, alpha=0.50, edgecolor='white', label=f'{lbl}  μ={v.mean():.1f}')
    axes[0].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[0].set_ylabel('Aisle count', fontsize=10)
    axes[0].set_title('Distribution of aisle mean durations', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)
    for v, lbl, c in [(np.sort(np.asarray(acmp['A'], dtype=float)), 'Uniform (A)',   _A_COL),
                      (np.sort(np.asarray(acmp['B'], dtype=float)), 'Trip-Min (B)', _B_COL),
                      (np.sort(np.asarray(acmp['C'], dtype=float)), 'Trip-Max (C)', _C_COL)]:
        axes[1].plot(v, np.arange(1, len(v)+1)/len(v), color=c, lw=2, label=lbl)
    axes[1].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[1].set_ylabel('Cumulative fraction', fontsize=10)
    axes[1].set_title('CDF of per-aisle mean duration', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    axes[1].legend(fontsize=9);  axes[1].grid(alpha=0.3)
    dBv = np.asarray(acmp['dB'], dtype=float);  dCv = np.asarray(acmp['dC'], dtype=float)
    axes[2].hist(dBv, bins=50, color=_B_COL, alpha=0.55, edgecolor='white', label=f'B vs A  mean={dBv.mean():.2f}%')
    axes[2].hist(dCv, bins=50, color=_C_COL, alpha=0.55, edgecolor='white', label=f'C vs A  mean={dCv.mean():.2f}%')
    axes[2].axvline(0,         color='black', lw=1.5, linestyle='--')
    axes[2].axvline(dBv.mean(), color=_B_COL, lw=2,   linestyle='--')
    axes[2].axvline(dCv.mean(), color=_C_COL, lw=2,   linestyle='--')
    axes[2].set_xlabel('Δ (X − A) / A  %', fontsize=10)
    axes[2].set_ylabel('Aisle count', fontsize=10)
    axes[2].set_title('Per-aisle % duration change (vs Uniform A)', fontsize=10)
    axes[2].xaxis.set_major_formatter(mticker.PercentFormatter())
    axes[2].legend(fontsize=9);  axes[2].grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot7_per_aisle.png'))

    imp_B = (acmp['dB'] < 0).sum();  imp_C = (acmp['dC'] > 0).sum()
    log.info(f'  Aisles faster with B: {imp_B}/{len(acmp)}   slower with C: {imp_C}/{len(acmp)}')
    log.info(f'  Mean delta  B: {dBv.mean():.2f}%   C: {dCv.mean():.2f}%')

    # ── plot 8: mean task duration per batch ──────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.suptitle(
        f'Mean Aisle Task Duration per Batch  '
        f'(dots = per-batch mean, line = rolling {_WIN}-batch mean)  [{name}]',
        fontsize=13, fontweight='bold',
    )
    for df_t, c, lbl in [(df_tA, _A_COL, 'Uniform (A)'),
                         (df_tB, _B_COL, 'Trip-Min (B)'),
                         (df_tC, _C_COL, 'Trip-Max (C)')]:
        tpb   = (df_t.groupby('batch_id')['duration']
                 .mean().reset_index().sort_values('batch_id'))
        x     = np.asarray(tpb['batch_id'])
        y_raw = np.asarray(tpb['duration'])
        y_roll = np.asarray(pd.Series(y_raw).rolling(_WIN, min_periods=1).mean())
        ax.scatter(x, y_raw,  color=c, alpha=0.25, s=10, zorder=2)
        ax.plot(x,    y_roll, color=c, lw=2, label=lbl, zorder=3)
    ax.set_xlabel('Batch ID', fontsize=10)
    ax.set_ylabel('Mean task duration (sim time units)', fontsize=10)
    ax.legend(fontsize=9);  ax.grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot8_task_duration_per_batch.png'))

    log.info(f'  Saved → {run_dir}')


def run_config(cfg: dict, shared: dict, base_dir: str, log: logging.Logger) -> None:
    """Backward-compatible wrapper: simulate then analyse one regression config."""
    sim_result = _run_config_sim(cfg, shared, base_dir, log)
    _run_config_analysis(sim_result, shared, log)


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
                        help='Regression configs to simulate in parallel '
                             '(each uses 3 A/B/C workers; default=1 = sequential)')
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

    log.info(f'Discovered {len(pairs)} DB pair(s):')
    for label, inv_db, aff_db in pairs:
        log.info(f'  {label}')
        log.info(f'    inv : {inv_db}')
        log.info(f'    aff : {aff_db}')

    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}')
        log.info(f'  Dataset : {label}')
        log.info(f'{"="*64}')
        pair_dir = os.path.join(base_dir, label)
        shared   = build_shared_assets(inv_db, aff_db, log)

        if args.config_workers > 1:
            # ── parallel simulations ──────────────────────────────────────
            # Simulations run in threads; each thread spawns its own
            # ProcessPoolExecutor(3) for A/B/C workers.  Analyses stay
            # sequential because matplotlib pyplot is not thread-safe.
            log.info(f'  Running {len(REGRESSION_CONFIGS)} configs with '
                     f'{args.config_workers} parallel simulation thread(s)...')
            sim_results: dict[str, dict] = {}
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=args.config_workers) as pool:
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
                        log.error(f'  Config [{cfg_name}] FAILED: {exc}',
                                  exc_info=True)

            log.info('  All simulations done — running analyses sequentially...')
            for cfg in REGRESSION_CONFIGS:
                cfg_name = cfg.get('name', '?')
                if cfg_name in sim_results:
                    _run_config_analysis(sim_results[cfg_name], shared, log)
        else:
            # ── sequential (default) ──────────────────────────────────────
            for cfg in REGRESSION_CONFIGS:
                run_config(cfg, shared, pair_dir, log)

    log.info(f'\nAll {len(pairs)} dataset(s) × {len(REGRESSION_CONFIGS)} config(s) complete.'
             f'  Root: {base_dir}')


if __name__ == '__main__':
    main()
