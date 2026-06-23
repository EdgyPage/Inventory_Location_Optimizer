"""
Generate ONE realistic mixed inventory (per-category SKU families with multimodal,
per-dimension distributions) + its affinity matrix, written in the leaf layout that
run_simulation's discover_db_pairs consumes.

Families = the 6 categories, each with its own multimodal length/width/height/weight
specs and a per-family conveyable/non-conveyable propensity (aggregate ~0.75/0.25).
Dimensions are sampled INDEPENDENTLY per axis; every stored number is a grounded
integer >= 1, capped (enforced in Carton.build).  Initial stock is the equilibrium
point loaded by the sim; reorder is the JIT "day-before-runout" rule
    reorder_point = ceil(expected * (lead_time + 1))
with lead_time a deterministic per-dataset knob (e.g. 0 vs 1 -> two datasets).

Output layout
-------------
<PROFILE_INPUT_DIR>/<run_name>/<profile_name>/
    inventory/  inventory.db, params.json, stats.json, plots/
    affinity/   affinity.db,  params.json, stats.json, plots/

Usage
-----
python generate_mixed_profile.py --estimate
python generate_mixed_profile.py --num-skus 3000 --lead-time 1 --name smoke
python generate_mixed_profile.py --num-skus 76500 --lead-time 0   # JIT, immediate
python generate_mixed_profile.py --num-skus 76500 --lead-time 1   # JIT, 1-batch transit
"""

import matplotlib
matplotlib.use('Agg')

import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE      = os.path.dirname(os.path.abspath(__file__))
_WH        = os.path.dirname(_HERE)             # Warehouse/
_REPO_ROOT = os.path.dirname(_WH)               # repo root
sys.path.insert(0, _WH)


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

from generation.generate_inventory import (
    generate_run as _inv_run, Family, DEFAULT_FREQ_SPEC, DEFAULT_QTY_SPEC,
)
from generation.generate_affinity import generate_run as _aff_run


def _clean_path(val: str) -> str:
    if val.startswith(('r"', "r'")):
        return val[2:].rstrip('"').rstrip("'")
    return val.strip('"').strip("'")


_DEFAULT_PROFILES_DIR = _clean_path(os.getenv(
    'PROFILE_INPUT_DIR',
    os.path.join(_WH, 'generated', 'profiles'),
))


# ── the realistic creation plan ────────────────────────────────────────────────
# Shares + handling_split tuned so the aggregate conveyable fraction lands ~0.75.
# length/width/height share one spec per family (independent draws); tune per-axis freely.

def _tri(low, high, mode):   return {'dist': 'triangular', 'low': low, 'high': high, 'mode': mode}
def _norm(mean, std):        return {'dist': 'normal', 'mean': mean, 'std': std}
def _mix(*pairs):            return {'dist': 'mixture',
                                     'components': [{'prob': p, 'spec': s} for p, s in pairs]}

CREATION_PLAN = [
    # food — small/medium boxes, volume-correlated weight, mostly conveyable, fast movers
    Family('food', share=0.25, handling_split=(0.91, 0.09),
           length_spec=_tri(4, 24, 12), width_spec=_tri(4, 24, 12), height_spec=_tri(4, 20, 10),
           weight_spec={'dist': 'volume_poisson'},
           freq_spec={'dist': 'uniform', 'low': 0.3, 'high': 1.0},
           qty_spec={'dist': 'uniform', 'low': 1, 'high': 20}),

    # clothing — medium soft boxes, light, highly conveyable
    Family('clothing', share=0.18, handling_split=(0.97, 0.03),
           length_spec=_tri(8, 30, 18), width_spec=_tri(8, 30, 18), height_spec=_tri(6, 24, 14),
           weight_spec={'dist': 'volume_scaled_poisson', 'scale': 0.5}),

    # electronic — bimodal: small dense accessories + large units; dense weight; conveyable
    Family('electronic', share=0.22, handling_split=(0.93, 0.07),
           length_spec=_mix((0.6, _norm(8, 2)), (0.4, _norm(34, 5))),
           width_spec =_mix((0.6, _norm(8, 2)), (0.4, _norm(30, 5))),
           height_spec=_mix((0.6, _norm(6, 2)), (0.4, _norm(24, 5))),
           weight_spec={'dist': 'volume_scaled_poisson', 'scale': 1.6}),

    # seasonal — broad/bimodal sizes, medium weight, mixed handling
    Family('seasonal', share=0.12, handling_split=(0.60, 0.40),
           length_spec=_mix((0.5, _tri(6, 20, 12)), (0.5, _tri(28, 46, 38))),
           width_spec =_mix((0.5, _tri(6, 20, 12)), (0.5, _tri(28, 46, 38))),
           height_spec=_mix((0.5, _tri(6, 18, 11)), (0.5, _tri(26, 44, 36))),
           weight_spec={'dist': 'volume_poisson'}),

    # furniture — large + heavy, non-conveyable-leaning, slow movers
    Family('furniture', share=0.13, handling_split=(0.20, 0.80),
           length_spec=_tri(24, 48, 42), width_spec=_tri(24, 48, 42), height_spec=_tri(20, 48, 38),
           weight_spec={'dist': 'volume_scaled_poisson', 'scale': 2.0},
           freq_spec={'dist': 'uniform', 'low': 0.0, 'high': 0.35},
           qty_spec={'dist': 'uniform', 'low': 1, 'high': 4}),

    # chemical — medium dense drums, heavy, non-conveyable-leaning
    Family('chemical', share=0.10, handling_split=(0.40, 0.60),
           length_spec=_norm(20, 4), width_spec=_norm(20, 4), height_spec=_norm(24, 5),
           weight_spec={'dist': 'poisson_fixed', 'lam': 60.0}),
]


def _expected_conveyable_fraction(plan) -> float:
    tot = sum(f.share for f in plan)
    return sum(f.share * f.handling_split[0] for f in plan) / tot


# ── driver ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate one realistic mixed inventory + affinity profile.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--num-skus', type=int, default=76_500)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--name', default=None, help='run folder name (default: mixed_<ts>)')
    parser.add_argument('--lead-times', type=float, nargs='+', default=[1.0], metavar='L',
                        help='one or more deterministic lead times in batches; each produces a '
                             'sibling profile leaf under the same timestamped run folder '
                             '(e.g. --lead-times 0 1 -> mixed_<ts>/{lt0,lt1}/)')
    parser.add_argument('--coverage', type=float, default=10.0,
                        help='equilibrium coverage batches (initial loaded stock = coverage * expected)')
    parser.add_argument('--supply-cv-max', type=float, default=0.15,
                        help='per-SKU supply_cv ~ Uniform(0, this); drives reorder-quantity variation')
    parser.add_argument('--top-k', type=int, default=20)
    parser.add_argument('--candidate-k', type=int, default=60)
    parser.add_argument('--affinity-min-lift', type=float, default=1.0)
    parser.add_argument('--affinity-max-lift', type=float, default=5.0)
    parser.add_argument('--affinity-seed', type=int, default=0)
    parser.add_argument('--freq-spec', default=None,
                        help='JSON demand-frequency override applied to ALL families')
    parser.add_argument('--qty-spec', default=None,
                        help='JSON demand-quantity override applied to ALL families')
    parser.add_argument('--out-dir', default=_DEFAULT_PROFILES_DIR)
    parser.add_argument('--skip-affinity', action='store_true')
    parser.add_argument('--estimate', action='store_true',
                        help='print the plan + expected conveyable fraction and affinity size, then exit')
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-utf-8 consoles (e.g. cp1252 → arrows)
    except Exception:
        pass
    out_dir = _clean_path(args.out_dir)

    demand_override = None
    if args.freq_spec or args.qty_spec:
        demand_override = (
            json.loads(args.freq_spec) if args.freq_spec else dict(DEFAULT_FREQ_SPEC),
            json.loads(args.qty_spec)  if args.qty_spec  else dict(DEFAULT_QTY_SPEC),
        )

    if args.estimate:
        print(f'\n  Creation plan: {len(CREATION_PLAN)} families  num_skus={args.num_skus:,}')
        print(f'  {"category":<12}{"share":>7}{"conv":>7}{"nonconv":>9}')
        for f in CREATION_PLAN:
            print(f'  {f.category:<12}{f.share:>7.2f}{f.handling_split[0]:>7.2f}{f.handling_split[1]:>9.2f}')
        print(f'\n  expected conveyable fraction = {_expected_conveyable_fraction(CREATION_PLAN):.3f}  (target ~0.75)')
        pairs = args.num_skus * args.top_k
        print(f'  affinity estimate: ~{pairs:,} pairs  ~{pairs * 2 * 28 / 1_048_576:.0f} MB  (top-{args.top_k})\n')
        return

    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_name = args.name or f'mixed_{ts}'   # one timestamped run folder; one leaf per lead time
    run_dir  = os.path.join(out_dir, run_name)

    print(f'\n{"="*64}')
    print(f'  Mixed run     : {run_name}')
    print(f'  Dir           : {run_dir}')
    print(f'  num_skus={args.num_skus:,}  seed={args.seed}  coverage={args.coverage}')
    print(f'  lead_times    : {args.lead_times}  ->  ' +
          ', '.join(f'lt{lt:g}' for lt in args.lead_times))
    print(f'  expected conveyable fraction ~ {_expected_conveyable_fraction(CREATION_PLAN):.3f}')
    print(f'{"="*64}\n')

    for lt in args.lead_times:
        prof_name = f'mixed_realistic_lt{lt:g}'
        leaf      = os.path.join(run_dir, prof_name)
        os.makedirs(leaf, exist_ok=True)
        print(f'\n[{prof_name}] lead_time={lt}')

        t0      = time.perf_counter()
        inv_run = _inv_run(
            name                         = 'inventory',
            num_skus                     = args.num_skus,
            seed                         = args.seed,           # same seed → datasets differ only by lead time
            out_dir                      = leaf,
            creation_plan                = CREATION_PLAN,
            lead_time                    = lt,
            equilibrium_coverage_batches = args.coverage,
            supply_cv_max                = args.supply_cv_max,
            demand_override              = demand_override,
        )
        inv_db = os.path.join(inv_run, 'inventory.db')
        print(f'  inventory done in {time.perf_counter()-t0:.1f}s → {inv_db}')

        if not args.skip_affinity:
            t0 = time.perf_counter()
            _aff_run(
                inventory_db = inv_db,
                name         = 'affinity',
                out_dir      = leaf,
                top_k        = args.top_k,
                candidate_k  = args.candidate_k,
                min_lift     = args.affinity_min_lift,
                max_lift     = args.affinity_max_lift,
                seed         = args.affinity_seed,
            )
            print(f'  affinity done in {time.perf_counter()-t0:.1f}s')

    print(f'\n[mixed] Done → {run_dir}  ({len(args.lead_times)} lead-time profile(s))\n')


if __name__ == '__main__':
    main()
