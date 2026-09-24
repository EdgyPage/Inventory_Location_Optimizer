"""S05 -- does sort-match pile the lines into a few aisles?

Per policy, the realised pick LINES each aisle receives from the window's arrivals
(summed over the packs' lives), then: the busiest aisle, its ratio to the mean over
aisles that received any, and the share of lines on the busiest 5% of aisles.  The aisle
ceiling (aisle-churn S04: one picker per aisle-day, k* = S / max W_a) binds on the
BUSIEST aisle, so a rule that raises max W_a lowers k*.

    python .scratch/placement-sortmatch/assets/s05_aisles.py <run_root> <cell> <channel> <arm>
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dyn  # noqa: E402

ROWS = [('recorded', 'cost'), ('uniform', 'cost'), ('cheapest', 'cost'),
        ('batch_tmin', 'cost'), ('batch_sort', 'visits'), ('qscale:batch0.25', 'visits')]


def main():
    root, cell, channel, arm = sys.argv[1:5]
    tiers, end = dyn.load_stream(root, cell, channel, arm)
    print(f'{"policy":28s} {"max aisle lines":>15s} {"max/mean":>9s} {"top-5% share":>12s} '
          f'{"aisles used":>11s}')
    for pol, key in ROWS:
        loads: dict = {}
        for t in tiers.values():
            dyn.run_policy(t, end, pol, key, loads=loads)
        v = np.array(sorted(loads.values(), reverse=True))
        top = max(1, int(round(0.05 * len(v))))
        print(f'{pol + "/" + key:28s} {v[0]:15.0f} {v[0] / v.mean():9.2f} '
              f'{v[:top].sum() / v.sum():12.1%} {len(v):11d}', flush=True)


if __name__ == '__main__':
    main()
