"""S09 -- breathing room: how long a ranked rule keeps finding good free bins.

THE MODEL.  A ranked placement rule sends each fresh pack to the cheapest free bin of its class
(travel cost D = x * x_pace + y * y_pace).  Freed bins return slowly (a fresh pack is picked out
only by its SKU's next line, S08), so over a window the rule CONSUMES its class's free pool in
cost order: after u = placements so far / free bins of the class, the next placement lands at
the free pool's u-quantile.  Everything a placement is worth is therefore a function of the
consumed fraction, and the pool's size over the daily placement rate,

    B_c = F_c / m_c            (days of breathing room in class c)

is the clock that runs it out.

TWO VARIANTS of "cheapest": GLOBAL (the class's whole free list in D order) and PER-AISLE (each
aisle consumes its own D-sorted free list at an equal share of the class's placements -- what an
aisle-balancing rule like rank_cartlabor does).

MEASURED vs PREDICTED, per 10-day block: the share of reorder placements landing in ground-level
bins (M(y) = 1) and their mean height multiplier.

    python .scratch/aisle-churn/assets/s09_breathing.py <run_root> <cell> <channel> <arm> [<lo> <hi>]
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


def _M(y):
    return 1.0 if y < 96 else 1.2 if y < 240 else 1.4


def main(root, cell, channel, arm, lo=0, hi=40):
    from s06_pick import _geometry, _pick_cfg
    from Warehouse.kernel.cost_model import sec_per_inch
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    kf = db.replace('.db', '.keyframes.db')
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(db)))
    geo = _geometry(os.path.join(pair_dir, 'warehouse.db'))
    cfg = _pick_cfg(channel)
    xp, yp = sec_per_inch(cfg.x_speed), sec_per_inch(cfg.y_speed)
    kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
    K = kcon.execute('select min(batch_id) from bin_keyframe where batch_id >= ?',
                     (lo,)).fetchone()[0]
    occupied = {(a, bx, by) for a, bx, by in kcon.execute(
        'select aisle_id, bayX, bayY from bin_keyframe where batch_id = ?', (K,))}
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    placed = con.execute("select batch_id, aisle_id, bayX, bayY from bin_placement "
                         "where cause = 'reorder' and batch_id >= ? and batch_id < ?",
                         (lo, hi)).fetchall()
    used_aisles = {a for _b, a, _x, _y in placed}
    classes = {geo.by_id[a].key for a in used_aisles}
    # the free pool at the window's start, per class and per aisle, each bin (D, M)
    free_cls, free_aisle = defaultdict(list), defaultdict(list)
    for a in geo.aisles:
        if a.key not in classes:
            continue
        for c in range(1, a.C + 1):
            for r in range(1, a.R + 1):
                if (a.aisle_id, c, r) in occupied:
                    continue
                x, y = a.x_of(c), a.y_of(r)
                rec = (x * xp + y * yp, _M(y))
                free_cls[a.key].append(rec)
                free_aisle[a.aisle_id].append(rec)
    for v in free_cls.values():
        v.sort()
    for v in free_aisle.values():
        v.sort()
    aisles_of = defaultdict(list)
    for aid in free_aisle:
        aisles_of[geo.by_id[aid].key].append(aid)
    # measured, and the consumption count per class as the window runs
    per_cls_day = defaultdict(lambda: defaultdict(int))
    meas = defaultdict(lambda: [0, 0, 0.0])
    for b, a, bx, by in placed:
        key = geo.by_id[a].key
        per_cls_day[key][b] += 1
        M = _M(geo.by_id[a].y_of(by))
        m = meas[(b - lo) // BLOCK]
        m[0] += 1; m[1] += (M == 1.0); m[2] += M
    pred_g = defaultdict(lambda: [0, 0, 0.0])
    pred_a = defaultdict(lambda: [0, 0, 0.0])
    for key, days in per_cls_day.items():
        pool = free_cls[key]
        n_a = max(1, len(aisles_of[key]))
        used = 0
        for b in sorted(days):
            for _ in range(days[b]):
                blk = (b - lo) // BLOCK
                # global: the next bin of the class's cost-sorted free list
                d, M = pool[min(used, len(pool) - 1)] if pool else (0.0, 1.0)
                g = pred_g[blk]; g[0] += 1; g[1] += (M == 1.0); g[2] += M
                # per aisle: each aisle has consumed used / n_a of its own list
                depth = used / n_a
                tot = cnt = gm = 0.0
                for aid in aisles_of[key]:
                    lst = free_aisle[aid]
                    if not lst:
                        continue
                    dM = lst[min(int(depth), len(lst) - 1)][1]
                    cnt += 1; tot += dM; gm += (dM == 1.0)
                pa = pred_a[blk]; pa[0] += 1
                if cnt:
                    pa[1] += gm / cnt; pa[2] += tot / cnt
                used += 1
    F = sum(len(v) for v in free_cls.values())
    m_day = len(placed) / (hi - lo)
    print(f'{cell}/{channel}/{arm}: free bins in the placed classes {F:,} at batch {K}, '
          f'{m_day:,.0f} placements/day -> breathing room B = {F / m_day:,.0f} days')
    print(f'  {"block":>9} {"measured ground / M":>22} {"global model":>18} {"per-aisle model":>18}')
    for blk in sorted(meas):
        m, g, a = meas[blk], pred_g[blk], pred_a[blk]
        print(f'  {lo + blk * BLOCK:3d}-{lo + blk * BLOCK + BLOCK - 1:3d}   '
              f'{m[1] / m[0]:7.1%}  {m[2] / m[0]:.3f}      {g[1] / g[0]:6.1%}  {g[2] / g[0]:.3f}'
              f'      {a[1] / a[0]:6.1%}  {a[2] / a[0]:.3f}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3], *(int(x) for x in a[4:6]))
