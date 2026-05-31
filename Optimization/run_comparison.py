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
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, '..', 'Warehouse')))
sys.path.insert(0, _HERE)

from Aisle_Storage import Aisle
from Affinity_Store import AffinityStore
from Inventory_Management import Inventory_Manager
from generate_inventory import load_inventory_from_db
from Pick import PickConfig, PickSimulation
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig, Task

from Inventory_Management import (
    LoadParams,
    build_load_minimizing_assignment_fn,
    build_load_maximizing_assignment_fn,
)
from Picking_Data import (
    BatchStats, TaskStats,
    create_run, init_run_db,
    save_batch_stats, load_batch_stats,
    save_task_stats, load_task_stats,
)
from Simulation_Analytics import (
    extract_batch_stats, extract_task_stats,
    flag_batch_outliers, flag_task_outliers,
)
from Workload import WorkloadParams

# ── simulation constants ───────────────────────────────────────────────────────
SEED_WORLD       = 42
SEED_BATCHES     = 1337
N_BATCHES        = 1_000
K_PICKERS        = 25
_CHECKPOINT      = 100
_WIN             = 50
_BATCH_MEAN_FRAC = 0.25
_BATCH_STD_FRAC  = 0.03
_BINS_PER_AISLE      = 25 * 20   # 500 — matches AisleConfig(bayX=25, bayY=20)
_N_PALLET_TYPES      = 48         # 4 sizes × 6 categories × 2 handling
_N_SINGLETON_TYPES   = 12         # 1 type  × 6 categories × 2 handling
_SINGLETON_MAX_DIM   = 16         # Singleton.max_width — used to classify cartons
_SING_FRACTION_CAP   = 0.35       # singleton bins ≤ 35% of total warehouse bins
_TARGET_FILL         = 0.90       # target bin fill rate after overstock sampling

_DEFAULT_BATCHES_DIR = os.path.normpath(
    os.path.join(_HERE, '..', 'Warehouse', 'generated', 'batches')
)

_CATEGORIES  = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_ALL_SIZES   = ['small', 'medium', 'large', 'extra_large']
_CONV_SIZES  = ['small', 'medium', 'large']
_CONV_PROBS  = [0.25, 0.50, 0.25]
_NCONV_SIZES = ['medium', 'large', 'extra_large']
_NCONV_PROBS = [0.20, 0.50, 0.30]
_SIZE_ORDER  = ['small', 'medium', 'large', 'extra_large']
_SIZE_LABELS = ['Small', 'Medium', 'Large', 'Extra-Large']

_A_COL      = '#5b9bd5'
_B_COL      = '#f4a030'
_C_COL      = '#70ad47'
_TRAVEL_COL = '#a9a9a9'

# ── warehouse configuration ────────────────────────────────────────────────────
_AISLE_CFGS = []
for _size in _ALL_SIZES:
    for _cat in _CATEGORIES:
        _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'pallet', 25, 20, [_size], None))
        _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'pallet', 25, 20, [_size], None))
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'singleton', 25, 20, _CONV_SIZES,  _CONV_PROBS))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'singleton', 25, 20, _NCONV_SIZES, _NCONV_PROBS))


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
    fmt = logging.Formatter('%(asctime)s  %(message)s', datefmt='%H:%M:%S')
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


def _save_resume(run_dir: str, next_batch_id: int, run_ids: dict) -> None:
    state = {
        'next_batch_id': next_batch_id,
        'run_ids'      : run_ids,
        'random_state' : random.getstate(),
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


def _tdf(stats, aisle_size_map, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W_a'        : s.W_a,
        'lift_sum'   : s.lift_sum,
        'num_bins'   : s.num_bins_visited,
        'total_items': s.total_items,
        'pallet_size': aisle_size_map.get(s.aisle_id),
        'unit_type'  : aisle_unittype_map.get(s.aisle_id),
        'handling'   : aisle_handling_map.get(s.aisle_id),
    } for s in stats])


def _pallet_df(df):
    d = df[(df['unit_type'] == 'pallet') & (df['pallet_size'].notna())].copy()
    d['pallet_size'] = pd.Categorical(d['pallet_size'], categories=_SIZE_ORDER, ordered=True)
    return d




def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


# ── DB helpers ─────────────────────────────────────────────────────────────────


def discover_db_pairs(batches_dir: str) -> list[tuple[str, str, str]]:
    """Scan batches_dir and return (label, inventory_db, affinity_db) for every valid pair."""
    pairs: list[tuple[str, str, str]] = []
    if not os.path.isdir(batches_dir):
        return pairs
    for batch_name in sorted(os.listdir(batches_dir)):
        batch_path = os.path.join(batches_dir, batch_name)
        if not os.path.isdir(batch_path):
            continue
        for profile_name in sorted(os.listdir(batch_path)):
            profile_path = os.path.join(batch_path, profile_name)
            if not os.path.isdir(profile_path):
                continue
            inv_db = os.path.join(profile_path, 'inventory', 'inventory.db')
            aff_db = os.path.join(profile_path, 'affinity', 'affinity.db')
            if os.path.exists(inv_db) and os.path.exists(aff_db):
                pairs.append((f'{batch_name}__{profile_name}', inv_db, aff_db))
    return pairs


def find_latest_db_pairs(batches_dir: str) -> list[tuple[str, str, str]]:
    """Return DB pairs from the most recently generated batch only.

    Batch directories are named batch_YYYYMMDD_HHMMSS, so the last entry
    when sorted lexicographically is always the newest.  Walks backwards
    through batches until one with valid inventory+affinity pairs is found.
    """
    if not os.path.isdir(batches_dir):
        return []
    batch_names = sorted([
        d for d in os.listdir(batches_dir)
        if os.path.isdir(os.path.join(batches_dir, d))
    ])
    for batch_name in reversed(batch_names):
        batch_path = os.path.join(batches_dir, batch_name)
        pairs: list[tuple[str, str, str]] = []
        for profile_name in sorted(os.listdir(batch_path)):
            profile_path = os.path.join(batch_path, profile_name)
            if not os.path.isdir(profile_path):
                continue
            inv_db = os.path.join(profile_path, 'inventory', 'inventory.db')
            aff_db = os.path.join(profile_path, 'affinity', 'affinity.db')
            if os.path.exists(inv_db) and os.path.exists(aff_db):
                pairs.append((f'{batch_name}__{profile_name}', inv_db, aff_db))
        if pairs:
            return pairs
    return []


def _stock_to_target_fill(
    manager  : 'Inventory_Manager',
    inventory,
    target   : float = _TARGET_FILL,
    log      : logging.Logger | None = None,
) -> int:
    """Oversample inventory until the manager reaches *target* bin fill rate.

    Every SKU must already be stocked at least once before calling this.
    Additional bins are drawn weighted by demand.frequency so fast-moving
    SKUs accumulate more stock locations.  Enqueues a batch of candidates
    and clears any that couldn't be placed due to full aisles.

    Returns the number of extra bins successfully stocked.
    """
    total_bins  = len(manager.warehouse.bins)
    target_bins = round(target * total_bins)
    current     = len(manager.unavailable)

    if current >= target_bins:
        return 0

    needed  = target_bins - current
    weights = [c.demand.frequency for c in inventory.cartons]
    total_w = sum(weights)
    norm_w  = [w / total_w for w in weights]

    # Sample generously — some will fail if bins of that type are full.
    # Unplaced items stay in the queue (FIFO) and are retried each batch
    # as picks free up bin slots.
    sample = random.choices(inventory.cartons, weights=norm_w, k=needed * 3)
    before = len(manager.unavailable)
    manager.enqueue_all(sample, quantity=1)
    added = len(manager.unavailable) - before

    if log:
        fill_pct = len(manager.unavailable) / total_bins
        log.info(f'  Overstock: +{added:,} bins  fill={fill_pct:.1%}  '
                 f'(target {target:.0%})')
    return added


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

    # Count singleton vs pallet cartons by their actual dimensions
    n_singleton = sum(
        1 for c in inventory.cartons
        if max(c.length, c.width, c.height) <= _SINGLETON_MAX_DIM
    )
    n_pallet = n_skus - n_singleton

    # ── Warehouse sizing ───────────────────────────────────────────────────────
    # Three constraints must all hold simultaneously:
    #   1. Every SKU gets at least one bin (n_sing ≤ sing_bins, n_pall ≤ pall_bins)
    #   2. Singleton bins ≤ _SING_FRACTION_CAP (35%) of total bins
    #   3. Total bins large enough to reach _TARGET_FILL (90%) via overstock
    #
    # (1) sets the minimum replicas per type.
    # (2) may require extra pallet replicas when singletons are abundant.
    # (3) sets a lower bound on total_bins = n_skus / _TARGET_FILL.

    sing_replicas = max(1, math.ceil(n_singleton / (_N_SINGLETON_TYPES * _BINS_PER_AISLE)))
    pall_replicas = max(1, math.ceil(n_pallet    / (_N_PALLET_TYPES    * _BINS_PER_AISLE)))

    # Enforce singleton cap: sing_bins/(sing_bins + pall_bins) ≤ _SING_FRACTION_CAP
    sing_bins_now = _N_SINGLETON_TYPES * sing_replicas * _BINS_PER_AISLE
    min_pall_bins = math.ceil(
        sing_bins_now * (1 - _SING_FRACTION_CAP) / _SING_FRACTION_CAP
    )
    pall_replicas = max(
        pall_replicas,
        math.ceil(min_pall_bins / (_N_PALLET_TYPES * _BINS_PER_AISLE)),
    )

    # Enforce 90% fill target: total_bins ≥ n_skus / _TARGET_FILL
    sing_bins  = _N_SINGLETON_TYPES * sing_replicas * _BINS_PER_AISLE
    pall_bins  = _N_PALLET_TYPES    * pall_replicas * _BINS_PER_AISLE
    min_total  = math.ceil(n_skus / _TARGET_FILL)
    if sing_bins + pall_bins < min_total:
        extra_pall = math.ceil((min_total - sing_bins - pall_bins) / (_N_PALLET_TYPES * _BINS_PER_AISLE))
        pall_replicas += extra_pall

    total_aisles = _N_SINGLETON_TYPES * sing_replicas + _N_PALLET_TYPES * pall_replicas
    total_bins   = total_aisles * _BINS_PER_AISLE
    sing_frac    = (_N_SINGLETON_TYPES * sing_replicas) / total_aisles

    # _AISLE_CFGS is ordered: 48 pallet types first, then 12 singleton types
    _pall_w = pall_replicas / total_aisles
    _sing_w = sing_replicas / total_aisles
    aisle_splits = [_pall_w] * _N_PALLET_TYPES + [_sing_w] * _N_SINGLETON_TYPES

    log.info(f'  Inventory : {n_pallet:,} pallet  {n_singleton:,} singleton cartons')
    log.info(f'  Warehouse : {_N_PALLET_TYPES}×{pall_replicas} pallet'
             f' + {_N_SINGLETON_TYPES}×{sing_replicas} singleton'
             f' = {total_aisles} aisles / {total_bins:,} bins'
             f'  sing={sing_frac:.1%}  target_fill={_TARGET_FILL:.0%}')

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
        p           = json.load(open(param_path))
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

    Aisle.next_aisle_id = 1
    random.seed(SEED_WORLD)
    warehouse_A = Warehouse_Builder().from_config(warehouse_cfg).build()
    random.seed(SEED_WORLD + 100)
    manager_A   = Inventory_Manager(warehouse_A)
    manager_A.enqueue_all(inventory.cartons, quantity=1)
    log.info(f'  Warehouse A base stock: {len(manager_A.unavailable):,} / {total_bins:,} bins'
             f'  ({len(manager_A.unavailable)/total_bins:.1%})')
    _stock_to_target_fill(manager_A, inventory, target=_TARGET_FILL, log=log)
    placed_A = len(manager_A.unavailable)
    log.info(f'  Warehouse A final     : {placed_A:,} / {total_bins:,} bins  ({placed_A/total_bins:.1%})')

    return dict(
        inventory          = inventory,
        affinity_store     = affinity_store,
        warehouse_A        = warehouse_A,
        manager_A          = manager_A,
        batch_cfg          = batch_cfg,
        load_params        = load_params,
        warehouse_cfg      = warehouse_cfg,
        total_aisles       = total_aisles,
        total_bins         = total_bins,
        aisle_size_map     = {a.aisle_id: a.storage_size  for a in warehouse_A.aisles},
        aisle_unittype_map = {a.aisle_id: a.unit_type     for a in warehouse_A.aisles},
        aisle_handling_map = {a.aisle_id: a.handling_type for a in warehouse_A.aisles},
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

def run_config(cfg: dict, shared: dict, base_dir: str, log: logging.Logger) -> None:
    name = cfg.get('name') or (
        f"w{cfg.get('pick_weight_coef',1.1)}_v{cfg.get('pick_volume_coef',1e-3)}"
        f"_i{cfg.get('pick_intercept',1.0)}_c{cfg.get('cart_swap_coef',10.0)}"
    )
    pick_cfg = PickConfig(
        num_pickers      = K_PICKERS,
        x_move_time      = cfg.get('x_move_time',      1.0),
        y_move_time      = cfg.get('y_move_time',      0.5),
        pick_intercept   = cfg.get('pick_intercept',   1.0),
        pick_weight_coef = cfg.get('pick_weight_coef', 1.1),
        pick_volume_coef = cfg.get('pick_volume_coef', 1e-3),
        cart_swap_coef   = cfg.get('cart_swap_coef',   10.0),
    )
    wp      = WorkloadParams.from_pick_config(pick_cfg)
    run_dir = os.path.join(base_dir, name)
    db_path = os.path.join(run_dir, 'sim.db')
    os.makedirs(run_dir, exist_ok=True)

    inventory          = shared['inventory']
    affinity_store     = shared['affinity_store']
    warehouse_A        = shared['warehouse_A']
    manager_A          = shared['manager_A']
    batch_cfg          = shared['batch_cfg']
    load_params        = shared['load_params']
    warehouse_cfg      = shared['warehouse_cfg']
    total_aisles       = shared['total_aisles']
    total_bins         = shared['total_bins']
    aisle_size_map     = shared['aisle_size_map']
    aisle_unittype_map = shared['aisle_unittype_map']
    aisle_handling_map = shared['aisle_handling_map']

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
        'x_move_time'     : pick_cfg.x_move_time,
        'y_move_time'     : pick_cfg.y_move_time,
        'num_pickers'     : pick_cfg.num_pickers,
        'total_aisles'    : total_aisles,
        'total_bins'      : total_bins,
        'n_skus'          : len(inventory.cartons),
        'bin_slack_pct'   : round((total_bins / len(inventory.cartons) - 1) * 100, 2),
        'batch_mean_frac' : _BATCH_MEAN_FRAC,
        'n_batches'       : N_BATCHES,
        'seed_world'      : SEED_WORLD,
        'seed_batches'    : SEED_BATCHES,
    }
    with open(os.path.join(run_dir, 'config.json'), 'w') as f:
        json.dump(config_record, f, indent=2)

    # ── warehouses B and C — uniform initial stock, strategy-specific reorders ──
    # All three warehouses start from the same uniform placement (same seed as A)
    # so the comparison reflects only the reorder strategy, not placement bias.
    Aisle.next_aisle_id = 1
    random.seed(SEED_WORLD)
    warehouse_B = Warehouse_Builder().from_config(warehouse_cfg).build()
    log.info('  Stocking B (uniform initial)...')
    t0 = time.perf_counter()
    random.seed(SEED_WORLD + 100)
    manager_B = Inventory_Manager(warehouse_B, affinity=affinity_store)
    manager_B.enqueue_all(inventory.cartons, quantity=1)
    _stock_to_target_fill(manager_B, inventory, target=_TARGET_FILL)
    manager_B.init_lift_state(affinity_store)
    manager_B.assignment_fn = build_load_minimizing_assignment_fn(
        load_params, affinity_store, wp,
        manager_B._aisle_sku_sets, manager_B._aisle_lift_sum,
        manager_B._aisle_idx_sets,
    )
    log.info(f'  B ready  {time.perf_counter()-t0:.1f}s  ({len(manager_B.unavailable):,} bins)')

    Aisle.next_aisle_id = 1
    random.seed(SEED_WORLD)
    warehouse_C = Warehouse_Builder().from_config(warehouse_cfg).build()
    log.info('  Stocking C (uniform initial)...')
    t0 = time.perf_counter()
    random.seed(SEED_WORLD + 100)
    manager_C = Inventory_Manager(warehouse_C, affinity=affinity_store)
    manager_C.enqueue_all(inventory.cartons, quantity=1)
    _stock_to_target_fill(manager_C, inventory, target=_TARGET_FILL)
    manager_C.init_lift_state(affinity_store)
    manager_C.assignment_fn = build_load_maximizing_assignment_fn(
        load_params, affinity_store, wp,
        manager_C._aisle_sku_sets, manager_C._aisle_lift_sum,
        manager_C._aisle_idx_sets,
    )
    log.info(f'  C ready  {time.perf_counter()-t0:.1f}s  ({len(manager_C.unavailable):,} bins)')

    # ── DB init / resume ──────────────────────────────────────────────────────
    resume = _load_resume(run_dir)
    if resume:
        run_a   = resume['run_ids']['A']
        run_b   = resume['run_ids']['B']
        run_c   = resume['run_ids']['C']
        start_i = resume['next_batch_id']
        random.setstate(resume['random_state'])
        log.info(f'  Resuming from batch {start_i}  (run_ids A={run_a} B={run_b} C={run_c})')
    else:
        init_run_db(db_path)
        run_a   = create_run(db_path, 'uniform_assignment')
        run_b   = create_run(db_path, 'load_minimizing_assignment')
        run_c   = create_run(db_path, 'load_maximizing_assignment')
        start_i = 0
        random.seed(SEED_BATCHES)
        log.info(f'  New run  run_ids A={run_a} B={run_b} C={run_c}')

    run_ids = {'A': run_a, 'B': run_b, 'C': run_c}

    # ── simulation loop ───────────────────────────────────────────────────────
    pb_A: list = [];  pb_B: list = [];  pb_C: list = []
    pt_A: list = [];  pt_B: list = [];  pt_C: list = []
    skipped = 0
    t_loop  = time.perf_counter()

    # Per-phase accumulators for timing breakdown (reset each checkpoint window)
    _t: dict[str, float] = {
        'batch': 0.0, 'tasks': 0.0,
        'sim_A': 0.0, 'sim_B': 0.0, 'sim_C': 0.0,
        'stats': 0.0, 'save':  0.0,
    }

    for i in range(start_i, N_BATCHES):
        # Restock bins depleted by the previous batch's picks — called once
        # here instead of after every pick event to avoid O(N_bins) hot loop.
        manager_A.check_reorders()
        manager_B.check_reorders()
        manager_C.check_reorders()

        _t0 = time.perf_counter()
        batch = Batch(batch_cfg, inventory, affinity=affinity_store)
        _t['batch'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        ta = Task.from_batch(batch, warehouse_A, manager=manager_A)
        tb = Task.from_batch(batch, warehouse_B, manager=manager_B)
        tc = Task.from_batch(batch, warehouse_C, manager=manager_C)
        _t['tasks'] += time.perf_counter() - _t0

        if not ta or not tb or not tc:
            skipped += 1
            continue

        _t0 = time.perf_counter();  ea = PickSimulation(ta, pick_cfg, manager=manager_A).run();  _t['sim_A'] += time.perf_counter() - _t0
        _t0 = time.perf_counter();  eb = PickSimulation(tb, pick_cfg, manager=manager_B).run();  _t['sim_B'] += time.perf_counter() - _t0
        _t0 = time.perf_counter();  ec = PickSimulation(tc, pick_cfg, manager=manager_C).run();  _t['sim_C'] += time.perf_counter() - _t0

        _t0 = time.perf_counter()
        bsA = extract_batch_stats(ea, batch_id=i, k_pickers=K_PICKERS, run_id=run_a)
        bsB = extract_batch_stats(eb, batch_id=i, k_pickers=K_PICKERS, run_id=run_b)
        bsC = extract_batch_stats(ec, batch_id=i, k_pickers=K_PICKERS, run_id=run_c)
        _lc: dict = {}   # shared across A/B/C — same task SKU sets hit cache for B and C
        tsA = extract_task_stats(ea, ta, batch_id=i, affinity=affinity_store, wp=wp, run_id=run_a, lift_cache=_lc)
        tsB = extract_task_stats(eb, tb, batch_id=i, affinity=affinity_store, wp=wp, run_id=run_b, lift_cache=_lc)
        tsC = extract_task_stats(ec, tc, batch_id=i, affinity=affinity_store, wp=wp, run_id=run_c, lift_cache=_lc)
        _t['stats'] += time.perf_counter() - _t0

        pb_A.append(bsA);  pb_B.append(bsB);  pb_C.append(bsC)
        pt_A.extend(tsA);  pt_B.extend(tsB);  pt_C.extend(tsC)

        if len(pb_A) >= _CHECKPOINT:
            _t0 = time.perf_counter()
            save_batch_stats(db_path, run_a, pb_A)
            save_batch_stats(db_path, run_b, pb_B)
            save_batch_stats(db_path, run_c, pb_C)
            save_task_stats(db_path, run_a, pt_A)
            save_task_stats(db_path, run_b, pt_B)
            save_task_stats(db_path, run_c, pt_C)
            _save_resume(run_dir, i + 1, run_ids)
            _t['save'] += time.perf_counter() - _t0

            wall   = time.perf_counter() - t_loop
            n_done = i + 1 - start_i
            rate   = n_done / wall
            total  = sum(_t.values())
            log.info(
                f'  Batch {i+1:4d}/{N_BATCHES}  '
                f'A={bsA.duration:.0f}  B={bsB.duration:.0f}  C={bsC.duration:.0f}  '
                f'{rate:.2f} batches/s'
            )
            log.info(
                f'    timing/{_CHECKPOINT} batches — '
                f'batch={_t["batch"]:.1f}s  tasks={_t["tasks"]:.1f}s  '
                f'simA={_t["sim_A"]:.1f}s  simB={_t["sim_B"]:.1f}s  simC={_t["sim_C"]:.1f}s  '
                f'stats={_t["stats"]:.1f}s  save={_t["save"]:.1f}s  '
                f'total={total:.1f}s'
            )
            for k in _t:
                _t[k] = 0.0
            pb_A.clear();  pb_B.clear();  pb_C.clear()
            pt_A.clear();  pt_B.clear();  pt_C.clear()

    # flush remainder
    for run_id, pb, pt in [(run_a, pb_A, pt_A), (run_b, pb_B, pt_B), (run_c, pb_C, pt_C)]:
        if pb:
            save_batch_stats(db_path, run_id, pb)
            save_task_stats(db_path, run_id, pt)

    elapsed = time.perf_counter() - t_loop
    done    = N_BATCHES - start_i - skipped
    log.info(f'  Done: {done} triplets in {elapsed:.1f}s  ({skipped} skipped)')

    # remove resume checkpoint — run is complete
    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)

    # ── analysis ──────────────────────────────────────────────────────────────
    bs_fA = flag_batch_outliers(load_batch_stats(db_path, run_a))
    bs_fB = flag_batch_outliers(load_batch_stats(db_path, run_b))
    bs_fC = flag_batch_outliers(load_batch_stats(db_path, run_c))
    ts_fA = flag_task_outliers(load_task_stats(db_path, run_a))
    ts_fB = flag_task_outliers(load_task_stats(db_path, run_b))
    ts_fC = flag_task_outliers(load_task_stats(db_path, run_c))

    df_bA = _bdf([s for s in bs_fA if not s.is_outlier])
    df_bB = _bdf([s for s in bs_fB if not s.is_outlier])
    df_bC = _bdf([s for s in bs_fC if not s.is_outlier])
    df_tA = _tdf([s for s in ts_fA if not s.is_outlier], aisle_size_map, aisle_unittype_map, aisle_handling_map)
    df_tB = _tdf([s for s in ts_fB if not s.is_outlier], aisle_size_map, aisle_unittype_map, aisle_handling_map)
    df_tC = _tdf([s for s in ts_fC if not s.is_outlier], aisle_size_map, aisle_unittype_map, aisle_handling_map)

    # summary CSVs
    bcols  = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols  = ['duration', 'W_a', 'lift_sum', 'num_bins']
    summ_b = pd.concat(
        [df_bA[bcols].agg(['mean','median','std']).T,
         df_bB[bcols].agg(['mean','median','std']).T,
         df_bC[bcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Load-Min (B)', 'Load-Max (C)']).round(3)
    summ_t = pd.concat(
        [df_tA[tcols].agg(['mean','median','std']).T,
         df_tB[tcols].agg(['mean','median','std']).T,
         df_tC[tcols].agg(['mean','median','std']).T],
        axis=1, keys=['Uniform (A)', 'Load-Min (B)', 'Load-Max (C)']).round(3)
    summ_b.to_csv(os.path.join(run_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(run_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    # ── plot 1: batch duration ─────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Batch Completion Time  [{name}]', fontsize=13, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['duration'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['duration'].values, 'B — Load-Minimizing', _B_COL),
        (axes[2], df_bC['duration'].values, 'C — Load-Maximizing', _C_COL),
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
        (axes[1], df_tB['duration'].values, 'B — Load-Minimizing', _B_COL),
        (axes[2], df_tC['duration'].values, 'C — Load-Maximizing', _C_COL),
    ]:
        _kde_plot(ax, data, color, bins=50)
        ax.set_xlabel('Task duration  (sim time units)', fontsize=10)
        ax.set_ylabel('Count', fontsize=10)
        ax.set_title(f'{label}  (n={len(data):,})', fontsize=10)
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot2_task_duration.png'))

    # ── plot 3: completion rate ────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7))
    fig.suptitle(f'Batch Completion Rate  (rolling {_WIN}-batch window)  [{name}]',
                 fontsize=13, fontweight='bold')
    for ax, col, ylabel, title in [
        (ax1, 'completion_rate', 'Items / time unit',    'Throughput rate'),
        (ax2, 'duration',        'Duration (time units)', 'Batch completion time'),
    ]:
        ax.plot(df_bA.sort_values('batch_id')['batch_id'].values, _roll(df_bA, col, _WIN),
                color=_A_COL, lw=2, label='Uniform (A)')
        ax.plot(df_bB.sort_values('batch_id')['batch_id'].values, _roll(df_bB, col, _WIN),
                color=_B_COL, lw=2, label='Load-Min (B)')
        ax.plot(df_bC.sort_values('batch_id')['batch_id'].values, _roll(df_bC, col, _WIN),
                color=_C_COL, lw=2, label='Load-Max (C)')
        ax.set_ylabel(ylabel, fontsize=10);  ax.set_title(title, fontsize=10)
        ax.legend(fontsize=9);  ax.grid(alpha=0.3)
    ax2.set_xlabel('Batch ID', fontsize=10)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot3_completion_rate.png'))

    # ── plot 4: picker concurrency ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.5))
    fig.suptitle(f'Picker Concurrency  [{name}]', fontsize=12, fontweight='bold')
    for ax, data, label, color in [
        (axes[0], df_bA['avg_concurrent_pickers'].values, 'A — Uniform',         _A_COL),
        (axes[1], df_bB['avg_concurrent_pickers'].values, 'B — Load-Minimizing', _B_COL),
        (axes[2], df_bC['avg_concurrent_pickers'].values, 'C — Load-Maximizing', _C_COL),
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
    for dfb, c, lbl in [(df_bA, _A_COL, 'Uniform'), (df_bB, _B_COL, 'Load-Min'), (df_bC, _C_COL, 'Load-Max')]:
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
    axes[2].set_xticks(x);  axes[2].set_xticklabels(['Uniform (A)', 'Load-Min (B)', 'Load-Max (C)'])
    axes[2].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100))
    axes[2].set_ylabel('Mean fraction (%)', fontsize=10);  axes[2].set_title('Aggregate mean split', fontsize=10)
    axes[2].legend(fontsize=8);  axes[2].grid(axis='x', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot5_picker_utilisation.png'))

    # ── plot 6a: pallet size ───────────────────────────────────────────────────
    dp_A = _pallet_df(df_tA);  dp_B = _pallet_df(df_tB);  dp_C = _pallet_df(df_tC)
    mdA  = [dp_A[dp_A['pallet_size']==s]['duration'].mean() for s in _SIZE_ORDER]
    mdB  = [dp_B[dp_B['pallet_size']==s]['duration'].mean() for s in _SIZE_ORDER]
    mdC  = [dp_C[dp_C['pallet_size']==s]['duration'].mean() for s in _SIZE_ORDER]
    x2   = np.arange(len(_SIZE_ORDER));  w2 = 0.25

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(f'Pallet-Aisle Task Analysis by Storage Size  [{name}]', fontsize=13, fontweight='bold')
    axes[0].bar(x2 - w2, mdA, width=w2, color=_A_COL, alpha=0.85, label='Uniform (A)')
    axes[0].bar(x2,       mdB, width=w2, color=_B_COL, alpha=0.85, label='Load-Min (B)')
    axes[0].bar(x2 + w2, mdC, width=w2, color=_C_COL, alpha=0.85, label='Load-Max (C)')
    axes[0].set_xticks(x2);  axes[0].set_xticklabels(_SIZE_LABELS)
    axes[0].set_ylabel('Mean task duration', fontsize=10)
    axes[0].set_title('Mean task duration per pallet size', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    dB2 = [(b-a)/abs(a)*100 if a else 0 for a, b in zip(mdA, mdB)]
    dC2 = [(c-a)/abs(a)*100 if a else 0 for a, c in zip(mdA, mdC)]
    axes[1].bar(x2 - w2/2, dB2, width=w2, color=[_B_COL if d < 0 else '#c00000' for d in dB2], alpha=0.85, label='B vs A')
    axes[1].bar(x2 + w2/2, dC2, width=w2, color=[_C_COL if d > 0 else '#c00000' for d in dC2], alpha=0.85, label='C vs A')
    axes[1].axhline(0, color='black', lw=1)
    for j, (dB, dC) in enumerate(zip(dB2, dC2)):
        axes[1].text(j - w2/2, dB + (0.3 if dB >= 0 else -0.6), f'{dB:.1f}%', ha='center', fontsize=8)
        axes[1].text(j + w2/2, dC + (0.3 if dC >= 0 else -0.6), f'{dC:.1f}%', ha='center', fontsize=8)
    axes[1].set_xticks(x2);  axes[1].set_xticklabels(_SIZE_LABELS)
    axes[1].set_ylabel('Δ (X − A) / A  %', fontsize=10);  axes[1].set_title('Duration delta per size', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter())
    axes[1].legend(fontsize=8);  axes[1].grid(axis='y', alpha=0.3)

    scols = ['#9dc3e6', '#5b9bd5', '#2e75b6', '#1f4e79']
    for sz, sc, sl in zip(_SIZE_ORDER, scols, _SIZE_LABELS):
        vA = dp_A[dp_A['pallet_size']==sz]['duration'].values
        vB = dp_B[dp_B['pallet_size']==sz]['duration'].values
        vC = dp_C[dp_C['pallet_size']==sz]['duration'].values
        lo = min(vA.min(), vB.min(), vC.min());  hi = max(vA.max(), vB.max(), vC.max())
        xs = np.linspace(lo, hi, 300)
        if len(vA) > 1: axes[2].plot(xs, gaussian_kde(vA, 'silverman')(xs), color=sc, lw=2,   ls='-',  label=f'{sl} A')
        if len(vB) > 1: axes[2].plot(xs, gaussian_kde(vB, 'silverman')(xs), color=sc, lw=2,   ls='--', label=f'{sl} B')
        if len(vC) > 1: axes[2].plot(xs, gaussian_kde(vC, 'silverman')(xs), color=sc, lw=1.5, ls=':',  label=f'{sl} C')
    axes[2].set_xlabel('Task duration', fontsize=10);  axes[2].set_ylabel('Density', fontsize=10)
    axes[2].set_title('KDE: solid=A, dashed=B, dot=C', fontsize=10)
    axes[2].legend(fontsize=6, ncol=3);  axes[2].grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot6a_pallet_size.png'))

    # ── plot 6b: handling breakdown ────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f'Mean Task Duration per Pallet Size × Handling  [{name}]', fontsize=12, fontweight='bold')
    for ax, h in [(axes[0], 'conveyable'), (axes[1], 'non-conveyable')]:
        mA = [dp_A[(dp_A['pallet_size']==s)&(dp_A['handling']==h)]['duration'].mean() for s in _SIZE_ORDER]
        mB = [dp_B[(dp_B['pallet_size']==s)&(dp_B['handling']==h)]['duration'].mean() for s in _SIZE_ORDER]
        mC = [dp_C[(dp_C['pallet_size']==s)&(dp_C['handling']==h)]['duration'].mean() for s in _SIZE_ORDER]
        ax.bar(x2 - w2, mA, width=w2, color=_A_COL, alpha=0.85, label='Uniform (A)')
        ax.bar(x2,       mB, width=w2, color=_B_COL, alpha=0.85, label='Load-Min (B)')
        ax.bar(x2 + w2, mC, width=w2, color=_C_COL, alpha=0.85, label='Load-Max (C)')
        ax.set_xticks(x2);  ax.set_xticklabels(_SIZE_LABELS)
        ax.set_title(f'{h.capitalize()} pallet aisles', fontsize=10)
        ax.set_ylabel('Mean task duration', fontsize=10)
        ax.legend(fontsize=9);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(run_dir, 'plot6b_pallet_size_handling.png'))

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
                      (acmp['B'], 'Load-Min (B)', _B_COL),
                      (acmp['C'], 'Load-Max (C)', _C_COL)]:
        axes[0].hist(v, bins=50, color=c, alpha=0.50, edgecolor='white', label=f'{lbl}  μ={v.mean():.1f}')
    axes[0].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[0].set_ylabel('Aisle count', fontsize=10)
    axes[0].set_title('Distribution of aisle mean durations', fontsize=10)
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)
    for v, lbl, c in [(np.sort(acmp['A'].values), 'Uniform (A)',   _A_COL),
                      (np.sort(acmp['B'].values), 'Load-Min (B)', _B_COL),
                      (np.sort(acmp['C'].values), 'Load-Max (C)', _C_COL)]:
        axes[1].plot(v, np.arange(1, len(v)+1)/len(v), color=c, lw=2, label=lbl)
    axes[1].set_xlabel('Per-aisle mean task duration', fontsize=10)
    axes[1].set_ylabel('Cumulative fraction', fontsize=10)
    axes[1].set_title('CDF of per-aisle mean duration', fontsize=10)
    axes[1].yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    axes[1].legend(fontsize=9);  axes[1].grid(alpha=0.3)
    dBv = acmp['dB'].values;  dCv = acmp['dC'].values
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
    log.info(f'  Saved → {run_dir}')


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Warehouse assignment comparison — uses the newest generated inventory+affinity pair.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--batches-dir', default=_DEFAULT_BATCHES_DIR,
                        help='Root directory produced by generate_profile_suite.py')
    parser.add_argument('--all-batches', action='store_true',
                        help='Run every batch/profile pair instead of only the newest batch')
    parser.add_argument('--resume', metavar='BASE_DIR', default=None,
                        help='Resume a previous run by passing its base directory')
    args = parser.parse_args()

    if args.resume:
        base_dir = args.resume if os.path.isabs(args.resume) else os.path.join(_HERE, args.resume)
        if not os.path.isdir(base_dir):
            sys.exit(f'Resume directory not found: {base_dir}')
    else:
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_dir = os.path.join(_HERE, f'comparison_{ts}')
        os.makedirs(base_dir, exist_ok=True)

    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    log.info(f'Output directory : {base_dir}')
    log.info(f'Batches dir      : {args.batches_dir}')
    log.info(f'Mode             : {"all batches" if args.all_batches else "latest batch only"}')

    if args.all_batches:
        pairs = discover_db_pairs(args.batches_dir)
    else:
        pairs = find_latest_db_pairs(args.batches_dir)

    if not pairs:
        sys.exit(f'No inventory+affinity DB pairs found in: {args.batches_dir}')

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
        for cfg in REGRESSION_CONFIGS:
            run_config(cfg, shared, pair_dir, log)

    log.info(f'\nAll {len(pairs)} dataset(s) × {len(REGRESSION_CONFIGS)} config(s) complete.'
             f'  Root: {base_dir}')


if __name__ == '__main__':
    main()
