"""
Generate ONE realistic mixed inventory (per-category SKU families with multimodal,
per-dimension distributions) + its affinity matrix, written in the leaf layout that
run_simulation's discover_db_pairs consumes.

Families = the 6 categories, each with its own multimodal length/width/height/weight
specs and a per-family conveyable/non-conveyable propensity (aggregate ~0.75/0.25).
Dimensions are sampled INDEPENDENTLY per axis; every stored number is a grounded
integer >= 1, capped (enforced in Order.build).  Initial stock is the equilibrium
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
# repo root on sys.path so package imports resolve when run as a script.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


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

from dataclasses import replace

from Warehouse.generation.generate_inventory import (
    generate_run as _inv_run, Family, DEFAULT_FREQ_SPEC, DEFAULT_QTY_SPEC,
    fulfillment_families, DEFAULT_FF_WEIGHT_SPEC,
)
from Warehouse.generation.generate_affinity import generate_run as _aff_run


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


# ── bell (normal) frequency profile ────────────────────────────────────────────
# Opt-in alternative to the uniform freq specs above (select with --freq-profile bell).
# Store frequency is drawn per-category from a NORMAL centered by mover speed (furniture
# slowest, food fastest; chemical high per its consumable restock cadence); fulfillment
# SKUs draw from an equal-weight mixture of three normals (single category → nothing to key
# on).  Only freq_spec changes — BELL_CREATION_PLAN is CREATION_PLAN with dimensions/weight/
# handling/share untouched — so a bell run differs from the uniform baseline only in demand
# skew.  Tails past the (1e-6, 1.0] frequency clamp (Order.build) are absorbed, as the dim specs
# already rely on their own clamps.
BELL_STORE_FREQ = {
    'food':       _norm(0.80, 0.10),
    'chemical':   _norm(0.50, 0.10),
    'clothing':   _norm(0.35, 0.05),
    'electronic': _norm(0.35, 0.05),
    'seasonal':   _norm(0.15, 0.05),
    'furniture':  _norm(0.05, 0.015),
}
BELL_FF_FREQ = _mix((1 / 3, _norm(0.15, 0.05)),
                    (1 / 3, _norm(0.25, 0.05)),
                    (1 / 3, _norm(0.55, 0.10)))
BELL_CREATION_PLAN = [replace(fam, freq_spec=dict(BELL_STORE_FREQ[fam.category]))
                      for fam in CREATION_PLAN]


def _expected_conveyable_fraction(plan) -> float:
    tot = sum(f.share for f in plan)
    return sum(f.share * f.handling_split[0] for f in plan) / tot


def _parse_lead_spec(spec, rand_range, freq_profile='uniform') -> tuple:
    """A --lead-times token → (profile_name, lead_time, lead_time_range).
    'random' → per-SKU lead ~ randint(rand_range); a number → fixed per-SKU lead.
    A non-uniform freq_profile (e.g. 'bell') is tagged into the name so its run leaf stays
    distinct from the uniform baseline (mixed_realistic_bell_lt1 vs mixed_realistic_lt1);
    'uniform' keeps the historical names unchanged."""
    tag = '' if freq_profile == 'uniform' else f'{freq_profile}_'
    if str(spec).strip().lower() == 'random':
        lo, hi = int(rand_range[0]), int(rand_range[1])
        return (f'mixed_realistic_{tag}ltrand{lo}-{hi}', 0.0, (lo, hi))
    val = float(spec)
    return (f'mixed_realistic_{tag}lt{val:g}', val, None)


def _ff_weight_spec(args) -> dict:
    """Fulfillment weight spec from --ff-weight-spec JSON, else triangular(min, mode, max)."""
    if args.ff_weight_spec:
        return json.loads(args.ff_weight_spec)
    return {'dist': 'triangular', 'low': args.ff_weight_min,
            'mode': args.ff_weight_mode, 'high': args.ff_weight_max}


def _build_plan(args) -> list:
    """The 6 store families, plus a fulfillment sub-catalog when --fulfillment-fraction > 0.
    Store shares scale to (1-F) and the fulfillment families sum to F, so shares stay relative.
    --freq-profile bell swaps the per-category freq specs for bell (normal) ones and gives
    fulfillment SKUs an equal-weight 3-normal mixture; 'uniform' keeps the baseline plan."""
    bell    = args.freq_profile == 'bell'
    base    = BELL_CREATION_PLAN if bell else CREATION_PLAN
    ff_freq = BELL_FF_FREQ if bell else None
    f = args.fulfillment_fraction
    if f <= 0.0:
        return list(base)
    store = [replace(fam, share=fam.share * (1.0 - f)) for fam in base]
    lo, hi = args.ff_dim_range
    ff = fulfillment_families(
        total_share=f, cube_fraction=args.ff_cube_fraction, weight_spec=_ff_weight_spec(args),
        dim_low=lo, dim_high=hi, cube_sizes=tuple(args.ff_cube_sizes), freq_spec=ff_freq,
    )
    return store + ff


# ── driver ───────────────────────────────────────────────────────────────────

def main() -> None:
    # FIRST statement in main, before the parser exists: `--help` is printed and exited from
    # INSIDE parse_args, so a reconfigure that sits after it never runs on the one path that
    # needs it most.  U+2192 (in --ff-cube-fraction's help) has no cp1252 mapping, so `--help`
    # on a legacy console died with UnicodeEncodeError instead of printing usage.
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-utf-8 consoles (e.g. cp1252 → arrows)
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description='Generate one realistic mixed inventory + affinity profile.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--num-skus', type=int, default=76_500)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--name', default=None, help='run folder name (default: mixed_<ts>)')
    parser.add_argument('--lead-times', nargs='+', default=['1'], metavar='L',
                        help='one or more lead-time specs; each produces a sibling profile leaf '
                             'under the same timestamped run folder.  A spec is a number (fixed '
                             'per-SKU lead in batches) OR the word "random" (per-SKU lead ~ '
                             'randint(--lead-random-range)).  e.g. --lead-times 0 random -> '
                             'mixed_<ts>/{lt0, ltrand0-5}/ — identical inventories, differing only '
                             'in lead/reorder.')
    parser.add_argument('--lead-random-range', type=int, nargs=2, default=[0, 5], metavar=('LO', 'HI'),
                        help='inclusive per-SKU lead range for the "random" lead-times spec')
    parser.add_argument('--coverage', type=float, default=10.0,
                        help='equilibrium coverage batches (initial loaded stock = coverage * expected)')
    parser.add_argument('--supply-cv-max', type=float, default=0.15,
                        help='per-SKU supply_cv ~ Uniform(0, this); drives reorder-quantity variation')
    parser.add_argument('--top-k', type=int, default=20)
    parser.add_argument('--candidate-k', type=int, default=60)
    parser.add_argument('--affinity-min-lift', type=float, default=1.0)
    parser.add_argument('--affinity-max-lift', type=float, default=5.0)
    parser.add_argument('--affinity-seed', type=int, default=0)
    parser.add_argument('--freq-profile', choices=['uniform', 'bell'], default='uniform',
                        help="relative-frequency profile: 'uniform' (baseline per-category specs) "
                             "or 'bell' (per-category normals for store + a 3-normal mixture for "
                             'fulfillment). --freq-spec, if given, overrides this for every family.')
    parser.add_argument('--freq-spec', default=None,
                        help='JSON relative-frequency override applied to ALL families')
    parser.add_argument('--qty-spec', default=None,
                        help='JSON demand-quantity override applied to ALL families')
    # ── fulfillment sub-catalog (a fraction of --num-skus; store families scale down to the rest) ──
    parser.add_argument('--fulfillment-fraction', type=float, default=0.0, metavar='F',
                        help='fraction of num-skus that are fulfillment SKUs (0 = none). The 6 store '
                             'families scale to 1-F; fulfillment SKUs are small forward-pick items '
                             'routed to fulfillment bins.')
    parser.add_argument('--ff-cube-fraction', type=float, default=0.3, metavar='C',
                        help='cube share of the fulfillment SKUs (default 0.3 → rectangles preferred)')
    parser.add_argument('--ff-weight-min', type=float, default=1.0)
    parser.add_argument('--ff-weight-mode', type=float, default=2.5)
    parser.add_argument('--ff-weight-max', type=float, default=10.0,
                        help='fulfillment weight is triangular(min, mode, max) — right-skewed, light')
    parser.add_argument('--ff-weight-spec', default=None,
                        help='JSON fulfillment weight spec; overrides --ff-weight-min/mode/max')
    parser.add_argument('--ff-dim-range', type=int, nargs=2, default=[3, 16], metavar=('LO', 'HI'),
                        help='rectangular fulfillment dimension range (HI capped at 16 to fit FF bins)')
    parser.add_argument('--ff-cube-sizes', type=int, nargs='+', default=[4, 6, 8], metavar='S',
                        help='cube edge sizes for cube fulfillment SKUs (L=W=H drawn from these)')
    parser.add_argument('--out-dir', default=_DEFAULT_PROFILES_DIR)
    parser.add_argument('--skip-affinity', action='store_true')
    parser.add_argument('--estimate', action='store_true',
                        help='print the plan + expected conveyable fraction and affinity size, then exit')
    args = parser.parse_args()
    out_dir = _clean_path(args.out_dir)

    demand_override = None
    if args.freq_spec or args.qty_spec:
        demand_override = (
            json.loads(args.freq_spec) if args.freq_spec else dict(DEFAULT_FREQ_SPEC),
            json.loads(args.qty_spec)  if args.qty_spec  else dict(DEFAULT_QTY_SPEC),
        )

    plan = _build_plan(args)

    if args.estimate:
        print(f'\n  Creation plan: {len(plan)} families  num_skus={args.num_skus:,}  '
              f'freq profile={args.freq_profile}')
        print(f'  {"category":<12}{"share":>7}{"conv":>7}{"nonconv":>9}')
        for f in plan:
            print(f'  {f.category:<12}{f.share:>7.2f}{f.handling_split[0]:>7.2f}{f.handling_split[1]:>9.2f}')
        # Conveyable fraction is a store-only metric (fulfillment isn't conveyable/non-conveyable).
        print(f'\n  expected conveyable fraction = {_expected_conveyable_fraction(CREATION_PLAN):.3f}  '
              f'(store-only, target ~0.75)')
        if args.fulfillment_fraction > 0:
            cf = args.ff_cube_fraction
            print(f'  fulfillment fraction = {args.fulfillment_fraction:.3f}  '
                  f'(rect {1 - cf:.0%} / cube {cf:.0%}; weight {_ff_weight_spec(args)})')
        pairs = args.num_skus * args.top_k
        print(f'  affinity estimate: ~{pairs:,} pairs  ~{pairs * 2 * 28 / 1_048_576:.0f} MB  (top-{args.top_k})\n')
        return

    ts         = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_name   = args.name or f'mixed_{ts}'   # one timestamped run folder; one leaf per lead spec
    run_dir    = os.path.join(out_dir, run_name)
    lead_specs = [_parse_lead_spec(s, args.lead_random_range, args.freq_profile) for s in args.lead_times]

    print(f'\n{"="*64}')
    print(f'  Mixed run     : {run_name}')
    print(f'  Dir           : {run_dir}')
    print(f'  num_skus={args.num_skus:,}  seed={args.seed}  coverage={args.coverage}')
    print(f'  freq profile  : {args.freq_profile}')
    print(f'  lead specs    : {args.lead_times}  ->  ' + ', '.join(n for n, _, _ in lead_specs))
    print(f'  expected conveyable fraction ~ {_expected_conveyable_fraction(CREATION_PLAN):.3f}  (store-only)')
    if args.fulfillment_fraction > 0:
        print(f'  fulfillment    : {args.fulfillment_fraction:.0%} of SKUs  '
              f'(cube {args.ff_cube_fraction:.0%} / rect {1 - args.ff_cube_fraction:.0%})')
    print(f'{"="*64}\n')

    for prof_name, lead_time, lead_range in lead_specs:
        leaf = os.path.join(run_dir, prof_name)
        os.makedirs(leaf, exist_ok=True)
        desc = f'random {lead_range}' if lead_range else f'{lead_time:g}'
        print(f'\n[{prof_name}] lead={desc}')

        t0      = time.perf_counter()
        inv_run = _inv_run(
            name                         = 'inventory',
            num_skus                     = args.num_skus,
            seed                         = args.seed,           # same seed → datasets differ only by lead
            out_dir                      = leaf,
            creation_plan                = plan,
            lead_time                    = lead_time,
            lead_time_range              = lead_range,
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
