"""S04 -- the dynamic lab over every arm of a root, both channels.

Each row is (policy, pack key); `recorded` is the arm's own bins.  Realised work is
reported against the uniform draw on the same stream.

    python -u .scratch/placement-sortmatch/assets/s04_dyn_grid.py <run_root>
        [--cells k1_off_fifo] [--json out.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dyn  # noqa: E402

ROWS = [('recorded', 'cost'), ('uniform', 'cost'), ('cheapest', 'cost'),
        ('batch_tmin', 'cost'), ('quantile', 'visits'), ('ideal', 'visits'),
        ('batch_sort', 'cost'), ('batch_sort', 'visits'),
        ('qscale:batch0.25', 'visits'), ('qscale:batch', 'visits')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--cells', nargs='*')
    ap.add_argument('--json')
    a = ap.parse_args()
    cells = a.cells or sorted(os.path.basename(p) for p in glob.glob(os.path.join(a.root, 'k*')))
    rows = []
    for cell in cells:
        for channel in ('store', 'fulfillment'):
            dbs = glob.glob(os.path.join(a.root, cell, '*', '*', channel, 'sim_*.db'))
            arms = sorted({os.path.basename(p)[4:-3] for p in dbs
                           if not p.endswith('.keyframes.db')})
            for arm in arms:
                if arm.startswith('opt_fifo'):
                    continue          # byte-identical to uni_fifo (FIFO restock)
                tiers, end = dyn.load_stream(a.root, cell, channel, arm)
                tot, wall = {}, {}
                n = sum(len(t['arrivals']) for t in tiers.values())
                for pol, key in ROWS:
                    c = w = 0.0
                    for t in tiers.values():
                        ci, wi, _k = dyn.run_policy(t, end, pol, key)
                        c += ci
                        w += wi
                    tot[f'{pol}/{key}'] = c
                    wall[f'{pol}/{key}'] = w
                u = tot['uniform/cost']
                print(f'\n== {cell} {channel} {arm}: {n} packs', flush=True)
                for k in tot:
                    print(f'  {k:26s} {tot[k] / u - 1:+8.3%}  {1e6 * wall[k] / max(n, 1):7.1f} us/pack',
                          flush=True)
                rows.append(dict(cell=cell, channel=channel, arm=arm, packs=n, work=tot,
                                 wall=wall))
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(rows, fh, indent=1)


if __name__ == '__main__':
    main()
