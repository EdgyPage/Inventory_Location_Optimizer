"""
Run generate_inventory + generate_affinity for a suite of carton profiles.

Each profile varies the dimension and / or weight distribution to produce
inventories with meaningfully different physical characteristics — from
tight unimodal clusters to bimodal, trimodal, and heavy-tailed mixtures.
The goal is to build a library of inventory × affinity pairs that capture
diverse warehouse scenarios without regenerating them at simulation runtime.

Output layout
-------------
<PROFILE_INPUT_DIR>/<profile_name>/
    profile_manifest.json   written at start with all profile specs
    profile_summary.json    written at end with timing and stats
    <carton_profile_name>/
        inventory/          → inventory.db, params.json, stats.json, plots/
        affinity/           → affinity.db,  params.json, stats.json, plots/
    cross_profile/
        dim_length_overlay.png      length KDE across all profiles
        dim_volume_overlay.png      volume KDE across all profiles
        weight_overlay.png          weight KDE across all profiles
        profile_stats_table.png     summary table image

Usage
-----
# dry run — print profile list and estimated affinity sizes, then exit
python generate_profile_suite.py --estimate

# full overnight run
python generate_profile_suite.py

# run only selected profiles
python generate_profile_suite.py --profiles default bimodal_size heavy_tail_weight

Options
-------
--name NAME             profile run folder name (default: profile_<timestamp>)
--num-skus N            SKU count for all inventories (default: 76500)
--seed INT              base seed; profile i uses seed + i (default: 42)
--max-per-group INT     affinity SKU cap per group (default: no cap)
--skip-affinity         generate inventories only
--profiles NAME [...]   subset of profiles to run (default: all)
--out-dir PATH          root output directory (overrides PROFILE_INPUT_DIR env var)
--affinity-seed INT     base seed for affinity generation (default: 0)
--affinity-min-lift F   (default: 1.5)
--affinity-max-lift F   (default: 5.0)
--estimate              print profile specs and size estimates, then exit
"""

import matplotlib
matplotlib.use('Agg')

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)


def _load_env(path: str) -> None:
    """Inject KEY=VALUE pairs from *path* into os.environ (shell vars take priority)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _, _val = _line.partition('=')
            _key = _key.strip();  _val = _val.strip()
            if _val.startswith(('r"', "r'")):
                _val = _val[2:].rstrip('"').rstrip("'")
            else:
                _val = _val.strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


_load_env(os.path.join(_REPO_ROOT, '.env'))

from generate_inventory import generate_run as _inv_run, _DEFAULT_OUT_DIR as _INV_DEFAULT
from generate_affinity  import generate_run as _aff_run, estimate as _aff_estimate, \
                               print_estimate as _print_estimate, _DEFAULT_OUT_DIR as _AFF_DEFAULT

_DEFAULT_PROFILES_DIR = os.getenv(
    'PROFILE_INPUT_DIR',
    os.path.join(_HERE, 'generated', 'profiles'),
)


# ── carton profiles ────────────────────────────────────────────────────────────
#
# dim_spec controls how each of length / width / height is sampled.
# weight_spec controls how weight is sampled (optionally volume-correlated).
#
# Supported dists: triangular, uniform, normal, beta, mixture  (dim)
#                  volume_poisson, volume_scaled_poisson,
#                  poisson_fixed, uniform, normal, mixture     (weight)

CARTON_PROFILES = [
    {
        'name': 'default',
        'description': 'Triangular dims (mode=max), Poisson weight ~ volume^(1/3). '
                       'Reproduces the original Inventory_Builder behaviour.',
        'dim_spec'   : {'dist': 'triangular', 'low': 3, 'high': 48, 'mode': 48},
        'weight_spec': {'dist': 'volume_poisson'},
    },
    {
        'name': 'uniform_sizes',
        'description': 'Uniform dim distribution — equal probability across [3, 48]. '
                       'Volume-correlated weight.',
        'dim_spec'   : {'dist': 'uniform', 'low': 3, 'high': 48},
        'weight_spec': {'dist': 'volume_poisson'},
    },
    {
        'name': 'small_dense',
        'description': 'Small cartons (triangular → small), high fixed weight. '
                       'Models dense electronics or hardware.',
        'dim_spec'   : {'dist': 'triangular', 'low': 3, 'high': 18, 'mode': 12},
        'weight_spec': {'dist': 'uniform', 'low': 20, 'high': 70},
    },
    {
        'name': 'large_light',
        'description': 'Large cartons (triangular → large end), very light weight. '
                       'Models foam packaging, pillows, or flat-pack furniture.',
        'dim_spec'   : {'dist': 'triangular', 'low': 24, 'high': 48, 'mode': 44},
        'weight_spec': {'dist': 'poisson_fixed', 'lam': 3.0},
    },
    {
        'name': 'bimodal_size',
        'description': '50 % small singleton-range + 50 % large pallet-range. '
                       'Produces a clear size gap in the distribution.',
        'dim_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.50, 'spec': {'dist': 'triangular', 'low': 3,  'high': 14, 'mode': 10}},
                {'prob': 0.50, 'spec': {'dist': 'triangular', 'low': 26, 'high': 48, 'mode': 42}},
            ],
        },
        'weight_spec': {'dist': 'volume_poisson'},
    },
    {
        'name': 'bimodal_size_decoupled_weight',
        'description': 'Same bimodal size split as above, but weight is drawn from a '
                       'mixture independent of volume — light-heavy mix cuts across sizes.',
        'dim_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.50, 'spec': {'dist': 'triangular', 'low': 3,  'high': 14, 'mode': 10}},
                {'prob': 0.50, 'spec': {'dist': 'triangular', 'low': 26, 'high': 48, 'mode': 42}},
            ],
        },
        'weight_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.60, 'spec': {'dist': 'poisson_fixed', 'lam': 4.0}},
                {'prob': 0.40, 'spec': {'dist': 'poisson_fixed', 'lam': 45.0}},
            ],
        },
    },
    {
        'name': 'heavy_tail_weight',
        'description': '80 % items are light (Poisson lam=5); 20 % are very heavy '
                       '(Poisson lam=90). Produces a strong fat tail.',
        'dim_spec'   : {'dist': 'triangular', 'low': 3, 'high': 48, 'mode': 48},
        'weight_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.80, 'spec': {'dist': 'poisson_fixed', 'lam': 5.0}},
                {'prob': 0.20, 'spec': {'dist': 'poisson_fixed', 'lam': 90.0}},
            ],
        },
    },
    {
        'name': 'clustered_medium',
        'description': 'Normal dims tightly centred at medium size (mean=24, std=4). '
                       'Weight scale boosted to 1.5× volume correlation.',
        'dim_spec'   : {'dist': 'normal', 'mean': 24, 'std': 4},
        'weight_spec': {'dist': 'volume_scaled_poisson', 'scale': 1.5},
    },
    {
        'name': 'trimodal_spiky',
        'description': 'Three distinct size clusters (small 30 %, medium 40 %, large 30 %) '
                       'combined with a trimodal weight mixture. Both dimensions and weight '
                       'have spiky distributions.',
        'dim_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.30, 'spec': {'dist': 'normal', 'mean': 7,  'std': 2}},
                {'prob': 0.40, 'spec': {'dist': 'normal', 'mean': 24, 'std': 3}},
                {'prob': 0.30, 'spec': {'dist': 'normal', 'mean': 42, 'std': 3}},
            ],
        },
        'weight_spec': {
            'dist': 'mixture',
            'components': [
                {'prob': 0.40, 'spec': {'dist': 'poisson_fixed', 'lam': 3.0}},
                {'prob': 0.35, 'spec': {'dist': 'poisson_fixed', 'lam': 28.0}},
                {'prob': 0.25, 'spec': {'dist': 'poisson_fixed', 'lam': 80.0}},
            ],
        },
    },
    {
        'name': 'beta_right_skewed',
        'description': 'Beta(2, 8) dims scaled to [3, 48] → right-skewed toward small. '
                       'Most items are small; a long tail reaches large sizes.',
        'dim_spec'   : {'dist': 'beta', 'alpha': 2.0, 'beta': 8.0, 'low': 3, 'high': 48},
        'weight_spec': {'dist': 'normal', 'mean': 14, 'std': 6},
    },
    {
        'name': 'beta_left_skewed',
        'description': 'Beta(8, 2) dims scaled to [3, 48] → left-skewed toward large. '
                       'Most items are large; a tail reaches small sizes.',
        'dim_spec'   : {'dist': 'beta', 'alpha': 8.0, 'beta': 2.0, 'low': 3, 'high': 48},
        'weight_spec': {'dist': 'volume_poisson'},
    },
    {
        'name': 'uniform_weight',
        'description': 'Triangular dims (default), but weight is uniform [1, 120] — '
                       'completely decoupled from size.',
        'dim_spec'   : {'dist': 'triangular', 'low': 3, 'high': 48, 'mode': 48},
        'weight_spec': {'dist': 'uniform', 'low': 1, 'high': 120},
    },
]

PROFILE_MAP = {p['name']: p for p in CARTON_PROFILES}


# ── cross-profile overlay plots ────────────────────────────────────────────────

def _save_close(fig, path: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_cross_profile_overlay(
    profile_dfs : dict[str, pd.DataFrame],
    out_dir     : str,
) -> None:
    """One figure per dimension + weight showing KDE for every profile overlaid."""
    names  = list(profile_dfs.keys())
    colors = plt.cm.tab20(np.linspace(0, 1, len(names)))

    for col, xlabel, fname in [
        ('length', 'Length',             'dim_length_overlay.png'),
        ('width',  'Width',              'dim_width_overlay.png'),
        ('height', 'Height',             'dim_height_overlay.png'),
        ('volume', 'Volume (L × W × H)', 'dim_volume_overlay.png'),
        ('weight', 'Weight',             'weight_overlay.png'),
    ]:
        fig, ax = plt.subplots(figsize=(12, 5))
        for name, color in zip(names, colors):
            df   = profile_dfs[name]
            vals = np.asarray(df[col], dtype=float)
            if vals.max() <= vals.min():
                continue
            kde = gaussian_kde(np.random.choice(vals, size=min(len(vals), 20_000), replace=False),
                               bw_method='silverman')
            xs  = np.linspace(vals.min(), vals.max(), 400)
            ax.plot(xs, kde(xs), lw=1.8, color=color, label=name, alpha=0.85)
        ax.set_xlabel(xlabel, fontsize=10)
        ax.set_ylabel('Density', fontsize=10)
        ax.set_title(f'{xlabel} distribution — all profiles', fontsize=12, fontweight='bold')
        ax.legend(fontsize=7, ncol=2, loc='upper left')
        ax.grid(alpha=0.3)
        _save_close(fig, os.path.join(out_dir, fname))


def plot_profile_stats_table(
    profile_stats : dict[str, dict],
    out_dir       : str,
) -> None:
    """Render a summary table as an image for quick comparison."""
    rows = []
    for name, s in profile_stats.items():
        rows.append({
            'Profile'       : name,
            'Length mean'   : f"{s['dimensions']['length']['mean']:.1f}",
            'Length std'    : f"{s['dimensions']['length']['std']:.1f}",
            'Volume mean'   : f"{s['dimensions']['volume']['mean']:.0f}",
            'Weight mean'   : f"{s['weight']['mean']:.1f}",
            'Weight std'    : f"{s['weight']['std']:.1f}",
            'Weight p95'    : f"{s['weight']['p95']:.0f}",
            'Singleton frac': f"{s['singleton_fraction']:.1%}",
        })
    df = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(max(14, len(df.columns) * 1.7), 0.5 * len(df) + 1.5))
    ax.axis('off')
    tbl = ax.table(
        cellText  = df.values,
        colLabels = df.columns,
        cellLoc   = 'center',
        loc       = 'center',
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.auto_set_column_width(list(range(len(df.columns))))
    for (r, c), cell in tbl.get_celld().items():
        if r == 0:
            cell.set_facecolor('#2e75b6')
            cell.set_text_props(color='white', fontweight='bold')
        elif r % 2 == 0:
            cell.set_facecolor('#dce6f1')
    ax.set_title('Cross-profile inventory statistics', fontsize=12, fontweight='bold', pad=12)
    plt.tight_layout()
    _save_close(fig, os.path.join(out_dir, 'profile_stats_table.png'))


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate inventory + affinity for a suite of carton profiles.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--name', default=None,
                        help='Profile run folder name (default: profile_<timestamp>)')
    parser.add_argument('--num-skus', type=int, default=76_500,
                        help='SKU count applied to all profiles')
    parser.add_argument('--seed', type=int, default=42,
                        help='Base inventory seed; profile i uses seed + i')
    parser.add_argument('--top-k', type=int, default=20,
                        help='Partners stored per SKU in affinity matrix (default: 20)')
    parser.add_argument('--candidate-k', type=int, default=60,
                        help='Candidate pool per SKU before noise reranking (default: 60)')
    parser.add_argument('--skip-affinity', action='store_true',
                        help='Generate inventories only, skip affinity generation')
    parser.add_argument('--profiles', nargs='+', default=None,
                        metavar='NAME',
                        help='Subset of profiles to run (default: all). '
                             f'Available: {", ".join(PROFILE_MAP)}')
    parser.add_argument('--out-dir', default=_DEFAULT_PROFILES_DIR,
                        help='Root directory for profile output (overrides PROFILE_INPUT_DIR)')
    parser.add_argument('--affinity-seed', type=int, default=0,
                        help='Base seed for lift value generation')
    parser.add_argument('--affinity-min-lift', type=float, default=1.0)
    parser.add_argument('--affinity-max-lift', type=float, default=5.0)
    parser.add_argument('--estimate', action='store_true',
                        help='Print profile specs and estimated affinity sizes, then exit')
    args = parser.parse_args()

    # resolve which profiles to run
    if args.profiles:
        unknown = [p for p in args.profiles if p not in PROFILE_MAP]
        if unknown:
            sys.exit(f'Unknown profile(s): {unknown}\n'
                     f'Available: {list(PROFILE_MAP)}')
        selected = [PROFILE_MAP[n] for n in args.profiles]
    else:
        selected = CARTON_PROFILES

    # ── estimate / dry-run ─────────────────────────────────────────────────────
    if args.estimate:
        print(f'\n{"="*60}')
        print(f'  {len(selected)} profile(s)  num_skus={args.num_skus:,}  top_k={args.top_k}')
        print(f'{"="*60}')
        for i, p in enumerate(selected):
            print(f'\n  [{i+1}/{len(selected)}] {p["name"]}')
            print(f'    {p["description"]}')
            print(f'    dim_spec    : {json.dumps(p["dim_spec"])}')
            print(f'    weight_spec : {json.dumps(p["weight_spec"])}')
        # top-K is linear: N × top_k pairs per profile (× 2 directions)
        pairs_per_profile = args.num_skus * args.top_k
        mb_per_profile    = pairs_per_profile * 2 * 28 / 1_048_576
        print(f'\n  Affinity estimate per profile: ~{pairs_per_profile:,} pairs  '
              f'~{mb_per_profile:.0f} MB  (top-{args.top_k} sparse)')
        print(f'  Total affinity estimate: ~{mb_per_profile * len(selected):.0f} MB  '
              f'({mb_per_profile * len(selected) / 1024:.1f} GB)\n')
        return

    # ── setup batch dir ────────────────────────────────────────────────────────
    ts           = datetime.now().strftime('%Y%m%d_%H%M%S')
    profile_name = args.name or f'profile_{ts}'
    profile_dir  = os.path.join(args.out_dir, profile_name)
    cross_dir    = os.path.join(profile_dir, 'cross_profile')
    os.makedirs(cross_dir, exist_ok=True)

    manifest = {
        'profile_name'  : profile_name,
        'timestamp'     : ts,
        'num_skus'      : args.num_skus,
        'base_seed'     : args.seed,
        'top_k'         : args.top_k,
        'candidate_k'   : args.candidate_k,
        'skip_affinity' : args.skip_affinity,
        'profiles'      : [
            {'name': p['name'], 'description': p['description'],
             'dim_spec': p['dim_spec'], 'weight_spec': p['weight_spec']}
            for p in selected
        ],
    }
    with open(os.path.join(profile_dir, 'profile_manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f'\n{"="*64}')
    print(f'  Profile run : {profile_name}')
    print(f'  Dir         : {profile_dir}')
    print(f'  {len(selected)} profile(s)  |  '
          f'num_skus={args.num_skus:,}  |  '
          f'affinity top-{args.top_k}')
    print(f'{"="*64}\n')

    # ── profile loop ───────────────────────────────────────────────────────────
    t_start    = time.perf_counter()
    summary    = {}
    profile_dbs: dict[str, str] = {}   # profile_name → inventory_db path

    for i, profile in enumerate(selected):
        pname    = profile['name']
        prof_dir = os.path.join(profile_dir, pname)
        inv_dir  = os.path.join(prof_dir, 'inventory')
        inv_seed = args.seed + i

        print(f'\n[{i+1}/{len(selected)}] {pname}')
        print(f'  {profile["description"]}')

        # inventory
        t0      = time.perf_counter()
        inv_run = _inv_run(
            name               = 'inventory',
            num_skus           = args.num_skus,
            seed               = inv_seed,
            quantity_spec      = None,   # uses DEFAULT_QUANTITY_SPEC (mode=35, mean=50)
            out_dir            = prof_dir,
            dim_spec           = profile['dim_spec'],
            weight_spec        = profile['weight_spec'],
        )
        inv_db      = os.path.join(inv_run, 'inventory.db')
        inv_elapsed = time.perf_counter() - t0
        profile_dbs[pname] = inv_db

        # read stats for summary
        with open(os.path.join(inv_run, 'stats.json')) as f:
            inv_stats = json.load(f)

        entry: dict = {
            'inventory_dir'   : inv_run,
            'inventory_db'    : inv_db,
            'inventory_seed'  : inv_seed,
            'inventory_elapsed_s': round(inv_elapsed, 2),
            'inv_stats'       : inv_stats,
        }

        # affinity
        if not args.skip_affinity:
            aff_seed = args.affinity_seed + i
            t0       = time.perf_counter()
            aff_run  = _aff_run(
                inventory_db  = inv_db,
                name          = 'affinity',
                out_dir       = prof_dir,
                top_k         = args.top_k,
                candidate_k   = args.candidate_k,
                min_lift      = args.affinity_min_lift,
                max_lift      = args.affinity_max_lift,
                seed          = aff_seed,
            )
            aff_elapsed = time.perf_counter() - t0
            with open(os.path.join(aff_run, 'stats.json')) as f:
                aff_stats = json.load(f)
            entry['affinity_dir']        = aff_run
            entry['affinity_db']         = os.path.join(aff_run, 'affinity.db')
            entry['affinity_seed']       = aff_seed
            entry['affinity_elapsed_s']  = round(aff_elapsed, 2)
            entry['aff_stats']           = aff_stats
            print(f'  affinity: {aff_stats["total_unique_pairs"]:,} pairs  '
                  f'{aff_elapsed:.0f}s')

        summary[pname] = entry

    total_elapsed = time.perf_counter() - t_start
    print(f'\n{"="*64}')
    print(f'  All {len(selected)} profile(s) done in {total_elapsed:.0f}s')
    print(f'{"="*64}\n')

    # ── cross-profile plots ────────────────────────────────────────────────────
    print('[profiles] Generating cross-profile overlay plots...')
    profile_dfs   = {}
    profile_stats = {}
    for pname, inv_db in profile_dbs.items():
        conn = sqlite3.connect(inv_db)
        df   = pd.read_sql_query('SELECT * FROM cartons', conn)
        conn.close()
        df['volume']         = df['length'] * df['width'] * df['height']
        df['is_singleton']   = (df[['length', 'width', 'height']].max(axis=1) <= 16).astype(int)
        profile_dfs[pname]   = df
        profile_stats[pname] = summary[pname]['inv_stats']

    plot_cross_profile_overlay(profile_dfs, cross_dir)
    plot_profile_stats_table(profile_stats, cross_dir)
    print(f'[profiles] Cross-profile plots → {cross_dir}')

    # ── profile summary ────────────────────────────────────────────────────────
    summary_out = {
        'profile_name'    : profile_name,
        'total_elapsed_s' : round(total_elapsed, 1),
        'profiles_run'    : len(selected),
        'profiles'        : {
            name: {
                'inventory_dir'      : e['inventory_dir'],
                'inventory_elapsed_s': e['inventory_elapsed_s'],
                'weight_mean'        : e['inv_stats']['weight']['mean'],
                'weight_std'         : e['inv_stats']['weight']['std'],
                'weight_p95'         : e['inv_stats']['weight']['p95'],
                'volume_mean'        : e['inv_stats']['dimensions']['volume']['mean'],
                'singleton_fraction' : e['inv_stats']['singleton_fraction'],
                **(
                    {
                        'affinity_dir'       : e.get('affinity_dir'),
                        'affinity_elapsed_s' : e.get('affinity_elapsed_s'),
                        'total_unique_pairs' : e.get('aff_stats', {}).get('total_unique_pairs'),
                    }
                    if not args.skip_affinity else {}
                ),
            }
            for name, e in summary.items()
        },
    }
    with open(os.path.join(profile_dir, 'profile_summary.json'), 'w') as f:
        json.dump(summary_out, f, indent=2)
    print(f'[profiles] Summary → {os.path.join(profile_dir, "profile_summary.json")}')
    print(f'[profiles] Done.')


if __name__ == '__main__':
    main()
