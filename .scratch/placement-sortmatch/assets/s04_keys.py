"""S04b -- which PACK KEY makes a drain-wide sort-match win on realised work?

Runs `batch_sort` (and the online scaled quantile) under every pack key of `dyn.run_policy`,
beside `recorded`, `uniform` and `cheapest`, on the named arms' streams.

    python .scratch/placement-sortmatch/assets/s04_keys.py <run_root> <cell> <channel> <arm>...
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dyn  # noqa: E402

KEYS = ('cost', 'alpha', 'visits', 'units', 'labor')


def main():
    root, cell, channel, *arms = sys.argv[1:]
    for arm in arms:
        tiers, end = dyn.load_stream(root, cell, channel, arm)
        base = {}
        for pol in ('recorded', 'uniform', 'cheapest'):
            base[pol] = sum(dyn.run_policy(t, end, pol)[0] for t in tiers.values())
        u = base['uniform']
        print(f'\n== {cell} {channel} {arm}')
        for pol in ('recorded', 'cheapest'):
            print(f'  {pol:26s} {base[pol] / u - 1:+8.3%}')
        for key in KEYS:
            for pol in ('batch_sort', 'qscale:batch0.25'):
                tot = sum(dyn.run_policy(t, end, pol, key)[0] for t in tiers.values())
                print(f'  {pol + " / " + key:26s} {tot / u - 1:+8.3%}', flush=True)


if __name__ == '__main__':
    main()
