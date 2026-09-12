"""Draw the reference pair's batch script under a NAMED sampler, into any directory.

Takes the v2 run's OWN BatchConfig off its pickled cache (so mean/std/inventory_size are
the run's, not retyped) and changes exactly one field: sampler='v3'.  The seeds come from
the run_spec, so the v3 script is the same experiment drawn by the fixed sampler.

Serial on purpose: `precompute_batches` would spawn a pool, and the affinity partner map is
~1 GB resident per process.

    REPO=. python .scratch/department-calibration/assets/draw_batches_under_sampler.py <out dir> [sampler, default v3]
"""
import os
import pickle
import sys
from dataclasses import replace as dc_replace

sys.path.insert(0, os.environ['REPO'])
import Optimization.config.sim_config  # noqa: F401  (loads .env)

from Optimization.simdriver.batch_precompute import ensure_batches

RUN = 'comparison_20260912_055947'
CELL = 'k1_off'
PAIR = 'mixed_20260816_131535__mixed_realistic_bell_lt0'
OUT = sys.argv[1]
SAMPLER = sys.argv[2] if len(sys.argv) > 2 else 'v3'

import json

base = os.environ['COMPARISON_OUTPUT_DIR']
run_dir = os.path.join(base, RUN)
pair_dir = os.path.join(run_dir, CELL, PAIR)
spec = json.load(open(os.path.join(run_dir, 'run_spec.json')))
_name, inv_db, aff_db = spec['pairs'][0]
seed_batches, n_batches = int(spec['seed_batches']), int(spec['n_batches'])

from Optimization.config.channels import FF_BATCH_SEED_OFFSET

os.makedirs(OUT, exist_ok=True)

# Identify each cached file's channel by its BatchConfig.inventory_size against the
# regime-filtered section size -- the filename's fingerprint carries no channel.
from Optimization.simdriver.batch_precompute import _load_worker_inventory

sizes = {}
for regime in ('store', 'fulfillment'):
    sizes[len(_load_worker_inventory(inv_db, None, None, regime).orders)] = regime
print('section sizes ->', sizes)

for p in sorted(f for f in os.listdir(pair_dir) if f.startswith('_batches_')):
    with open(os.path.join(pair_dir, p), 'rb') as f:
        cfg = pickle.load(f)['batches'][0].config
    regime = sizes[cfg.inventory_size]
    seed = seed_batches + (FF_BATCH_SEED_OFFSET if regime == 'fulfillment' else 0)
    v3 = dc_replace(cfg, sampler=SAMPLER)
    print(f'{regime}: n={cfg.inventory_size} mean={cfg.mean_fraction} std={cfg.std_fraction} '
          f'seed={seed} {cfg.sampler} -> {SAMPLER}')
    path, fp = ensure_batches(OUT, inv_db, None, None, aff_db, v3, seed, n_batches,
                              workers=1, channel_regime=regime)
    with open(path, 'rb') as f:
        bs = pickle.load(f)['batches']
    req = sum(min(b.num_skus, b.config.inventory_size) for b in bs)
    got = sum(len(b.items) for b in bs)
    print(f'   -> {os.path.basename(path)}  requested={req} delivered={got} '
          f'shortfall={100.0 * (req - got) / req:.2f}%')
