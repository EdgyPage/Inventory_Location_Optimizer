"""S07 -- fulfillment's placement lever is aisle co-location under the sampler's affinity.

Every fulfillment bin is at M = 1 and its lanes are one-way (x travel = the lane whatever the
bin), so a placement can only change WHICH AISLES a day opens -- tasks, and the swaps and lane
walks that come with them.  Two closed forms for the aisles a day opens, per arm, at a
keyframe's placement (each SKU served from its first-drained bin, ADR-0003's smallest-on-hand):

  independent   E[tasks] = sum_a (1 - exp(-Lambda_a)),  Lambda_a = sum of the aisle's SKUs'
                line rates (the expected_travel form; blind to which SKUs are drawn TOGETHER)
  script        mean over the window's days of |union of the aisles that day's SKUs occupy|
                (conditional on the drawn script, which carries the affinity co-draws)

against the realised tasks per day (`batch_stats.num_tasks`).  The co-location value a rule
earns is the script form's rank-vs-fifo gap; the independent form's gap is what S06's closed
form could see.

    python .scratch/aisle-churn/assets/s07_colocation.py <run_root> <lo> <hi> [<offset>]
"""
from __future__ import annotations

import glob
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def first_aisle(kcon, K):
    """sku -> the aisle of its first-drained bin (smallest on hand, then location)."""
    best = {}
    for a, x, y, sku, q in kcon.execute('select aisle_id, bayX, bayY, sku, qty from '
                                        'bin_keyframe where batch_id = ?', (K,)):
        k = (q, a, x, y)
        if sku not in best or k < best[sku][0]:
            best[sku] = (k, a)
    return {s: v[1] for s, v in best.items()}


def main(root, lo, hi, offset=0, channel='fulfillment'):
    from Optimization.simdriver.batch_precompute import read_batches_blob
    for kf in sorted(glob.glob(os.path.join(root, 'k1_off_fifo', '*', '*', channel,
                                            'sim_*.keyframes.db'))):
        db = kf.replace('.keyframes.db', '.db')
        arm = os.path.basename(db)[4:-3]
        pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(kf)))
        kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        ks = [k for (k,) in kcon.execute('select distinct batch_id from bin_keyframe where '
                                        'batch_id >= ? and batch_id < ? order by 1', (lo, hi))]
        script = None
        for pkl in glob.glob(os.path.join(pair_dir, '_batches_*.pkl')):
            blob = read_batches_blob(pkl)
            if blob and blob[1]:
                s0 = next(iter(blob[1][0].items))
                if kcon.execute('select 1 from bin_keyframe where sku = ? limit 1',
                                (s0,)).fetchone():
                    script = blob[1]
        for i, K in enumerate(ks):
            end = ks[i + 1] if i + 1 < len(ks) else hi
            where = first_aisle(kcon, K)
            days = script[K - offset:end - offset]
            rate = Counter(s for b in days for s in b.items)
            lam = defaultdict(float)
            for s, n in rate.items():
                if s in where:
                    lam[where[s]] += n / len(days)
            indep = sum(1 - math.exp(-v) for v in lam.values())
            scr = sum(len({where[s] for s in b.items if s in where}) for b in days) / len(days)
            real = con.execute('select avg(num_tasks) from batch_stats where batch_id >= ? and '
                               'batch_id < ?', (K, end)).fetchone()[0]
            print(f'{channel:12s} {arm:26s} K={K:3d}  tasks/day: independent {indep:7.1f}  '
                  f'script {scr:7.1f}  realised {real:7.1f}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], int(a[1]), int(a[2]), int(a[3]) if len(a) > 3 else 0)
