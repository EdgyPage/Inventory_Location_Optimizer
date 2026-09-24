"""S04 anchor -- does the dynamic lab's occupancy model agree with the run?

Replays an arm's RECORDED bins through `dyn`'s release model and counts placements into a
bin the model still thinks occupied.  Two release rules:
  lag   a bin picked out in batch t is free from batch t+1 (dyn's rule as first written)
  same  a bin picked out in batch t is free for batch t's own drain

    python .scratch/placement-sortmatch/assets/s04_fidelity.py <run_root> <cell> <channel> <arm>
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dyn  # noqa: E402


def check(tiers, rule):
    bad = total = 0
    for t in tiers.values():
        occ_until = dict(t['init'])                 # bin -> batch its stock is picked out
        for p in t['arrivals']:
            b = p['batch']
            bid = p['bid']
            total += 1
            fb = occ_until.get(bid)
            if fb is not None:
                still = fb >= b if rule == 'lag' else fb > b
                bad += still
            occ_until[bid] = b + p['life']
    return bad, total


def main():
    root, cell, channel, arm = sys.argv[1:5]
    tiers, _end = dyn.load_stream(root, cell, channel, arm)
    for rule in ('lag', 'same'):
        bad, total = check(tiers, rule)
        print(f'{rule:5s}: {bad} of {total} recorded placements land in a bin the model '
              f'holds occupied ({bad / max(total, 1):.2%})')


if __name__ == '__main__':
    main()
