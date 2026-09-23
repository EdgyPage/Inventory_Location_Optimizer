"""S15 -- P9a: does a velocity-aware rule hold a ground share above its own occupancy?

For each uni-start arm of a root's reference cell (store): the share of reorder placements that
land on a ground bin (M(y) = 1) per 10-day block, beside sigma_G -- the ground share of the
OCCUPIED bins of the placed classes at the last keyframe in the window.  S09's steady state says
a velocity-blind rule converges to s* = sigma_G; a turnover-aware one can sit above it.

    python .scratch/aisle-churn/assets/s15_ground.py <run_root> [<channel>]
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

BLOCK = 10


def main(root, channel='store'):
    from s06_pick import _geometry, _pick_cfg
    from s09_front import _M
    br = _pick_cfg(channel).height_brackets
    for db in sorted(glob.glob(os.path.join(root, 'k1_off_fifo', '*', '*', channel,
                                            'sim_uni_*.db'))):
        if db.endswith('.keyframes.db'):
            continue
        arm = os.path.basename(db)[4:-3]
        geo = _geometry(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(db))),
                                     'warehouse.db'))
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        blk = defaultdict(lambda: [0, 0])
        classes = set()
        for b, a, y in con.execute("select batch_id, aisle_id, bayY from bin_placement where "
                                   "cause = 'reorder' and batch_id < 40"):
            g = geo.by_id[a]
            classes.add(g.key)
            m = blk[b // BLOCK]
            m[0] += 1
            m[1] += _M(g.y_of(y), br) == 1.0
        kcon = sqlite3.connect('file:' + db.replace('.db', '.keyframes.db') + '?mode=ro',
                               uri=True)
        K = kcon.execute('select max(batch_id) from bin_keyframe where batch_id < 40').fetchone()[0]
        occ = gr = 0
        for a, y in kcon.execute('select aisle_id, bayY from bin_keyframe where batch_id = ?',
                                 (K,)):
            g = geo.by_id[a]
            if g.key in classes:
                occ += 1
                gr += _M(g.y_of(y), br) == 1.0
        shares = '  '.join(f'{v[1] / v[0]:6.1%}' for _k, v in sorted(blk.items()))
        print(f'{arm:30s} ground share by block {shares}   sigma_G@{K} {gr / max(1, occ):6.1%}')


if __name__ == '__main__':
    main(sys.argv[1], *(sys.argv[2:3]))
