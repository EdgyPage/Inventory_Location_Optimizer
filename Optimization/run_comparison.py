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
import logging.handlers
import math
import multiprocessing
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


def _save_worker_checkpoint(run_dir: str, strategy: str, next_batch_id: int) -> None:
    """Written by each worker process at every checkpoint interval."""
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    with open(path, 'wb') as f:
        pickle.dump({'next_batch_id': next_batch_id}, f)


def _load_worker_checkpoint(run_dir: str, strategy: str) -> int:
    """Returns the next batch ID to run, or 0 if no checkpoint exists."""
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    if not os.path.exists(path):
        return 0
    with open(path, 'rb') as f:
        return pickle.load(f).get('next_batch_id', 0)


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
        aisle_size_map     = {a.aisle_id: a.storage_size  for a in warehouse_meta.aisles},
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


# ── per-strategy worker (runs in its own process) ─────────────────────────────

def _run_strategy_worker(args: dict) -> dict:
    """Simulate one assignment strategy end-to-end in its own process.

    Log records are sent through a multiprocessing.Queue to a QueueListener
    in the main process, so every line appears in the shared log file in
    real time rather than only after the worker finishes.
    """
    # ── logging setup (must be first — spawned process starts with no handlers) ─
    log_queue = args['log_queue']
    root      = logging.getLogger()
    root.handlers = []
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.INFO)
    strategy = args['strategy']
    log      = logging.getLogger(f'worker-{strategy}')

    # ── unpack args ───────────────────────────────────────────────────────────
    inv_db        = args['inv_db']
    aff_db        = args['aff_db']
    db_path       = args['db_path']
    run_dir       = args['run_dir']
    run_id        = args['run_id']
    start_i       = args['start_i']
    n_batches     = args['n_batches']
    k_pickers     = args['k_pickers']
    seed_world    = args['seed_world']
    seed_batches  = args['seed_batches']
    checkpoint    = args['checkpoint']
    target_fill   = args['target_fill']
    warehouse_cfg = args['warehouse_cfg']
    pick_cfg      = args['pick_cfg']
    wp            = args['wp']
    load_params   = args['load_params']
    batch_cfg     = args['batch_cfg']

    log.info('=' * 56)
    log.info(f'Strategy {strategy} starting  run_id={run_id}  '
             f'batches {start_i}→{n_batches}')
    log.info(f'  pick  w={pick_cfg.pick_weight_coef}  '
             f'v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  '
             f'cart={pick_cfg.cart_swap_coef}  '
             f'x={pick_cfg.x_move_time}  y={pick_cfg.y_move_time}')
    log.info(f'  load  λ={load_params.lambda_}  '
             f'k={load_params.k}  γ={load_params.gamma}')
    log.info(f'  seeds  world={seed_world}  batches={seed_batches}')
    log.info(f'  target fill={target_fill:.0%}  checkpoint every {checkpoint} batches')

    # ── load inventory ────────────────────────────────────────────────────────
    log.info(f'Loading inventory: {inv_db}')
    t0        = time.perf_counter()
    inventory = load_inventory_from_db(inv_db)
    n_skus    = len(inventory.cartons)
    log.info(f'  {n_skus:,} SKUs loaded  ({time.perf_counter()-t0:.2f}s)')

    # ── load affinity ─────────────────────────────────────────────────────────
    log.info(f'Loading affinity: {aff_db}')
    t0       = time.perf_counter()
    affinity = AffinityStore(aff_db)
    n_aff    = affinity._matrix.nnz if affinity._matrix is not None else 0
    mb       = 0.0 if affinity._matrix is None else (
        affinity._matrix.data.nbytes +
        affinity._matrix.indices.nbytes +
        affinity._matrix.indptr.nbytes) / 1_048_576
    log.info(f'  {n_aff:,} affinity entries  {mb:.0f} MB  '
             f'({time.perf_counter()-t0:.1f}s)')

    # ── build warehouse ───────────────────────────────────────────────────────
    total_bins = warehouse_cfg.total_aisles * 500  # bayX=25 × bayY=20
    log.info(f'Building warehouse: {warehouse_cfg.total_aisles} aisles / '
             f'{total_bins:,} bins...')
    t0 = time.perf_counter()
    Aisle.next_aisle_id = 1
    random.seed(seed_world)
    warehouse = Warehouse_Builder().from_config(warehouse_cfg).build()
    log.info(f'  Warehouse built  ({time.perf_counter()-t0:.1f}s)')

    # ── initial stock (uniform, all strategies identical) ─────────────────────
    log.info(f'Stocking {n_skus:,} SKUs (1 bin each, qty=stock_qty)...')
    t0 = time.perf_counter()
    random.seed(seed_world + 100)
    mgr = Inventory_Manager(warehouse,
                            affinity=(affinity if strategy != 'A' else None))
    mgr.enqueue_all(inventory.cartons, quantity=1)
    base_fill = len(mgr.unavailable)
    log.info(f'  Base stock: {base_fill:,} / {len(warehouse.bins):,} bins  '
             f'({base_fill/len(warehouse.bins):.1%})  ({time.perf_counter()-t0:.1f}s)')

    # ── overstock to target fill ──────────────────────────────────────────────
    log.info(f'Overstocking to {target_fill:.0%} fill...')
    t0 = time.perf_counter()
    _stock_to_target_fill(mgr, inventory, target=target_fill)
    filled = len(mgr.unavailable)
    log.info(f'  After overstock: {filled:,} / {len(warehouse.bins):,} bins  '
             f'({filled/len(warehouse.bins):.1%})  ({time.perf_counter()-t0:.1f}s)')

    # ── load-aware setup (B / C only) ─────────────────────────────────────────
    if strategy in ('B', 'C'):
        log.info(f'Building lift state...')
        t0 = time.perf_counter()
        mgr.init_lift_state(affinity)
        n_aisles_with_lift = sum(1 for v in mgr._aisle_lift_sum.values() if v > 0)
        log.info(f'  Lift state: {len(mgr._aisle_lift_sum)} aisles  '
                 f'{n_aisles_with_lift} with lift>0  ({time.perf_counter()-t0:.1f}s)')

    if strategy == 'B':
        mgr.assignment_fn = build_load_minimizing_assignment_fn(
            load_params, affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_lift_sum, mgr._aisle_idx_sets)
        log.info('  assignment_fn = load_minimizing')
    elif strategy == 'C':
        mgr.assignment_fn = build_load_maximizing_assignment_fn(
            load_params, affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_lift_sum, mgr._aisle_idx_sets)
        log.info('  assignment_fn = load_maximizing')
    else:
        log.info('  assignment_fn = uniform_random')

    # ── RNG fast-forward for resume ───────────────────────────────────────────
    random.seed(seed_batches)
    if start_i > 0:
        log.info(f'Fast-forwarding RNG through {start_i} batches...')
        t0 = time.perf_counter()
        for _ in range(start_i):
            Batch(batch_cfg, inventory, affinity=affinity)
        log.info(f'  RNG advanced to batch {start_i}  ({time.perf_counter()-t0:.1f}s)')

    # ── simulation loop ───────────────────────────────────────────────────────
    log.info(f'Simulation starting at batch {start_i}...')
    pb: list = []
    pt: list = []
    skipped        = 0
    reorders_ckpt  = 0   # reorder triggers since last checkpoint log
    dur_sum_ckpt   = 0.0
    dur_count_ckpt = 0
    last_dur       = 0.0
    t_loop         = time.perf_counter()
    t_ckpt         = time.perf_counter()

    for i in range(start_i, n_batches):
        triggered = mgr.check_reorders()
        reorders_ckpt += len(triggered)

        batch = Batch(batch_cfg, inventory, affinity=affinity)
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if not tasks:
            skipped += 1
            continue

        events   = PickSimulation(tasks, pick_cfg, manager=mgr).run()
        bs       = extract_batch_stats(events, batch_id=i,
                                       k_pickers=k_pickers, run_id=run_id)
        ts       = extract_task_stats(events, tasks, batch_id=i,
                                      affinity=affinity, wp=wp, run_id=run_id)
        pb.append(bs)
        pt.extend(ts)
        last_dur       = bs.duration
        dur_sum_ckpt  += bs.duration
        dur_count_ckpt += 1

        if len(pb) >= checkpoint:
            t_save0 = time.perf_counter()
            save_batch_stats(db_path, run_id, pb)
            save_task_stats(db_path, run_id, pt)
            _save_worker_checkpoint(run_dir, strategy, i + 1)
            t_save = time.perf_counter() - t_save0

            wall      = time.perf_counter() - t_loop
            ckpt_wall = time.perf_counter() - t_ckpt
            cum_rate  = (i + 1 - start_i) / wall          # batches/s since start
            ckpt_rate = dur_count_ckpt / ckpt_wall        # batches/s this window
            avg_dur   = dur_sum_ckpt / dur_count_ckpt if dur_count_ckpt else 0.0
            cur_fill  = len(mgr.unavailable) / len(warehouse.bins)
            q_depth   = mgr.queue_depth

            log.info(
                f'  Batch {i+1:4d}/{n_batches}'
                f'  dur={bs.duration:6.0f}'
                f'  avg={avg_dur:6.0f}'
                f'  rate={ckpt_rate:.2f}/s ({cum_rate:.2f} cum)'
                f'  fill={cur_fill:.1%}'
                f'  q={q_depth}'
                f'  reorders={reorders_ckpt}'
                f'  wall={wall:.0f}s'
                f'  db_save={t_save:.2f}s'
            )

            pb.clear(); pt.clear()
            reorders_ckpt  = 0
            dur_sum_ckpt   = 0.0
            dur_count_ckpt = 0
            t_ckpt         = time.perf_counter()

    if pb:
        log.info(f'  Flushing final {len(pb)} batches to DB...')
        save_batch_stats(db_path, run_id, pb)
        save_task_stats(db_path, run_id, pt)

    elapsed = time.perf_counter() - t_loop
    done    = n_batches - start_i - skipped
    log.info('=' * 56)
    log.info(f'Strategy {strategy} DONE')
    log.info(f'  batches={done}  skipped={skipped}  wall={elapsed:.1f}s  '
             f'rate={done/elapsed:.2f}/s  last_dur={last_dur:.0f}')
    log.info('=' * 56)

    return {
        'strategy' : strategy,
        'run_id'   : run_id,
        'elapsed'  : elapsed,
        'done'     : done,
        'skipped'  : skipped,
        'last_dur' : last_dur,
    }


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
        # pick model (regression config)
        'name'            : name,
        'pick_weight_coef': pick_cfg.pick_weight_coef,
        'pick_volume_coef': pick_cfg.pick_volume_coef,
        'pick_intercept'  : pick_cfg.pick_intercept,
        'cart_swap_coef'  : pick_cfg.cart_swap_coef,
        'x_move_time'     : pick_cfg.x_move_time,
        'y_move_time'     : pick_cfg.y_move_time,
        'num_pickers'     : pick_cfg.num_pickers,
        # load model (λ / k / γ)
        'load_lambda'     : load_params.lambda_,
        'load_k'          : load_params.k,
        'load_gamma'      : load_params.gamma,
        # warehouse / simulation
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

    # ── DB init / resume ──────────────────────────────────────────────────────
    resume = _load_resume(run_dir)
    if resume:
        run_ids = resume['run_ids']
        run_a, run_b, run_c = run_ids['A'], run_ids['B'], run_ids['C']
        starts  = resume.get('next_batch', {})
        start_A = _load_worker_checkpoint(run_dir, 'A') or starts.get('A', 0)
        start_B = _load_worker_checkpoint(run_dir, 'B') or starts.get('B', 0)
        start_C = _load_worker_checkpoint(run_dir, 'C') or starts.get('C', 0)
        log.info(f'  Resuming  A@{start_A}  B@{start_B}  C@{start_C}'
                 f'  (run_ids {run_a}/{run_b}/{run_c})')
    else:
        init_run_db(db_path)
        run_a   = create_run(db_path, 'uniform_assignment')
        run_b   = create_run(db_path, 'load_minimizing_assignment')
        run_c   = create_run(db_path, 'load_maximizing_assignment')
        run_ids = {'A': run_a, 'B': run_b, 'C': run_c}
        start_A = start_B = start_C = 0
        log.info(f'  New run  run_ids A={run_a} B={run_b} C={run_c}')

    _save_resume(run_dir, run_ids, start_A, start_B, start_C)

    # ── log queue: workers send records here; listener forwards to handlers ────
    log_queue = multiprocessing.Queue(-1)
    listener  = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True
    )
    listener.start()
    log.info('  Log queue listener started — worker output will appear in real time')

    # ── shared args passed to every worker ────────────────────────────────────
    _shared = dict(
        inv_db        = shared['inv_db'],
        aff_db        = shared['aff_db'],
        db_path       = db_path,
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
        log_queue     = log_queue,
    )
    strategy_args = [
        {**_shared, 'strategy': 'A', 'run_id': run_a, 'start_i': start_A},
        {**_shared, 'strategy': 'B', 'run_id': run_b, 'start_i': start_B},
        {**_shared, 'strategy': 'C', 'run_id': run_c, 'start_i': start_C},
    ]

    # ── dispatch workers ──────────────────────────────────────────────────────
    log.info(f'  Launching 3 parallel workers  (config={name})')
    t_wall  = time.perf_counter()
    results = {}

    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_run_strategy_worker, a): a['strategy']
                       for a in strategy_args}
            for future in concurrent.futures.as_completed(futures):
                s   = futures[future]
                res = future.result()   # re-raises any worker exception
                results[s] = res
                log.info(
                    f'  Worker {s} returned  done={res["done"]}  '
                    f'skipped={res["skipped"]}  '
                    f'wall={res["elapsed"]:.1f}s  '
                    f'last_dur={res["last_dur"]:.0f}'
                )
    finally:
        listener.stop()
        log_queue.close()
        log.info('  Log queue listener stopped')

    elapsed = time.perf_counter() - t_wall
    log.info(f'  All workers done  wall={elapsed:.1f}s')

    # clean up per-strategy checkpoint files
    for s in ('A', 'B', 'C'):
        p = os.path.join(run_dir, f'_ckpt_{s}.pkl')
        if os.path.exists(p):
            os.remove(p)
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
        vA = np.asarray(dp_A[dp_A['pallet_size']==sz]['duration'], dtype=float)
        vB = np.asarray(dp_B[dp_B['pallet_size']==sz]['duration'], dtype=float)
        vC = np.asarray(dp_C[dp_C['pallet_size']==sz]['duration'], dtype=float)
        all_v = [v for v in (vA, vB, vC) if len(v) > 1]
        if not all_v:
            continue
        combined = np.concatenate(all_v)
        lo, hi = float(combined.min()), float(combined.max())
        if lo >= hi:
            continue
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
