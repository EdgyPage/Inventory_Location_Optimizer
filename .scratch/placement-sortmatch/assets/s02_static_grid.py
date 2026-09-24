"""S00/S02/S03 -- the static drain grid: every matcher on real drains, both channels,
every arm of a `_churn_probe` root, against the exact optimum.

    python .scratch/placement-sortmatch/assets/s02_static_grid.py <run_root> [--batches 5]
        [--json out.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lab  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--batches', type=int, default=5)
    ap.add_argument('--keyframe', type=int, default=25)
    ap.add_argument('--json')
    a = ap.parse_args()
    rows = []
    for cell in sorted(os.path.basename(p) for p in glob.glob(os.path.join(a.root, 'k*'))):
        for channel in ('store', 'fulfillment'):
            dbs = glob.glob(os.path.join(a.root, cell, '*', '*', channel, 'sim_*.db'))
            arms = sorted({os.path.basename(p)[4:-3] for p in dbs
                           if not p.endswith('.keyframes.db')})
            for arm in arms:
                drains = lab.load_drains(a.root, cell, channel, arm, a.keyframe, a.batches)
                if not drains:
                    continue
                print(f'\n== {cell} {channel} {arm}')
                tot, wall, peak = lab.report(drains)
                rows.append(dict(cell=cell, channel=channel, arm=arm,
                                 packs=sum(d.n for d in drains),
                                 objective=tot, wall=wall))
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(rows, fh, indent=1)


if __name__ == '__main__':
    main()
