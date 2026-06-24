"""
Generate a demand-weighted sparse affinity matrix from an inventory DB.

Lift values reflect co-purchase likelihood: SKUs in the same
(handling × category) storage area whose demand_frequency × demand_qty_rate
(throughput) is high have higher affinity.  Only the top-K partners per SKU
are stored, producing a sparse matrix that is semantically richer and orders
of magnitude smaller than the old all-pairs approach.

Lift formula
------------
For each SKU s in a group, compute a Pareto rank score:

    rank_score(s) = 1 / sqrt(rank(s) + 1)      # rank 0 = highest demand → 1.0

For a pair (i, j) in the same storage area:

    geometric_mean = sqrt(rank_score_i * rank_score_j)
    lift(i, j)     = clip(
        min_lift + (max_lift - min_lift) * geometric_mean + Gaussian(0, noise_std),
        min_lift, max_lift
    )

For each SKU, the top-K partners (highest lift) are selected from a candidate
pool of the top-candidate_k SKUs by rank score.  Both (i→j) and (j→i) are
stored with the same lift so delta_lift and sum_lift remain symmetric.

Storage
-------
Expected rows ≈ N_skus × top_k × 2 (some overlap reduced by INSERT OR IGNORE).
For 76 500 SKUs with top_k=20: ~3 M rows ≈ 85 MB.
Previous all-pairs approach: up to 488 M rows ≈ 13 GB.

Output layout
-------------
<out_dir>/<name>/
    affinity.db              — SQLite: affinity + run_metadata
    params.json              — full parameter record
    stats.json               — descriptive statistics
    plots/
        lift_histogram.png   — distribution of stored lift values
        lift_by_group.png    — mean/median lift per storage group
        activity_vs_lift.png — demand throughput vs lift (confirms signal)
        degree_distribution.png — how many SKUs reference each partner
        cumulative_lift.png  — CDF of lift values

Usage
-----
python generate_affinity.py --inventory-db path/to/inventory.db [options]
python generate_affinity.py --inventory-db ... --estimate

Callable API
------------
from generate_affinity import generate_run
generate_run(inventory_db='...', name='run1', top_k=20)
"""

import matplotlib
matplotlib.use('Agg')

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_WH   = os.path.dirname(_HERE)           # parent Warehouse/ — domain imports + output dirs
sys.path.insert(0, _WH)

_DEFAULT_OUT_DIR     = os.path.join(_WH, 'generated', 'affinities')
_TOP_K_DEFAULT       = 20
_CANDIDATE_K_DEFAULT = 60    # must be > _TOP_K_DEFAULT; provides buffer for noise reranking
_NOISE_STD_DEFAULT   = 0.15  # Gaussian noise std added to each lift score
_BYTES_PER_ROW       = 28    # 2× INTEGER (8) + REAL (8) + SQLite overhead (~4)
_MB                  = 1_048_576
_GB                  = 1_073_741_824


# ── DB schema ──────────────────────────────────────────────────────────────────

_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS affinity (
        sku_i INTEGER NOT NULL,
        sku_j INTEGER NOT NULL,
        lift  REAL    NOT NULL,
        PRIMARY KEY (sku_i, sku_j)
    );
    CREATE INDEX IF NOT EXISTS idx_affinity_sku_i ON affinity(sku_i);
    CREATE TABLE IF NOT EXISTS run_metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
'''


def _init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=-262144')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── load helpers ───────────────────────────────────────────────────────────────

def load_affinity_from_db(db_path: str) -> dict:
    """Load entire affinity matrix into a plain dict[(sku_i, sku_j) → float].
    Only suitable for matrices that fit comfortably in RAM.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT sku_i, sku_j, lift FROM affinity').fetchall()
    conn.close()
    return {(i, j): lift for i, j, lift in rows}


def iter_affinity_from_db(db_path: str, chunk_size: int = 100_000):
    """Yield (sku_i, sku_j, lift) rows in chunks without loading all into RAM."""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute('SELECT sku_i, sku_j, lift FROM affinity')
    while True:
        chunk = cur.fetchmany(chunk_size)
        if not chunk:
            break
        yield from chunk
    conn.close()


# ── estimation ─────────────────────────────────────────────────────────────────

def estimate(group_counts: dict[str, int], top_k: int) -> dict:
    """Return expected pair counts and DB sizes for a top-K run."""
    groups = {}
    total_pairs = 0
    for group, n in group_counts.items():
        effective_k = min(top_k, n - 1) if n > 1 else 0
        # upper bound: n × effective_k (some pairs appear from both sides)
        pairs = n * effective_k
        groups[group] = {'eligible_skus': n, 'max_pairs': pairs, 'max_db_rows': pairs * 2}
        total_pairs += pairs

    db_rows  = total_pairs * 2
    db_bytes = db_rows * _BYTES_PER_ROW
    return {
        'groups'          : groups,
        'max_pairs'       : total_pairs,
        'max_db_rows'     : db_rows,
        'estimated_db_mb' : db_bytes / _MB,
        'estimated_db_gb' : db_bytes / _GB,
    }


def print_estimate(est: dict, top_k: int) -> None:
    print(f'\n  Affinity matrix estimate  (top-{top_k} partners/SKU)')
    print(f'  {"Group":<30}  {"SKUs":>6}  {"Max pairs":>12}  {"Max rows":>12}')
    print(f'  {"-"*30}  {"------":>6}  {"----------":>12}  {"----------":>12}')
    for group, info in est['groups'].items():
        print(f'  {group:<30}  {info["eligible_skus"]:>6,}  '
              f'{info["max_pairs"]:>12,}  {info["max_db_rows"]:>12,}')
    print(f'  {"TOTAL":<30}  {"":>6}  '
          f'{est["max_pairs"]:>12,}  {est["max_db_rows"]:>12,}')
    size_str = (f'{est["estimated_db_gb"]:.2f} GB'
                if est['estimated_db_gb'] >= 1.0
                else f'{est["estimated_db_mb"]:.1f} MB')
    print(f'\n  Estimated max SQLite DB size : {size_str}  (actual is smaller due to overlaps)\n')


# ── rank scoring ───────────────────────────────────────────────────────────────

def _rank_scores(
    skus    : list[int],
    activity: dict[int, float],
) -> tuple[list[int], dict[int, float]]:
    """Return (sorted_skus, rank_score_map), sorted highest-to-lowest activity.

    rank_score = 1 / sqrt(rank + 1):
        rank 0 (highest demand) → 1.000
        rank 1                  → 0.707
        rank 3                  → 0.500
        rank 9                  → 0.316
        rank 99                 → 0.100
        rank 399                → 0.050
    This gives a realistic Pareto-like spread so high-velocity SKUs are
    clearly dominant partners without saturating low-velocity pairs at zero.
    """
    sorted_skus = sorted(skus, key=lambda s: activity.get(s, 0.0), reverse=True)
    scores = {sku: 1.0 / (r + 1) ** 0.5 for r, sku in enumerate(sorted_skus)}
    return sorted_skus, scores


# ── generation ─────────────────────────────────────────────────────────────────

def generate_affinity(
    group_skus  : dict[str, list[int]],
    sku_demand  : dict[int, tuple[float, float]],   # sku → (freq, qty_rate)
    min_lift    : float,
    max_lift    : float,
    noise_std   : float,
    top_k       : int,
    candidate_k : int,
    rng         : random.Random,
    conn        : sqlite3.Connection,
    batch_size  : int = 500_000,
) -> dict:
    """Generate demand-weighted top-K affinity pairs and insert to the DB.

    For each SKU i, scores the top-candidate_k SKUs by Pareto rank, adds
    Gaussian noise, and keeps the top-k highest-scoring partners.  Both
    (i→j) and (j→i) are inserted with the same lift value; INSERT OR IGNORE
    silently skips pairs already stored from a previous direction.

    Returns per-group statistics for stats.json and plots.
    """
    lift_range  = max_lift - min_lift
    group_stats = {}
    total_rows  = 0
    t_start     = time.perf_counter()

    for group_key, skus in sorted(group_skus.items()):
        n = len(skus)
        if n < 2:
            group_stats[group_key] = {
                'eligible_skus': n, 'pairs_stored': 0, 'db_rows': 0,
                'lift_mean': 0.0, 'lift_std': 0.0,
                'lift_min': 0.0, 'lift_max': 0.0, 'lift_median': 0.0,
            }
            continue

        t_group = time.perf_counter()

        activity    = {s: sku_demand[s][0] * sku_demand[s][1] for s in skus}
        sorted_skus, rs = _rank_scores(skus, activity)

        eff_top_k  = min(top_k, n - 1)
        eff_cand_k = min(candidate_k, n - 1)

        pending    : list[tuple[int, int, float]] = []
        lift_sample: list[float]                  = []
        rows_written = 0

        for sku_i in sorted_skus:
            rs_i = rs[sku_i]

            # Candidate pool: top eff_cand_k by rank score, excluding sku_i.
            # Since sorted_skus is ordered by rank, a prefix slice gives the
            # highest-activity candidates.
            if n - 1 <= eff_cand_k:
                candidates = [s for s in sorted_skus if s != sku_i]
            else:
                pool       = sorted_skus[:eff_cand_k + 1]
                candidates = [s for s in pool if s != sku_i][:eff_cand_k]

            # Score each candidate: geometric mean of rank scores + noise.
            scored: list[tuple[float, int]] = []
            for sku_j in candidates:
                geo  = (rs_i * rs[sku_j]) ** 0.5
                lift = min_lift + lift_range * geo + rng.gauss(0, noise_std)
                lift = max(min_lift, min(max_lift, lift))
                scored.append((lift, sku_j))

            scored.sort(reverse=True)

            for lift_val, sku_j in scored[:eff_top_k]:
                lift_sample.append(lift_val)
                # Both directions with the same lift (symmetric convention).
                pending.append((sku_i, sku_j, lift_val))
                pending.append((sku_j, sku_i, lift_val))

                if len(pending) >= batch_size:
                    conn.executemany(
                        'INSERT OR IGNORE INTO affinity VALUES (?,?,?)', pending
                    )
                    conn.commit()
                    rows_written += len(pending)
                    total_rows   += len(pending)
                    pending.clear()

        if pending:
            conn.executemany('INSERT OR IGNORE INTO affinity VALUES (?,?,?)', pending)
            conn.commit()
            rows_written += len(pending)
            total_rows   += len(pending)
            pending.clear()

        elapsed   = time.perf_counter() - t_group
        elapsed_t = time.perf_counter() - t_start
        pairs     = rows_written // 2
        print(f'  [{group_key}]  {n:,} SKUs  top-{eff_top_k}  →  '
              f'{pairs:,} pairs  ({rows_written:,} rows)  {elapsed:.1f}s  '
              f'[total {total_rows:,} / {elapsed_t:.0f}s elapsed]')

        v = np.array(lift_sample, dtype=np.float32)
        group_stats[group_key] = {
            'eligible_skus': n,
            'pairs_stored' : pairs,
            'db_rows'      : rows_written,
            'lift_mean'    : float(v.mean())      if len(v) else 0.0,
            'lift_std'     : float(v.std())       if len(v) else 0.0,
            'lift_min'     : float(v.min())       if len(v) else 0.0,
            'lift_max'     : float(v.max())       if len(v) else 0.0,
            'lift_median'  : float(np.median(v)) if len(v) else 0.0,
        }
        del v, lift_sample

    return group_stats


# ── statistics ─────────────────────────────────────────────────────────────────

def compute_stats(conn: sqlite3.Connection, group_stats: dict, params: dict) -> dict:
    total_rows  = conn.execute('SELECT COUNT(*) FROM affinity').fetchone()[0]
    total_pairs = total_rows // 2

    sample = conn.execute(
        'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT 2000000'
    ).fetchall()
    lifts = np.array([r[0] for r in sample], dtype=np.float32)

    def _s(v):
        return {
            'min': float(v.min()), 'max': float(v.max()),
            'mean': float(v.mean()), 'median': float(np.median(v)),
            'std': float(v.std()),
            'p25': float(np.percentile(v, 25)),
            'p75': float(np.percentile(v, 75)),
            'p95': float(np.percentile(v, 95)),
        }

    return {
        'total_unique_pairs' : total_pairs,
        'total_db_rows'      : total_rows,
        'estimated_db_mb'    : total_rows * _BYTES_PER_ROW / _MB,
        'lift_sample_size'   : len(lifts),
        'lift_stats'         : _s(lifts),
        'groups'             : group_stats,
        'source_inventory_db': params['source_inventory_db'],
    }


# ── plots ──────────────────────────────────────────────────────────────────────

def _save_close(fig, path: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_lift_histogram(
    conn: sqlite3.Connection, out_dir: str,
    min_lift: float, max_lift: float,
) -> None:
    rows  = conn.execute(
        'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT 2000000'
    ).fetchall()
    lifts = np.array([r[0] for r in rows], dtype=np.float32)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(lifts, bins=80, color='#5b9bd5', alpha=0.75, edgecolor='white')
    ax.axvline(lifts.mean(),     color='red',    lw=1.5, linestyle='--',
               label=f'Mean   {lifts.mean():.3f}')
    ax.axvline(np.median(lifts), color='orange', lw=1.5, linestyle=':',
               label=f'Median {np.median(lifts):.3f}')
    ax.set_xlabel('Lift value', fontsize=10)
    ax.set_ylabel('Pair count', fontsize=10)
    ax.set_title(
        f'Lift value distribution  [range {min_lift}–{max_lift}]\n'
        f'Concentrated toward lower values — reflects Pareto demand structure',
        fontsize=11, fontweight='bold'
    )
    ax.legend(fontsize=9);  ax.grid(axis='y', alpha=0.3)
    _save_close(fig, os.path.join(out_dir, 'lift_histogram.png'))


def plot_lift_by_group(group_stats: dict, out_dir: str) -> None:
    groups  = sorted(group_stats.keys())
    means   = [group_stats[g]['lift_mean']   for g in groups]
    stds    = [group_stats[g]['lift_std']    for g in groups]
    medians = [group_stats[g]['lift_median'] for g in groups]
    pairs   = [group_stats[g]['pairs_stored'] for g in groups]

    x = np.arange(len(groups))
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle('Lift statistics per (handling × category) group', fontsize=12, fontweight='bold')

    axes[0].bar(x, means, yerr=stds, capsize=4, color='#5b9bd5', alpha=0.80,
                edgecolor='white', label='Mean ± std')
    axes[0].plot(x, medians, 'D', color='orange', ms=5, label='Median')
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([g.replace('/', '\n') for g in groups], fontsize=7)
    axes[0].set_ylabel('Lift value')
    axes[0].set_title('Mean ± std  (median diamond)')
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    axes[1].bar(x, pairs, color='#70ad47', alpha=0.80, edgecolor='white')
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([g.replace('/', '\n') for g in groups], fontsize=7)
    axes[1].set_ylabel('Pairs stored (unique)')
    axes[1].set_title('Pairs per group  (linear in N × top_k)')
    axes[1].yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f'{v/1e6:.1f}M' if v >= 1e6 else f'{v/1e3:.0f}K')
    )
    axes[1].grid(axis='y', alpha=0.3)

    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'lift_by_group.png'))


def plot_activity_vs_lift(
    conn       : sqlite3.Connection,
    group_skus : dict[str, list[int]],
    sku_demand : dict[int, tuple[float, float]],
    out_dir    : str,
    sample_n   : int = 50_000,
) -> None:
    """Scatter plot of geometric-mean demand throughput vs stored lift."""
    activity = {s: d[0] * d[1] for s, d in sku_demand.items()}

    rows = conn.execute(
        f'SELECT sku_i, sku_j, lift FROM affinity WHERE sku_i < sku_j LIMIT {sample_n}'
    ).fetchall()
    if not rows:
        return

    geo_means, lifts = [], []
    for sku_i, sku_j, lift in rows:
        ai = activity.get(sku_i, 0.0)
        aj = activity.get(sku_j, 0.0)
        if ai > 0 and aj > 0:
            geo_means.append((ai * aj) ** 0.5)
            lifts.append(lift)

    if not geo_means:
        return

    geo_arr = np.array(geo_means, dtype=np.float64)
    lft_arr = np.array(lifts, dtype=np.float32)

    # log-scale x since activity follows Pareto distribution
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.scatter(geo_arr, lft_arr, s=2, alpha=0.15, color='#5b9bd5', rasterized=True)

    # Bin means overlay
    bins     = np.percentile(geo_arr, np.linspace(0, 100, 21))
    bin_idxs = np.digitize(geo_arr, bins)
    bin_mids, bin_means = [], []
    for b in range(1, len(bins)):
        mask = bin_idxs == b
        if mask.sum() > 5:
            bin_mids.append((bins[b - 1] + bins[b]) / 2)
            bin_means.append(lft_arr[mask].mean())
    ax.plot(bin_mids, bin_means, color='red', lw=2, label='Bin mean')

    ax.set_xscale('log')
    ax.set_xlabel('Geometric mean demand throughput  (freq × qty, log scale)', fontsize=10)
    ax.set_ylabel('Lift value', fontsize=10)
    ax.set_title(
        f'Demand throughput vs lift  (n={len(geo_arr):,})\n'
        f'Higher-demand pairs have higher affinity',
        fontsize=11, fontweight='bold'
    )
    ax.legend(fontsize=9);  ax.grid(alpha=0.2)
    _save_close(fig, os.path.join(out_dir, 'activity_vs_lift.png'))


def plot_degree_distribution(conn: sqlite3.Connection, out_dir: str) -> None:
    """Histogram of how many SKUs list each SKU as a partner (in-degree).

    High-demand SKUs appear as sku_j for many others → long right tail.
    """
    rows = conn.execute(
        'SELECT sku_j, COUNT(*) FROM affinity GROUP BY sku_j'
    ).fetchall()
    if not rows:
        return

    degrees = np.array([r[1] for r in rows], dtype=np.int32)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle('Partner in-degree distribution  (how many SKUs reference each partner)',
                 fontsize=12, fontweight='bold')

    axes[0].hist(degrees, bins=60, color='#f4a030', alpha=0.80, edgecolor='white')
    axes[0].set_xlabel('In-degree (times referenced as sku_j)', fontsize=10)
    axes[0].set_ylabel('SKU count', fontsize=10)
    axes[0].set_title(f'Distribution  (mean={degrees.mean():.1f}  max={degrees.max()})', fontsize=10)
    axes[0].grid(axis='y', alpha=0.3)

    sorted_deg = np.sort(degrees)[::-1]
    axes[1].plot(np.arange(1, len(sorted_deg) + 1), sorted_deg,
                 color='#5b9bd5', lw=1.5)
    axes[1].set_xlabel('SKU rank (by in-degree)', fontsize=10)
    axes[1].set_ylabel('In-degree', fontsize=10)
    axes[1].set_title('Rank curve  — confirms Pareto hub structure', fontsize=10)
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'degree_distribution.png'))


def plot_cumulative_lift(conn: sqlite3.Connection, out_dir: str) -> None:
    rows  = conn.execute(
        'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT 2000000'
    ).fetchall()
    lifts = np.sort([r[0] for r in rows])

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(lifts, np.arange(1, len(lifts) + 1) / len(lifts),
            color='#5b9bd5', lw=2)
    ax.set_xlabel('Lift value', fontsize=10)
    ax.set_ylabel('Cumulative fraction', fontsize=10)
    ax.set_title(f'CDF of lift values  (n={len(lifts):,})', fontsize=12, fontweight='bold')
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.grid(alpha=0.3)
    _save_close(fig, os.path.join(out_dir, 'cumulative_lift.png'))


# ── callable API ───────────────────────────────────────────────────────────────

def generate_run(
    inventory_db : str,
    name         : str,
    out_dir      : str        = _DEFAULT_OUT_DIR,
    top_k        : int        = _TOP_K_DEFAULT,
    candidate_k  : int        = _CANDIDATE_K_DEFAULT,
    min_lift     : float      = 1.0,
    max_lift     : float      = 5.0,
    noise_std    : float      = _NOISE_STD_DEFAULT,
    seed         : int        = 0,
    batch_size   : int        = 500_000,
    verbose      : bool       = True,
) -> str:
    """Generate one demand-weighted top-K affinity run.

    Parameters
    ----------
    inventory_db : path to inventory.db produced by generate_inventory.py
    name         : folder name under out_dir
    out_dir      : parent directory; run is created at out_dir/name/
    top_k        : partners stored per SKU (default 20)
    candidate_k  : candidate pool per SKU before noise reranking (default 60)
    min_lift     : lower bound of the lift scale (default 1.0)
    max_lift     : upper bound of the lift scale (default 5.0)
    noise_std    : Gaussian noise std added to each lift score (default 0.15)
    seed         : RNG seed for reproducibility
    batch_size   : DB insert batch size
    verbose      : print progress lines

    Returns the path to the created run directory.
    """
    def _log(msg):
        if verbose:
            print(msg)

    inv_db = os.path.abspath(inventory_db)
    if not os.path.exists(inv_db):
        raise FileNotFoundError(f'inventory DB not found: {inv_db}')

    conn_inv = sqlite3.connect(inv_db)
    rows = conn_inv.execute(
        'SELECT sku, handling, category, demand_frequency, demand_qty_rate '
        'FROM orders ORDER BY sku'
    ).fetchall()
    conn_inv.close()

    # Group by storage area (handling × category) and collect demand data
    group_skus: dict[str, list[int]]            = {}
    sku_demand: dict[int, tuple[float, float]]   = {}
    for sku, handling, category, freq, qty in rows:
        key = f'{handling}/{category}'
        group_skus.setdefault(key, []).append(sku)
        sku_demand[sku] = (freq, qty)

    group_counts = {k: len(v) for k, v in group_skus.items()}
    _log(f'[affinity:{name}] {len(rows):,} SKUs  {len(group_skus)} groups  top_k={top_k}')

    est      = estimate(group_counts, top_k)
    size_str = (f'{est["estimated_db_gb"]:.2f} GB'
                if est['estimated_db_gb'] >= 1.0
                else f'{est["estimated_db_mb"]:.1f} MB')
    _log(f'[affinity:{name}] Estimated max: {est["max_pairs"]:,} pairs  ~{size_str}')

    run_dir  = os.path.join(out_dir, name)
    plot_dir = os.path.join(run_dir, 'plots')
    os.makedirs(plot_dir, exist_ok=True)

    params = {
        'name'               : name,
        'timestamp'          : datetime.now().strftime('%Y%m%d_%H%M%S'),
        'seed'               : seed,
        'source_inventory_db': inv_db,
        'top_k'              : top_k,
        'candidate_k'        : candidate_k,
        'min_lift'           : min_lift,
        'max_lift'           : max_lift,
        'noise_std'          : noise_std,
        'batch_size'         : batch_size,
        'total_sku_count'    : len(rows),
        'group_counts'       : group_counts,
        'estimated_db_mb'    : est['estimated_db_mb'],
    }
    with open(os.path.join(run_dir, 'params.json'), 'w') as f:
        json.dump(params, f, indent=2)

    db_path  = os.path.join(run_dir, 'affinity.db')
    conn_aff = _init_db(db_path)
    conn_aff.execute('INSERT OR REPLACE INTO run_metadata VALUES (?,?)',
                     ('params_json', json.dumps(params, indent=2)))
    conn_aff.commit()

    rng = random.Random(seed)
    _log(f'[affinity:{name}] Generating pairs...')
    t0          = time.perf_counter()
    group_stats = generate_affinity(
        group_skus  = group_skus,
        sku_demand  = sku_demand,
        min_lift    = min_lift,
        max_lift    = max_lift,
        noise_std   = noise_std,
        top_k       = top_k,
        candidate_k = candidate_k,
        rng         = rng,
        conn        = conn_aff,
        batch_size  = batch_size,
    )
    elapsed    = time.perf_counter() - t0
    total_rows = conn_aff.execute('SELECT COUNT(*) FROM affinity').fetchone()[0]
    _log(f'[affinity:{name}] Done  {total_rows:,} rows  '
         f'({elapsed:.1f}s  {total_rows / max(elapsed, 0.001):.0f} rows/s)')

    stats = compute_stats(conn_aff, group_stats, params)
    with open(os.path.join(run_dir, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=2)

    _log(f'[affinity:{name}] Generating plots...')
    plot_lift_histogram(conn_aff, plot_dir, min_lift, max_lift)
    plot_lift_by_group(group_stats, plot_dir)
    plot_activity_vs_lift(conn_aff, group_skus, sku_demand, plot_dir)
    plot_degree_distribution(conn_aff, plot_dir)
    plot_cumulative_lift(conn_aff, plot_dir)
    conn_aff.close()

    _log(f'[affinity:{name}] Saved → {run_dir}')
    return run_dir


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate a demand-weighted sparse affinity matrix.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--inventory-db', required=True,
                        help='Path to inventory.db produced by generate_inventory.py')
    parser.add_argument('--top-k',       type=int,   default=_TOP_K_DEFAULT,
                        help='Partners stored per SKU')
    parser.add_argument('--candidate-k', type=int,   default=_CANDIDATE_K_DEFAULT,
                        help='Candidate pool size before noise reranking (must be > top-k)')
    parser.add_argument('--min-lift',    type=float, default=1.0)
    parser.add_argument('--max-lift',    type=float, default=5.0)
    parser.add_argument('--noise-std',   type=float, default=_NOISE_STD_DEFAULT,
                        help='Gaussian noise std added to lift scores')
    parser.add_argument('--seed',        type=int,   default=0)
    parser.add_argument('--batch-size',  type=int,   default=500_000)
    parser.add_argument('--name',        default=None)
    parser.add_argument('--out-dir',     default=_DEFAULT_OUT_DIR)
    parser.add_argument('--estimate',    action='store_true',
                        help='Print expected pair count and DB size, then exit')
    args = parser.parse_args()

    if args.candidate_k <= args.top_k:
        parser.error(f'--candidate-k ({args.candidate_k}) must be > --top-k ({args.top_k})')

    inv_db = os.path.abspath(args.inventory_db)
    if not os.path.exists(inv_db):
        sys.exit(f'inventory DB not found: {inv_db}')

    if args.estimate:
        conn_inv     = sqlite3.connect(inv_db)
        rows         = conn_inv.execute('SELECT handling, category FROM orders').fetchall()
        conn_inv.close()
        group_counts: dict[str, int] = {}
        for h, c in rows:
            key = f'{h}/{c}'
            group_counts[key] = group_counts.get(key, 0) + 1
        print_estimate(estimate(group_counts, args.top_k), args.top_k)
        return

    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = args.name or f'aff_top{args.top_k}_{ts}'

    generate_run(
        inventory_db = inv_db,
        name         = name,
        out_dir      = args.out_dir,
        top_k        = args.top_k,
        candidate_k  = args.candidate_k,
        min_lift     = args.min_lift,
        max_lift     = args.max_lift,
        noise_std    = args.noise_std,
        seed         = args.seed,
        batch_size   = args.batch_size,
    )


if __name__ == '__main__':
    main()
