"""
Generate a full affinity matrix from an inventory DB and persist it to SQLite.

Unlike the runtime Inventory.affinity_matrix() call (capped at 500 SKUs/group),
this script has no built-in group cap and is intended to run offline in downtime.
A --max-per-group flag is provided if you want to test with a smaller matrix.

Before running, use --estimate to print the expected pair count and DB size, then
decide whether to proceed.

Output layout
-------------
<out_dir>/<name>/
    affinity.db      — SQLite: affinity + run_metadata tables
    params.json      — full parameter record (includes path to source inventory DB)
    stats.json       — descriptive statistics
    plots/
        lift_histogram.png      distribution of all lift values
        lift_kde.png            KDE of lift values, split by group
        lift_by_group.png       box plot: lift distribution per (handling × category) group
        pairs_per_group.png     bar chart: pair count per group (shows quadratic scaling)
        cumulative_lift.png     CDF of lift values

Usage
-----
python generate_affinity.py --inventory-db path/to/inventory.db [options]
python generate_affinity.py --help

Estimate size before committing
--------------------------------
python generate_affinity.py --inventory-db path/to/inventory.db --estimate

Loading the result back
-----------------------
from generate_affinity import load_affinity_from_db
affinity = load_affinity_from_db('path/to/affinity.db')
# returns dict[tuple[int,int], float]  (both directions stored)

# For large matrices, use the streaming version:
for sku_i, sku_j, lift in iter_affinity_from_db('path/to/affinity.db'):
    ...
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
sys.path.insert(0, _HERE)

_DEFAULT_OUT_DIR = os.path.join(_HERE, 'generated', 'affinities')

_BYTES_PER_ROW = 28  # 2× INTEGER (8) + REAL (8) + SQLite row overhead (~4)
_MB = 1_048_576
_GB = 1_073_741_824


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
    conn.execute('PRAGMA cache_size=-262144')   # 256 MB page cache
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── load / save helpers ────────────────────────────────────────────────────────

def load_affinity_from_db(db_path: str) -> dict:
    """Load entire affinity matrix into a plain dict[tuple[int,int], float].

    Only suitable for matrices that fit comfortably in RAM.
    For very large matrices, use iter_affinity_from_db() instead.
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

def estimate(group_counts: dict[str, int], max_per_group: int | None) -> dict:
    """Return expected pair counts and estimated DB sizes per group."""
    groups = {}
    total_pairs = 0
    for group, n in group_counts.items():
        eligible = n if max_per_group is None else min(n, max_per_group)
        pairs = eligible * (eligible - 1) // 2
        groups[group] = {'eligible_skus': eligible, 'unique_pairs': pairs, 'db_rows': pairs * 2}
        total_pairs += pairs

    db_rows  = total_pairs * 2
    db_bytes = db_rows * _BYTES_PER_ROW
    return {
        'groups'           : groups,
        'total_unique_pairs': total_pairs,
        'total_db_rows'    : db_rows,
        'estimated_db_mb'  : db_bytes / _MB,
        'estimated_db_gb'  : db_bytes / _GB,
    }


def print_estimate(est: dict) -> None:
    print('\n  Affinity matrix estimate')
    print(f'  {"Group":<30}  {"SKUs":>6}  {"Pairs":>14}  {"DB rows":>14}')
    print(f'  {"-"*30}  {"------":>6}  {"----------":>14}  {"----------":>14}')
    for group, info in est['groups'].items():
        print(f'  {group:<30}  {info["eligible_skus"]:>6,}  '
              f'{info["unique_pairs"]:>14,}  {info["db_rows"]:>14,}')
    print(f'  {"TOTAL":<30}  {"":>6}  '
          f'{est["total_unique_pairs"]:>14,}  {est["total_db_rows"]:>14,}')
    gb = est['estimated_db_gb']
    size_str = f'{gb:.2f} GB' if gb >= 1.0 else f'{est["estimated_db_mb"]:.1f} MB'
    print(f'\n  Estimated SQLite DB size : {size_str}')
    if gb > 5:
        print(f'  WARNING: this is a large file. Consider --max-per-group to reduce size.')
    print()


# ── generation ─────────────────────────────────────────────────────────────────

def generate_affinity(
    group_skus   : dict[str, list[int]],
    max_per_group: int | None,
    min_lift     : float,
    max_lift     : float,
    rng          : random.Random,
    conn         : sqlite3.Connection,
    batch_size   : int = 500_000,
) -> dict:
    """Generate all within-group pairs and insert to DB in batches.

    Returns per-group statistics (for stats.json and plots).
    """
    group_stats = {}
    total_rows  = 0
    t_start     = time.perf_counter()

    for group_key, skus in sorted(group_skus.items()):
        eligible = skus if max_per_group is None else skus[:max_per_group]
        n        = len(eligible)
        expected = n * (n - 1)   # both directions
        t_group  = time.perf_counter()

        lift_values: list[float] = []
        pending: list[tuple[int, int, float]] = []
        rows_written = 0

        for idx in range(n):
            sku_i = eligible[idx]
            for sku_j in eligible[idx + 1:]:
                lift_val = rng.uniform(min_lift, max_lift)
                lift_values.append(lift_val)
                pending.append((sku_i, sku_j, lift_val))
                pending.append((sku_j, sku_i, lift_val))

                if len(pending) >= batch_size:
                    conn.executemany('INSERT OR IGNORE INTO affinity VALUES (?,?,?)', pending)
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
        print(f'  [{group_key}]  {n:,} SKUs  →  {pairs:,} pairs  '
              f'({rows_written:,} rows)  {elapsed:.1f}s  '
              f'[total {total_rows:,} rows / {elapsed_t:.0f}s elapsed]')

        v = np.array(lift_values, dtype=np.float32)
        group_stats[group_key] = {
            'eligible_skus': n,
            'unique_pairs' : pairs,
            'db_rows'      : rows_written,
            'lift_mean'    : float(v.mean()) if len(v) else 0.0,
            'lift_std'     : float(v.std())  if len(v) else 0.0,
            'lift_min'     : float(v.min())  if len(v) else 0.0,
            'lift_max'     : float(v.max())  if len(v) else 0.0,
            'lift_median'  : float(np.median(v)) if len(v) else 0.0,
        }
        del v, lift_values

    return group_stats


# ── statistics ─────────────────────────────────────────────────────────────────

def compute_stats(conn: sqlite3.Connection, group_stats: dict, params: dict) -> dict:
    total_rows = conn.execute('SELECT COUNT(*) FROM affinity').fetchone()[0]
    total_pairs = total_rows // 2

    # sample up to 5M rows for global lift stats (avoid loading entire large matrix)
    sample_limit = 5_000_000
    sample = conn.execute(
        f'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT {sample_limit}'
    ).fetchall()
    lifts = np.array([r[0] for r in sample], dtype=np.float32)

    def _s(v):
        return {
            'min': float(v.min()), 'max': float(v.max()),
            'mean': float(v.mean()), 'median': float(np.median(v)),
            'std': float(v.std()), 'p25': float(np.percentile(v, 25)),
            'p75': float(np.percentile(v, 75)), 'p95': float(np.percentile(v, 95)),
        }

    return {
        'total_unique_pairs'   : total_pairs,
        'total_db_rows'        : total_rows,
        'estimated_db_mb'      : total_rows * _BYTES_PER_ROW / _MB,
        'lift_sample_size'     : len(lifts),
        'lift_stats'           : _s(lifts),
        'groups'               : group_stats,
        'source_inventory_db'  : params['source_inventory_db'],
    }


# ── plots ──────────────────────────────────────────────────────────────────────

def _save_close(fig, path: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_lift_histogram(conn: sqlite3.Connection, out_dir: str,
                        min_lift: float, max_lift: float, sample_limit: int = 2_000_000) -> None:
    rows  = conn.execute(
        f'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT {sample_limit}'
    ).fetchall()
    lifts = np.array([r[0] for r in rows], dtype=np.float32)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(lifts, bins=80, color='#5b9bd5', alpha=0.75, edgecolor='white')
    ax.axvline(lifts.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {lifts.mean():.3f}')
    ax.axvline(np.median(lifts), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(lifts):.3f}')
    ax.set_xlabel('Lift value',  fontsize=10)
    ax.set_ylabel('Pair count',  fontsize=10)
    sample_note = f' (sample of {len(lifts):,})' if len(lifts) == sample_limit else ''
    ax.set_title(f'Lift value distribution{sample_note}  '
                 f'[range {min_lift}–{max_lift}]', fontsize=12, fontweight='bold')
    ax.legend(fontsize=9);  ax.grid(axis='y', alpha=0.3)
    _save_close(fig, os.path.join(out_dir, 'lift_histogram.png'))


def plot_lift_kde(conn: sqlite3.Connection, group_skus: dict[str, list[int]],
                  out_dir: str, sample_per_group: int = 50_000) -> None:
    from scipy.stats import gaussian_kde

    fig, ax = plt.subplots(figsize=(11, 5))
    colors  = plt.cm.tab20(np.linspace(0, 1, len(group_skus)))

    for (group_key, skus), color in zip(sorted(group_skus.items()), colors):
        if len(skus) < 2:
            continue
        sample_skus = skus[:200]   # only need a representive subset
        ph   = ','.join('?' * len(sample_skus))
        rows = conn.execute(
            f'SELECT lift FROM affinity WHERE sku_i IN ({ph}) '
            f'AND sku_i < sku_j LIMIT {sample_per_group}',
            sample_skus,
        ).fetchall()
        if len(rows) < 10:
            continue
        vals = np.array([r[0] for r in rows], dtype=np.float32)
        kde  = gaussian_kde(vals, bw_method='silverman')
        xs   = np.linspace(vals.min(), vals.max(), 300)
        ax.plot(xs, kde(xs), color=color, lw=2,
                label=f'{group_key}  (n={len(vals):,})')

    ax.set_xlabel('Lift value', fontsize=10);  ax.set_ylabel('Density', fontsize=10)
    ax.set_title('Lift KDE per (handling × category) group', fontsize=12, fontweight='bold')
    ax.legend(fontsize=7, ncol=2);  ax.grid(alpha=0.3)
    _save_close(fig, os.path.join(out_dir, 'lift_kde.png'))


def plot_lift_by_group(group_stats: dict, out_dir: str) -> None:
    groups  = sorted(group_stats.keys())
    means   = [group_stats[g]['lift_mean']   for g in groups]
    stds    = [group_stats[g]['lift_std']    for g in groups]
    medians = [group_stats[g]['lift_median'] for g in groups]

    x = np.arange(len(groups))
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle('Lift statistics per (handling × category) group', fontsize=12, fontweight='bold')

    axes[0].bar(x, means, yerr=stds, capsize=4, color='#5b9bd5', alpha=0.80,
                edgecolor='white', label='Mean ± std')
    axes[0].plot(x, medians, 'D', color='orange', ms=5, label='Median')
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([g.replace('/', '\n') for g in groups], fontsize=7)
    axes[0].set_ylabel('Lift value');  axes[0].set_title('Mean ± std  (median diamond)')
    axes[0].legend(fontsize=9);  axes[0].grid(axis='y', alpha=0.3)

    pairs = [group_stats[g]['unique_pairs'] for g in groups]
    axes[1].bar(x, pairs, color='#70ad47', alpha=0.80, edgecolor='white')
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([g.replace('/', '\n') for g in groups], fontsize=7)
    axes[1].set_ylabel('Unique pair count');  axes[1].set_title('Pairs per group')
    axes[1].yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f'{v/1e6:.1f}M' if v >= 1e6 else f'{v/1e3:.0f}K'))
    axes[1].grid(axis='y', alpha=0.3)

    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'lift_by_group.png'))


def plot_pairs_per_group(group_stats: dict, out_dir: str) -> None:
    groups  = sorted(group_stats.keys())
    n_skus  = [group_stats[g]['eligible_skus'] for g in groups]
    n_pairs = [group_stats[g]['unique_pairs']  for g in groups]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle('Group size vs pair count  (quadratic scaling)', fontsize=12, fontweight='bold')

    x = np.arange(len(groups))
    axes[0].bar(x, n_skus, color='#5b9bd5', alpha=0.80, edgecolor='white')
    axes[0].set_xticks(x)
    axes[0].set_xticklabels([g.replace('/', '\n') for g in groups], fontsize=7)
    axes[0].set_ylabel('SKU count');  axes[0].set_title('Eligible SKUs per group')
    axes[0].grid(axis='y', alpha=0.3)

    # scatter: n_skus vs n_pairs with quadratic reference curve
    ns = np.array(n_skus)
    axes[1].scatter(ns, n_pairs, color='#f4a030', s=60, zorder=3, label='Groups')
    xs_fit = np.linspace(0, max(ns) * 1.05, 200)
    axes[1].plot(xs_fit, xs_fit * (xs_fit - 1) / 2, color='#5b9bd5', lw=1.5,
                 linestyle='--', label='n(n-1)/2 reference')
    axes[1].set_xlabel('SKU count in group');  axes[1].set_ylabel('Unique pairs')
    axes[1].set_title('SKU count vs pairs  (scatter)')
    axes[1].legend(fontsize=9);  axes[1].grid(alpha=0.3)

    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'pairs_per_group.png'))


def plot_cumulative_lift(conn: sqlite3.Connection, out_dir: str,
                         sample_limit: int = 2_000_000) -> None:
    rows  = conn.execute(
        f'SELECT lift FROM affinity WHERE sku_i < sku_j LIMIT {sample_limit}'
    ).fetchall()
    lifts = np.sort([r[0] for r in rows])

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(lifts, np.arange(1, len(lifts) + 1) / len(lifts),
            color='#5b9bd5', lw=2)
    ax.set_xlabel('Lift value', fontsize=10);  ax.set_ylabel('Cumulative fraction', fontsize=10)
    sample_note = f' (sample of {len(lifts):,})' if len(lifts) == sample_limit else ''
    ax.set_title(f'CDF of lift values{sample_note}', fontsize=12, fontweight='bold')
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))
    ax.grid(alpha=0.3)
    _save_close(fig, os.path.join(out_dir, 'cumulative_lift.png'))


# ── callable API ───────────────────────────────────────────────────────────────

def generate_run(
    inventory_db  : str,
    name          : str,
    out_dir       : str          = _DEFAULT_OUT_DIR,
    max_per_group : int | None   = None,
    min_lift      : float        = 1.5,
    max_lift      : float        = 5.0,
    seed          : int          = 0,
    batch_size    : int          = 500_000,
    verbose       : bool         = True,
) -> str:
    """Generate one affinity run and return the path to the created run_dir.

    Parameters
    ----------
    inventory_db  : path to an inventory.db produced by generate_inventory.py
    name          : folder name under out_dir
    out_dir       : parent directory; run is created at out_dir/name/
    max_per_group : SKU cap per group (None = no cap, full matrix)
    min_lift      : minimum lift value
    max_lift      : maximum lift value
    seed          : reproducibility seed for lift sampling
    batch_size    : DB insert batch size (rows per commit)
    verbose       : print progress lines
    """
    def _log(msg):
        if verbose:
            print(msg)

    inv_db = os.path.abspath(inventory_db)
    if not os.path.exists(inv_db):
        raise FileNotFoundError(f'inventory DB not found: {inv_db}')

    conn_inv = sqlite3.connect(inv_db)
    rows     = conn_inv.execute('SELECT sku, handling, category FROM cartons ORDER BY sku').fetchall()
    conn_inv.close()

    group_skus: dict[str, list[int]] = {}
    for sku, handling, category in rows:
        key = f'{handling}/{category}'
        group_skus.setdefault(key, []).append(sku)

    group_counts = {k: len(v) for k, v in group_skus.items()}
    _log(f'[affinity:{name}] {len(rows):,} SKUs  {len(group_skus)} groups  '
         f'max_per_group={max_per_group or "none"}')

    est = estimate(group_counts, max_per_group)
    gb  = est['estimated_db_gb']
    size_str = f'{gb:.2f} GB' if gb >= 1.0 else f'{est["estimated_db_mb"]:.1f} MB'
    _log(f'[affinity:{name}] Estimated: {est["total_unique_pairs"]:,} pairs  ~{size_str}')
    if gb > 5:
        _log(f'[affinity:{name}] WARNING: large matrix ({gb:.1f} GB).')

    run_dir  = os.path.join(out_dir, name)
    plot_dir = os.path.join(run_dir, 'plots')
    os.makedirs(plot_dir, exist_ok=True)

    params = {
        'name'               : name,
        'timestamp'          : datetime.now().strftime('%Y%m%d_%H%M%S'),
        'seed'               : seed,
        'source_inventory_db': inv_db,
        'max_per_group'      : max_per_group,
        'min_lift'           : min_lift,
        'max_lift'           : max_lift,
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
        group_skus    = group_skus,
        max_per_group = max_per_group,
        min_lift      = min_lift,
        max_lift      = max_lift,
        rng           = rng,
        conn          = conn_aff,
        batch_size    = batch_size,
    )
    elapsed    = time.perf_counter() - t0
    total_rows = conn_aff.execute('SELECT COUNT(*) FROM affinity').fetchone()[0]
    _log(f'[affinity:{name}] Done  {total_rows:,} rows  '
         f'({elapsed:.1f}s  {total_rows/max(elapsed,0.001):.0f} rows/s)')

    stats = compute_stats(conn_aff, group_stats, params)
    with open(os.path.join(run_dir, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=2)

    plot_lift_histogram(conn_aff, plot_dir, min_lift, max_lift)
    plot_lift_kde(conn_aff, group_skus, plot_dir)
    plot_lift_by_group(group_stats, plot_dir)
    plot_pairs_per_group(group_stats, plot_dir)
    plot_cumulative_lift(conn_aff, plot_dir)
    conn_aff.close()
    _log(f'[affinity:{name}] Done.')

    return run_dir


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate and persist a full affinity matrix to a SQLite database.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--inventory-db', required=True,
                        help='Path to the inventory.db produced by generate_inventory.py')
    parser.add_argument('--max-per-group', type=int, default=None,
                        help='Cap eligible SKUs per group. Default: no cap.')
    parser.add_argument('--min-lift', type=float, default=1.5)
    parser.add_argument('--max-lift', type=float, default=5.0)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--batch-size', type=int, default=500_000)
    parser.add_argument('--name', default=None)
    parser.add_argument('--out-dir', default=_DEFAULT_OUT_DIR)
    parser.add_argument('--estimate', action='store_true',
                        help='Print expected pair count and DB size, then exit')
    args = parser.parse_args()

    inv_db = os.path.abspath(args.inventory_db)
    if not os.path.exists(inv_db):
        sys.exit(f'inventory DB not found: {inv_db}')

    if args.estimate:
        conn_inv     = sqlite3.connect(inv_db)
        rows         = conn_inv.execute('SELECT handling, category FROM cartons').fetchall()
        conn_inv.close()
        group_counts: dict[str, int] = {}
        for h, c in rows:
            group_counts[f'{h}/{c}'] = group_counts.get(f'{h}/{c}', 0) + 1
        print_estimate(estimate(group_counts, args.max_per_group))
        return

    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = args.name or (
        f'aff_{ts}' if args.max_per_group is None else f'aff_cap{args.max_per_group}_{ts}'
    )
    generate_run(
        inventory_db  = inv_db,
        name          = name,
        out_dir       = args.out_dir,
        max_per_group = args.max_per_group,
        min_lift      = args.min_lift,
        max_lift      = args.max_lift,
        seed          = args.seed,
        batch_size    = args.batch_size,
    )


if __name__ == '__main__':
    main()
