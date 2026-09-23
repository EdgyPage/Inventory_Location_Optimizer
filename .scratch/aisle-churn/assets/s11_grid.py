"""S11 -- run the (k, c) grid of `_churn_probe`, one `run_simulation` per point, sequentially.

Launched as a no-console scheduled task from an immutable `git archive` copy (memory
`launch-long-drivers-detached`, `detached-runs-import-the-working-tree`).  Each point's stdout
goes to `grid_logs/<tag>.log` beside this script's working directory, and the run root it
printed is recorded in `grid_logs/grid_runs.json` as it finishes, so a partial grid is readable.

    python -X utf8 -u .scratch/aisle-churn/assets/s11_grid.py [--workers N] [--only TAG ...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

STORE_DEMAND = 0.00245335
FF_DEMAND = 0.0181380
POINTS = [  # (tag, store k, ff k, c, n_batches)
    ('k1_c95', 1, 1, 0.95, 40), ('k1_c80', 1, 1, 0.80, 40),
    ('k3_c95', 3, 3, 0.95, 40), ('k3_c80', 3, 3, 0.80, 40),
    ('k10_c95', 10, 10, 0.95, 40), ('k10_c80', 10, 10, 0.80, 40),
    ('k30_c95', 30, 10, 0.95, 40), ('k30_c80', 30, 10, 0.80, 40),
    ('k1_c95_h80', 1, 1, 0.95, 80),
    # S12: bisecting the unloading-order threshold (dock contention), registered in S11
    ('k20_c95', 20, 10, 0.95, 40), ('k25_c95', 25, 10, 0.95, 40),
]
# S15: the plan's other placement rules (spec `_churn_rules`), below both gates
RULE_POINTS = [('r_k1', 1, 1, 0.95, 40), ('r_k3', 3, 3, 0.95, 40), ('r_k10', 10, 10, 0.95, 40)]



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--rules', action='store_true',
                    help='run the S15 rule supplement (_churn_rules)')
    a = ap.parse_args()
    logs = os.path.join(os.getcwd(), 'grid_logs')
    os.makedirs(logs, exist_ok=True)
    book = os.path.join(logs, 'grid_runs.json')
    done = json.load(open(book, encoding='utf-8')) if os.path.exists(book) else {}
    spec = '_churn_rules' if a.rules else '_churn_probe'
    for tag, ks, kf, c, nb in (RULE_POINTS if a.rules else POINTS):
        if (a.only and tag not in a.only) or tag in done:
            continue
        cmd = [sys.executable, '-X', 'utf8', '-u', os.path.join('Optimization', 'run_simulation.py'),
               '--spec', spec, '--workers', str(a.workers),
               '--max-tasks-per-child', '1', '--no-analyze', '--n-batches', str(nb),
               '--store-demand', f'{STORE_DEMAND * ks:.8g}', '--ff-demand', f'{FF_DEMAND * kf:.8g}',
               '--first-time-confidence', f'{c:g}']
        t0 = time.time()
        with open(os.path.join(logs, f'{tag}.log'), 'w', encoding='utf-8') as fh:
            rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
        text = open(os.path.join(logs, f'{tag}.log'), encoding='utf-8', errors='replace').read()
        m = re.search(r'Root:\s*(\S+)', text)
        done[tag] = {'root': m.group(1) if m else None, 'rc': rc, 'spec': spec,
                     'store_k': ks, 'ff_k': kf,
                     'c': c, 'n_batches': nb, 'wall_s': round(time.time() - t0)}
        json.dump(done, open(book, 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
