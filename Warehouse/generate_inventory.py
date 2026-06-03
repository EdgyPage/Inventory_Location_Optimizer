"""
Generate an Inventory with configurable dimension and weight distributions and
persist it to SQLite.

Each carton's length, width, and height are sampled from a dim_spec; weight is
sampled from a weight_spec.  Both specs support plain distributions and mixture
models, making it easy to produce bimodal, trimodal, or heavy-tailed inventories.

Supported dim distributions
---------------------------
  triangular  low, high, mode (default mode=high → left-skewed toward large)
  uniform     low, high
  normal      mean, std  (clipped to [3, 48])
  beta        alpha, beta, low, high  (beta variate scaled to [low, high])
  mixture     components: [{prob, spec}, ...]  (recursive, picks one component per draw)

Supported weight distributions
-------------------------------
  volume_poisson         Poisson(lam = volume^(1/3))  — default, size-correlated
  volume_scaled_poisson  Poisson(lam = scale * volume^(1/3))  — adjustable scale
  poisson_fixed          Poisson(lam)  — fixed mean, size-independent
  uniform                low, high
  normal                 mean, std  (clipped to [1, ∞))
  mixture                components: [{prob, spec}, ...]

Output layout
-------------
<out_dir>/<name>/
    inventory.db    — SQLite: cartons + run_metadata
    params.json     — full parameter record (dim_spec, weight_spec, seed, …)
    stats.json      — per-group counts, dimension / weight / demand summaries
    plots/
        group_sizes.png
        dimensions.png
        weight.png
        demand.png
        volume_vs_weight.png
        singleton_split.png

Usage (CLI)
-----------
python generate_inventory.py [--name NAME] [--num-skus 76500] [--seed 42]
    [--dim-spec '{"dist":"triangular","low":3,"high":48,"mode":48}']
    [--weight-spec '{"dist":"volume_poisson"}']
python generate_inventory.py --help

Callable API (used by generate_profile_suite.py)
-----------------------------------------
from generate_inventory import generate_run, load_inventory_from_db
run_dir  = generate_run(name='default', num_skus=76500, seed=42, out_dir='...')
inventory = load_inventory_from_db(run_dir + '/inventory.db')
"""

import matplotlib
matplotlib.use('Agg')

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime
from matplotlib.patches import Patch

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from Carton import Carton, StorageHandleConfig
from Demand import Demand
from Inventory_Builder import Inventory
from Storage_Primitive import Storage_Type

_DEFAULT_OUT_DIR  = os.path.join(_HERE, 'generated', 'inventories')
_HANDLINGS        = ['conveyable', 'non-conveyable']
_CATEGORIES       = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_CARTON_MAX_DIM   = 48
_SINGLETON_MAX_DIM = 16

DEFAULT_DIM_SPEC      = {'dist': 'triangular', 'low': 3, 'high': 48, 'mode': 48}
DEFAULT_WEIGHT_SPEC   = {'dist': 'volume_poisson'}
# Beta(27/13, 90/13) scaled to [5, 200]: mode=35, mean=50, right-skewed bell.
# Derivation: solving mode = low + (α-1)/(α+β-2)*(high-low) = 35
#             and    mean = low +  α/(α+β)       *(high-low) = 50
#             gives α+β = 9, α = 27/13.
DEFAULT_QUANTITY_SPEC = {'dist': 'beta_scaled', 'alpha': 27/13, 'beta': 90/13,
                         'low': 5, 'high': 200}

# Equilibrium inventory model
# ─────────────────────────────────────────────────────────────────────────────
# equilibrium_qty  = coverage_batches × expected_batch_demand
#   → how many batches of demand to hold at steady state; controls warehouse size.
# reorder_point    = trigger_fraction × equilibrium_qty
#   → fires when inventory drops to this fraction; OUP refills back to equilibrium.
# lead_time_mean   = mean batches before a placed order arrives in the warehouse.
#   → 0.0 = immediate (no disruption); >0 = delayed orders sampled per-SKU.
EQUILIBRIUM_COVERAGE_BATCHES: float = 10.0
REORDER_TRIGGER_FRACTION:     float = 0.50
LEAD_TIME_MEAN_BATCHES:       float = 0.0    # profile default; override per run
LEAD_TIME_CV:                 float = 0.50   # coefficient of variation for per-SKU sampling
# supply_cv: coefficient of variation for received reorder quantity.
# 0.0 = perfect fulfillment (always receive exactly what was ordered).
# 0.1 = ±10% typical variance ("ordered 50, got 45").
# Per-SKU values are drawn from HalfNormal(supply_cv_mean) at profile time,
# giving heterogeneous supplier reliability across SKUs.
SUPPLY_CV_MEAN: float = 0.0

# Legacy constant kept for backward-compatible DB loads.
REORDER_COVERAGE_BATCHES: float = EQUILIBRIUM_COVERAGE_BATCHES


# ── distribution samplers ──────────────────────────────────────────────────────

def _poisson_rng(lam: float, rng: random.Random) -> int:
    """Knuth's algorithm with normal approximation for large λ."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while p > threshold:
        k += 1
        p *= rng.random()
    return k - 1


def sample_quantity(spec: dict, rng: random.Random) -> int:
    """Sample initial stock quantity per bin from *spec*.

    Supported distributions
    -----------------------
    beta_scaled   alpha, beta, low, high — beta variate scaled to [low, high]
    uniform       low, high
    fixed         value
    """
    dist = spec.get('dist', 'beta_scaled')
    if dist == 'beta_scaled':
        alpha = spec.get('alpha', 27 / 13)
        beta  = spec.get('beta',  90 / 13)
        low   = int(spec.get('low',  5))
        high  = int(spec.get('high', 200))
        v = rng.betavariate(alpha, beta)
        return max(low, min(high, round(low + v * (high - low))))
    elif dist == 'uniform':
        return rng.randint(int(spec['low']), int(spec['high']))
    elif dist == 'fixed':
        return int(spec['value'])
    else:
        raise ValueError(f'Unknown quantity distribution: {dist!r}')


def sample_dim(spec: dict, rng: random.Random, max_cap: int = _CARTON_MAX_DIM) -> int:
    """Sample one carton dimension from *spec*, clipped to [3, max_cap].

    max_cap is passed down through mixture components so singleton cartons
    (max_cap=16) are respected even inside nested mixture specs.
    """
    lo   = 3
    dist = spec.get('dist', 'triangular')

    if dist == 'triangular':
        low  = max(lo, spec.get('low', 3))
        high = min(max_cap, spec.get('high', _CARTON_MAX_DIM))
        mode = min(high, max(low, spec.get('mode', high)))
        return max(lo, min(max_cap, round(rng.triangular(low, high, mode))))

    elif dist == 'uniform':
        low  = max(lo, spec.get('low', 3))
        high = min(max_cap, spec.get('high', _CARTON_MAX_DIM))
        return rng.randint(low, high)

    elif dist == 'normal':
        mean = spec.get('mean', 25.0)
        std  = spec.get('std', 10.0)
        return max(lo, min(max_cap, round(rng.gauss(mean, std))))

    elif dist == 'beta':
        alpha = spec.get('alpha', 2.0)
        beta  = spec.get('beta', 2.0)
        low   = max(lo, spec.get('low', 3))
        high  = min(max_cap, spec.get('high', _CARTON_MAX_DIM))
        v = rng.betavariate(alpha, beta)
        return max(lo, min(max_cap, round(low + v * (high - low))))

    elif dist == 'mixture':
        components = spec['components']
        probs      = [c['prob'] for c in components]
        chosen     = rng.choices(components, weights=probs, k=1)[0]
        return sample_dim(chosen['spec'], rng, max_cap)

    else:
        raise ValueError(f'Unknown dim distribution: {dist!r}')


def sample_weight(spec: dict, length: int, width: int, height: int,
                  rng: random.Random) -> int:
    """Sample carton weight from *spec*."""
    dist = spec.get('dist', 'volume_poisson')

    if dist == 'volume_poisson':
        lam = (length * width * height) ** (1 / 3)
        return max(1, _poisson_rng(lam, rng))

    elif dist == 'volume_scaled_poisson':
        scale = spec.get('scale', 1.0)
        lam   = scale * (length * width * height) ** (1 / 3)
        return max(1, _poisson_rng(lam, rng))

    elif dist == 'poisson_fixed':
        lam = float(spec.get('lam', 10.0))
        return max(1, _poisson_rng(lam, rng))

    elif dist == 'uniform':
        low  = int(spec.get('low', 1))
        high = int(spec.get('high', 50))
        return rng.randint(low, high)

    elif dist == 'normal':
        mean = float(spec.get('mean', 20.0))
        std  = float(spec.get('std', 10.0))
        return max(1, round(rng.gauss(mean, std)))

    elif dist == 'mixture':
        components = spec['components']
        probs      = [c['prob'] for c in components]
        chosen     = rng.choices(components, weights=probs, k=1)[0]
        return sample_weight(chosen['spec'], length, width, height, rng)

    else:
        raise ValueError(f'Unknown weight distribution: {dist!r}')


# ── inventory builder ──────────────────────────────────────────────────────────

def build_inventory_with_profile(
    num_skus                     : int,
    handling_splits              : list[float],
    category_splits              : list[float],
    singleton_fraction           : float,
    dim_spec                     : dict,
    weight_spec                  : dict,
    seed                         : int,
    equilibrium_coverage_batches : float = EQUILIBRIUM_COVERAGE_BATCHES,
    trigger_fraction             : float = REORDER_TRIGGER_FRACTION,
    lead_time_mean_batches       : float = LEAD_TIME_MEAN_BATCHES,
    lead_time_cv                 : float = LEAD_TIME_CV,
    supply_cv_mean               : float = SUPPLY_CV_MEAN,
) -> Inventory:
    """Build an Inventory using the equilibrium inventory model.

    Bypasses Carton.__init__ so any distribution can be used.
    Carton.next_sku is reset to 1 at the start of this function.

    Per-SKU attributes (all stored in DB):
      expected_batch_demand = freq × qty_rate
      equilibrium_qty       = max(1, round(coverage_batches × expected_batch_demand))
                              Target steady-state inventory; warehouse sized for this.
      reorder_point         = max(1, round(trigger_fraction × equilibrium_qty))
                              Reorder fires when total inventory falls to this level.
      lead_time_mean        = mean batches before a placed order arrives.
                              0 = immediate.  Sampled per-SKU from N(mean, mean*cv)
                              when lead_time_mean_batches > 0, else fixed at 0.

      supply_cv             = coefficient of variation for received quantity.
                              0.0 = perfect fulfillment; 0.1 → "ordered 50, got 45".
                              Per-SKU value drawn from HalfNormal(supply_cv_mean).

    At runtime: ideal = equilibrium_qty - current_qty; received = max(1, round(N(ideal, ideal*supply_cv))).
    """
    storage = Storage_Type()
    rng     = random.Random(seed)
    Carton.next_sku = 1
    cartons = []

    for _ in range(num_skus):
        handling     = rng.choices(storage.handling_storage_types, weights=handling_splits, k=1)[0]
        category     = rng.choices(storage.category_storage_types, weights=category_splits, k=1)[0]
        is_singleton = rng.random() < singleton_fraction
        cap          = _SINGLETON_MAX_DIM if is_singleton else _CARTON_MAX_DIM

        l  = sample_dim(dim_spec, rng, cap)
        w  = sample_dim(dim_spec, rng, cap)
        h  = sample_dim(dim_spec, rng, cap)
        wt = sample_weight(weight_spec, l, w, h, rng)

        freq     = rng.uniform(0.0, 1.0)
        qty_rate = rng.uniform(0.5, 20.0)
        expected = freq * qty_rate

        c              = object.__new__(Carton)
        c._sku         = Carton.next_sku
        Carton.next_sku += 1
        c.storage_type          = (handling, category)
        c.storage_handle_config = StorageHandleConfig(handling, category)
        c.lift_group            = (handling, category)
        c.length       = l
        c.width        = w
        c.height       = h
        c.weight       = wt
        c.demand       = Demand.from_rates(freq, qty_rate)

        c.expected_batch_demand = expected
        c.equilibrium_qty       = max(1, round(equilibrium_coverage_batches * expected))
        c.reorder_point         = max(1, round(trigger_fraction * c.equilibrium_qty))

        if lead_time_mean_batches > 0.0:
            # Per-SKU lead time heterogeneity: some SKUs have faster/slower suppliers
            lt = rng.gauss(lead_time_mean_batches, lead_time_mean_batches * lead_time_cv)
            c.lead_time_mean = max(0.0, lt)
        else:
            c.lead_time_mean = 0.0

        # Per-SKU supply reliability: abs(N(0, supply_cv_mean)) keeps values ≥ 0.
        # SKUs with higher supply_cv have less predictable fulfillment quantities.
        if supply_cv_mean > 0.0:
            c.supply_cv = abs(rng.gauss(0.0, supply_cv_mean))
        else:
            c.supply_cv = 0.0

        cartons.append(c)

    return Inventory(cartons)


# ── DB schema ──────────────────────────────────────────────────────────────────

_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS cartons (
        sku                   INTEGER PRIMARY KEY,
        handling              TEXT    NOT NULL,
        category              TEXT    NOT NULL,
        length                INTEGER NOT NULL,
        width                 INTEGER NOT NULL,
        height                INTEGER NOT NULL,
        weight                INTEGER NOT NULL,
        demand_frequency      REAL    NOT NULL,
        demand_qty_rate       REAL    NOT NULL,
        expected_batch_demand REAL    NOT NULL DEFAULT 0,
        equilibrium_qty       INTEGER NOT NULL DEFAULT 1,
        reorder_point         INTEGER NOT NULL DEFAULT 1,
        lead_time_mean        REAL    NOT NULL DEFAULT 0.0,
        supply_cv             REAL    NOT NULL DEFAULT 0.0
    );
    CREATE TABLE IF NOT EXISTS run_metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
'''


def _init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── save / load ────────────────────────────────────────────────────────────────

def save_inventory_to_db(inventory: Inventory, db_path: str, params: dict) -> None:
    conn = _init_db(db_path)
    rows = []
    for c in inventory.cartons:
        rows.append((
            c.sku, c.storage_type[0], c.storage_type[1],
            c.length, c.width, c.height, c.weight,
            c.demand.frequency, c.demand.quantity_rate,
            getattr(c, 'expected_batch_demand', 0.0),
            getattr(c, 'equilibrium_qty',       1),
            getattr(c, 'reorder_point',         1),
            getattr(c, 'lead_time_mean',        0.0),
            getattr(c, 'supply_cv',             0.0),
        ))
    conn.executemany(
        'INSERT OR REPLACE INTO cartons '
        '(sku, handling, category, length, width, height, weight, '
        ' demand_frequency, demand_qty_rate, expected_batch_demand, '
        ' equilibrium_qty, reorder_point, lead_time_mean, supply_cv) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        rows,
    )
    conn.execute('INSERT OR REPLACE INTO run_metadata VALUES (?,?)',
                 ('params_json', json.dumps(params, indent=2)))
    conn.commit()
    conn.close()


def load_inventory_from_db(db_path: str) -> Inventory:
    """Reconstruct an Inventory object from a previously saved DB.

    Column compatibility:
      - stock_qty, expected_batch_demand, reorder_point: added progressively;
        missing columns fall back to computed defaults.
      - is_singleton: was present in older schemas but is never loaded onto
        Carton objects (it is always derivable from dimensions).  Old DBs that
        still carry the column load correctly because SELECT uses explicit names.
    """
    conn = sqlite3.connect(db_path)
    col_names = [r[1] for r in conn.execute('PRAGMA table_info(cartons)').fetchall()]
    has_stock_qty      = 'stock_qty'             in col_names
    has_expected       = 'expected_batch_demand'  in col_names
    has_reorder_point  = 'reorder_point'          in col_names
    has_equilibrium    = 'equilibrium_qty'         in col_names
    has_lead_time      = 'lead_time_mean'          in col_names
    has_supply_cv      = 'supply_cv'               in col_names
    select = (
        'SELECT sku, handling, category, length, width, height, weight, '
        'demand_frequency, demand_qty_rate'
        + (', stock_qty'             if has_stock_qty     else '')
        + (', expected_batch_demand' if has_expected      else '')
        + (', reorder_point'         if has_reorder_point else '')
        + (', equilibrium_qty'       if has_equilibrium   else '')
        + (', lead_time_mean'        if has_lead_time     else '')
        + (', supply_cv'             if has_supply_cv     else '')
        + ' FROM cartons ORDER BY sku'
    )
    rows = conn.execute(select).fetchall()
    conn.close()

    cartons = []
    max_sku = 0
    for row in rows:
        sku, handling, category, length, width, height, weight, freq, qty_rate = row[:9]
        col = 9

        stock_qty = int(row[col]) if has_stock_qty else 1
        if has_stock_qty: col += 1

        expected_batch_demand = float(row[col]) if has_expected else freq * qty_rate
        if has_expected: col += 1

        reorder_point = int(row[col]) if has_reorder_point else None
        if has_reorder_point: col += 1

        # equilibrium_qty: prefer the new column; fall back to legacy stock_qty
        if has_equilibrium:
            equilibrium_qty = int(row[col]);  col += 1
        else:
            equilibrium_qty = stock_qty  # legacy DB: treat stock_qty as equilibrium

        if reorder_point is None:
            reorder_point = max(1, round(REORDER_TRIGGER_FRACTION * equilibrium_qty))

        lead_time_mean = float(row[col]) if has_lead_time else 0.0
        if has_lead_time: col += 1

        supply_cv = float(row[col]) if has_supply_cv else 0.0
        if has_supply_cv: col += 1

        c                        = object.__new__(Carton)
        c._sku                   = sku
        c.storage_type           = (handling, category)
        c.storage_handle_config  = StorageHandleConfig(handling, category)
        c.lift_group             = (handling, category)
        c.length                 = length
        c.width                  = width
        c.height                 = height
        c.weight                 = weight
        c.demand                 = Demand.from_rates(freq, qty_rate)
        c.expected_batch_demand  = expected_batch_demand
        c.equilibrium_qty        = equilibrium_qty
        c.reorder_point          = reorder_point
        c.lead_time_mean         = lead_time_mean
        c.supply_cv              = supply_cv
        cartons.append(c)
        max_sku = max(max_sku, sku)

    Carton.next_sku = max_sku + 1
    return Inventory(cartons)


# ── statistics ─────────────────────────────────────────────────────────────────

def compute_stats(df: pd.DataFrame) -> dict:
    def _summary(s: pd.Series) -> dict:
        return {
            'min': float(s.min()), 'max': float(s.max()),
            'mean': float(s.mean()), 'median': float(s.median()), 'std': float(s.std()),
            'p25': float(s.quantile(0.25)), 'p75': float(s.quantile(0.75)),
            'p95': float(s.quantile(0.95)),
        }

    df = df.copy()
    df['volume'] = df['length'] * df['width'] * df['height']

    groups = {}
    for (h, c), sub in df.groupby(['handling', 'category']):
        groups[f'{h}/{c}'] = {'count': int(len(sub)), 'fraction': float(len(sub) / len(df))}

    return {
        'total_skus'        : int(len(df)),
        'groups'            : groups,
        'handling_counts'   : df['handling'].value_counts().to_dict(),
        'category_counts'   : df['category'].value_counts().to_dict(),
        'singleton_count'   : int(df['is_singleton'].sum()),
        'singleton_fraction': float(df['is_singleton'].mean()),
        'dimensions'        : {
            'length': _summary(df['length']),
            'width' : _summary(df['width']),
            'height': _summary(df['height']),
            'volume': _summary(df['volume']),
        },
        'weight': _summary(df['weight']),
        'demand': {
            'frequency'    : _summary(df['demand_frequency']),
            'quantity_rate': _summary(df['demand_qty_rate']),
        },
        'stock_qty': _summary(df['stock_qty']) if 'stock_qty' in df.columns else {},
    }


# ── plots ──────────────────────────────────────────────────────────────────────

def _save_close(fig, path: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_group_sizes(df: pd.DataFrame, out_dir: str, title_suffix: str = '') -> None:
    groups = df.groupby(['handling', 'category']).size().reset_index(name='count')
    labels = [f"{r['handling'][:4]}\n{r['category']}" for _, r in groups.iterrows()]
    colors = ['#5b9bd5' if h == 'conveyable' else '#f4a030' for h in groups['handling']]

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.bar(range(len(groups)), groups['count'], color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel('SKU count')
    ax.set_title(f'SKU count by (handling × category) group{title_suffix}', fontweight='bold')
    ax.grid(axis='y', alpha=0.3)
    ax.legend(handles=[Patch(color='#5b9bd5', label='conveyable'),
                       Patch(color='#f4a030', label='non-conveyable')], fontsize=9)
    _save_close(fig, os.path.join(out_dir, 'group_sizes.png'))


def plot_dimensions(df: pd.DataFrame, out_dir: str, title_suffix: str = '') -> None:
    df = df.copy()
    df['volume'] = df['length'] * df['width'] * df['height']

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle(f'Carton Dimension Distributions{title_suffix}', fontsize=13, fontweight='bold')
    for ax, col, title in [
        (axes[0, 0], 'length', 'Length'),
        (axes[0, 1], 'width',  'Width'),
        (axes[1, 0], 'height', 'Height'),
        (axes[1, 1], 'volume', 'Volume  (L × W × H)'),
    ]:
        vals = df[col].values
        ax.hist(vals, bins=60, color='#5b9bd5', alpha=0.75, edgecolor='white')
        ax.axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {vals.mean():.1f}')
        ax.axvline(np.median(vals), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(vals):.1f}')
        ax.set_title(title, fontsize=10)
        ax.set_ylabel('Count')
        ax.legend(fontsize=8)
        ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'dimensions.png'))


def plot_weight(df: pd.DataFrame, out_dir: str, title_suffix: str = '') -> None:
    vals = df['weight'].values
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    fig.suptitle(f'Carton Weight Distribution{title_suffix}', fontsize=13, fontweight='bold')

    axes[0].hist(vals, bins=80, color='#70ad47', alpha=0.75, edgecolor='white')
    axes[0].axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {vals.mean():.1f}')
    axes[0].axvline(np.median(vals), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(vals):.1f}')
    axes[0].set_xlabel('Weight');  axes[0].set_ylabel('Count')
    axes[0].set_title('Histogram');  axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    if len(vals) > 1 and vals.max() > vals.min():
        kde = gaussian_kde(vals, bw_method='silverman')
        xs  = np.linspace(vals.min(), vals.max(), 500)
        axes[1].fill_between(xs, kde(xs), alpha=0.4, color='#70ad47')
        axes[1].plot(xs, kde(xs), color='#70ad47', lw=2)
        axes[1].axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--')
        axes[1].axvline(np.median(vals), color='orange', lw=1.5, linestyle=':')
    axes[1].set_xlabel('Weight');  axes[1].set_ylabel('Density')
    axes[1].set_title('KDE');  axes[1].grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'weight.png'))


def plot_demand(df: pd.DataFrame, out_dir: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    fig.suptitle('Demand Distributions', fontsize=13, fontweight='bold')
    for ax, col, title, color in [
        (axes[0], 'demand_frequency', 'Pick Frequency', '#5b9bd5'),
        (axes[1], 'demand_qty_rate',  'Quantity Rate',  '#f4a030'),
    ]:
        vals = df[col].values
        ax.hist(vals, bins=50, color=color, alpha=0.75, edgecolor='white')
        ax.axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {vals.mean():.3f}')
        ax.axvline(np.median(vals), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(vals):.3f}')
        ax.set_xlabel(title);  ax.set_ylabel('Count')
        ax.set_title(title);  ax.legend(fontsize=9);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'demand.png'))


def plot_stock_qty(df: pd.DataFrame, out_dir: str) -> None:
    """Histogram + KDE of the stock_qty distribution."""
    if 'stock_qty' not in df.columns:
        return
    vals = df['stock_qty'].values.astype(float)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    fig.suptitle('Initial Stock Quantity Distribution', fontsize=13, fontweight='bold')

    axes[0].hist(vals, bins=60, color='#9966cc', alpha=0.75, edgecolor='white')
    axes[0].axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--',
                    label=f'Mean   {vals.mean():.1f}')
    axes[0].axvline(np.median(vals), color='orange', lw=1.5, linestyle=':',
                    label=f'Median {np.median(vals):.1f}')
    axes[0].set_xlabel('Stock quantity per bin')
    axes[0].set_ylabel('SKU count')
    axes[0].set_title('Histogram  (mode≈35, mean≈50, range [5,200])')
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    if vals.max() > vals.min():
        kde = gaussian_kde(vals, bw_method='silverman')
        xs  = np.linspace(vals.min(), vals.max(), 500)
        axes[1].fill_between(xs, kde(xs), alpha=0.4, color='#9966cc')
        axes[1].plot(xs, kde(xs), color='#9966cc', lw=2)
        axes[1].axvline(vals.mean(),     color='red',    lw=1.5, linestyle='--')
        axes[1].axvline(np.median(vals), color='orange', lw=1.5, linestyle=':')
    axes[1].set_xlabel('Stock quantity per bin')
    axes[1].set_ylabel('Density')
    axes[1].set_title('KDE')
    axes[1].grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'stock_qty.png'))


def plot_volume_vs_weight(df: pd.DataFrame, out_dir: str, title_suffix: str = '') -> None:
    df = df.copy()
    df['volume'] = df['length'] * df['width'] * df['height']
    fig, ax = plt.subplots(figsize=(9, 6))
    hb = ax.hexbin(df['volume'], df['weight'], gridsize=60, cmap='YlOrRd', mincnt=1)
    fig.colorbar(hb, ax=ax, label='SKU count')
    ax.set_xlabel('Volume  (L × W × H)', fontsize=10)
    ax.set_ylabel('Weight', fontsize=10)
    ax.set_title(f'Volume vs Weight  (hex-bin){title_suffix}', fontsize=12, fontweight='bold')
    ax.grid(alpha=0.2)
    _save_close(fig, os.path.join(out_dir, 'volume_vs_weight.png'))


def plot_singleton_split(df: pd.DataFrame, out_dir: str) -> None:
    sing = df[df['is_singleton'] == 1]
    pall = df[df['is_singleton'] == 0]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    fig.suptitle('Singleton vs pallet-range dimension distributions', fontsize=12, fontweight='bold')
    for ax, col in zip(axes, ['length', 'width', 'height']):
        ax.hist(pall[col], bins=40, color='#5b9bd5', alpha=0.6, edgecolor='white', label=f'Pallet  n={len(pall):,}')
        ax.hist(sing[col], bins=20, color='#f4a030', alpha=0.7, edgecolor='white', label=f'Singleton  n={len(sing):,}')
        ax.set_title(col.capitalize());  ax.set_ylabel('Count')
        ax.legend(fontsize=8);  ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'singleton_split.png'))


def plot_dim_kde_overlay(df: pd.DataFrame, out_dir: str, title_suffix: str = '') -> None:
    """Overlay KDE curves for each dimension on a single plot."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle(f'Dimension KDE{title_suffix}', fontsize=12, fontweight='bold')

    for col, color in [('length', '#5b9bd5'), ('width', '#f4a030'), ('height', '#70ad47')]:
        vals = np.asarray(df[col], dtype=float)
        if vals.max() > vals.min():
            kde = gaussian_kde(vals, bw_method='silverman')
            xs  = np.linspace(vals.min(), vals.max(), 400)
            axes[0].plot(xs, kde(xs), lw=2, color=color, label=col)
    axes[0].set_xlabel('Dimension value');  axes[0].set_ylabel('Density')
    axes[0].set_title('Length / Width / Height KDE')
    axes[0].legend(fontsize=9);  axes[0].grid(alpha=0.3)

    df = df.copy()
    df['volume'] = df['length'] * df['width'] * df['height']
    v = np.asarray(df['volume'], dtype=float)
    if v.max() > v.min():
        kde = gaussian_kde(v, bw_method='silverman')
        xs  = np.linspace(v.min(), v.max(), 400)
        axes[1].fill_between(xs, kde(xs), alpha=0.3, color='#5b9bd5')
        axes[1].plot(xs, kde(xs), lw=2, color='#5b9bd5')
    axes[1].set_xlabel('Volume');  axes[1].set_ylabel('Density')
    axes[1].set_title('Volume KDE');  axes[1].grid(alpha=0.3)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'dim_kde.png'))


# ── callable API ───────────────────────────────────────────────────────────────

def generate_run(
    name                     : str,
    num_skus                 : int                = 76_500,
    handling_splits          : list[float] | None = None,
    category_splits          : list[float] | None = None,
    singleton_fraction       : float              = 0.5,
    seed                     : int                = 42,
    out_dir                  : str                = _DEFAULT_OUT_DIR,
    dim_spec                 : dict | None        = None,
    weight_spec              : dict | None        = None,
    quantity_spec            : dict | None        = None,
    reorder_coverage_batches : float              = REORDER_COVERAGE_BATCHES,
    verbose                  : bool               = True,
) -> str:
    """Generate one inventory run and return the path to the created run_dir.

    Parameters
    ----------
    name               : folder name under out_dir
    num_skus           : total SKU count
    handling_splits    : [conv_weight, nconv_weight]  (normalised internally)
    category_splits    : 6 category weights (normalised internally)
    singleton_fraction : fraction of SKUs capped at singleton dimensions (≤16)
    seed               : reproducibility seed
    out_dir            : parent directory; run is created at out_dir/name/
    dim_spec           : dict describing the dimension distribution (see module docstring)
    weight_spec        : dict describing the weight distribution
    verbose            : print progress lines
    """
    if handling_splits is None:
        handling_splits = [0.5, 0.5]
    if category_splits is None:
        category_splits = [1 / 6] * 6
    if dim_spec is None:
        dim_spec = DEFAULT_DIM_SPEC
    if weight_spec is None:
        weight_spec = DEFAULT_WEIGHT_SPEC
    if quantity_spec is None:
        quantity_spec = DEFAULT_QUANTITY_SPEC

    h_splits = [w / sum(handling_splits) for w in handling_splits]
    c_splits = [w / sum(category_splits) for w in category_splits]

    run_dir  = os.path.join(out_dir, name)
    plot_dir = os.path.join(run_dir, 'plots')
    os.makedirs(plot_dir, exist_ok=True)

    params = {
        'name'               : name,
        'timestamp'          : datetime.now().strftime('%Y%m%d_%H%M%S'),
        'seed'               : seed,
        'num_skus'           : num_skus,
        'handling_splits'    : h_splits,
        'category_splits'    : c_splits,
        'handling_labels'    : _HANDLINGS,
        'category_labels'    : _CATEGORIES,
        'singleton_fraction' : singleton_fraction,
        'dim_spec'           : dim_spec,
        'weight_spec'        : weight_spec,
        'quantity_spec'            : quantity_spec,
        'reorder_coverage_batches' : reorder_coverage_batches,
        'carton_min_dim'           : 3,
        'carton_max_dim'     : _CARTON_MAX_DIM,
        'singleton_max_dim'  : _SINGLETON_MAX_DIM,
    }
    with open(os.path.join(run_dir, 'params.json'), 'w') as f:
        json.dump(params, f, indent=2)

    def _log(msg):
        if verbose:
            print(msg)

    _log(f'[inventory:{name}] {num_skus:,} SKUs  seed={seed}  '
         f'dim={dim_spec.get("dist")}  weight={weight_spec.get("dist")}')

    t0        = time.perf_counter()
    inventory = build_inventory_with_profile(
        num_skus                 = num_skus,
        handling_splits          = h_splits,
        category_splits          = c_splits,
        singleton_fraction       = singleton_fraction,
        dim_spec                 = dim_spec,
        weight_spec              = weight_spec,
        seed                     = seed,
        quantity_spec            = quantity_spec,
        reorder_coverage_batches = reorder_coverage_batches,
    )
    _log(f'[inventory:{name}] Built {len(inventory.cartons):,} cartons  ({time.perf_counter()-t0:.2f}s)')

    db_path = os.path.join(run_dir, 'inventory.db')
    t0      = time.perf_counter()
    save_inventory_to_db(inventory, db_path, params)
    _log(f'[inventory:{name}] Saved → {db_path}  ({time.perf_counter()-t0:.2f}s)')

    conn = sqlite3.connect(db_path)
    df   = pd.read_sql_query('SELECT * FROM cartons', conn)
    conn.close()
    # is_singleton is not stored in the DB (derived from dimensions); add it
    # here so stats and plot functions have a consistent column to work with.
    df['is_singleton'] = (
        df[['length', 'width', 'height']].max(axis=1) <= _SINGLETON_MAX_DIM
    ).astype(int)

    stats = compute_stats(df)
    with open(os.path.join(run_dir, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=2)
    _log(f'[inventory:{name}] weight mean={stats["weight"]["mean"]:.1f}  '
         f'std={stats["weight"]["std"]:.1f}  '
         f'volume mean={stats["dimensions"]["volume"]["mean"]:.0f}')

    sfx = f'  [{name}]'
    plot_group_sizes(df, plot_dir, sfx)
    plot_dimensions(df, plot_dir, sfx)
    plot_weight(df, plot_dir, sfx)
    plot_demand(df, plot_dir)
    plot_stock_qty(df, plot_dir)
    plot_volume_vs_weight(df, plot_dir, sfx)
    plot_singleton_split(df, plot_dir)
    plot_dim_kde_overlay(df, plot_dir, sfx)
    _log(f'[inventory:{name}] Done.')

    return run_dir


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate and persist an Inventory to SQLite.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--name', default=None)
    parser.add_argument('--num-skus', type=int, default=76_500)
    parser.add_argument('--handling-splits', type=float, nargs=2, default=[0.5, 0.5],
                        metavar=('CONV', 'NCONV'))
    parser.add_argument('--category-splits', type=float, nargs=6, default=[1/6]*6, metavar='W')
    parser.add_argument('--singleton-fraction', type=float, default=0.5)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out-dir', default=_DEFAULT_OUT_DIR)
    parser.add_argument('--dim-spec', default=None,
                        help='JSON string for dimension distribution spec, e.g. '
                             '\'{"dist":"triangular","low":3,"high":48,"mode":48}\'')
    parser.add_argument('--weight-spec', default=None,
                        help='JSON string for weight distribution spec, e.g. '
                             '\'{"dist":"volume_poisson"}\'')
    args = parser.parse_args()

    ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
    name       = args.name or f'inv_{ts}'
    dim_spec   = json.loads(args.dim_spec)   if args.dim_spec   else DEFAULT_DIM_SPEC
    weight_spec = json.loads(args.weight_spec) if args.weight_spec else DEFAULT_WEIGHT_SPEC

    generate_run(
        name               = name,
        num_skus           = args.num_skus,
        handling_splits    = args.handling_splits,
        category_splits    = args.category_splits,
        singleton_fraction = args.singleton_fraction,
        seed               = args.seed,
        out_dir            = args.out_dir,
        dim_spec           = dim_spec,
        weight_spec        = weight_spec,
    )


if __name__ == '__main__':
    main()
