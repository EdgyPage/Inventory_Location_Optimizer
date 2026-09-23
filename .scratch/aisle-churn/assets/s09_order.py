"""S09 -- the unloading-order bound: how much ANY order of a day's arrivals could move picking.

An unloading order changes only WHICH of the day's arriving packs gets WHICH of the bins the
placement rule hands out that day (the fronts of S09's frontier law move by the day's count
whatever the order).  So on day d, in class c, with packs i of pick weight w_i (the handling
seconds per unit multiplier, h_i x units later picked from the pack inside the window) and bins
j of height multiplier M_j, the day's fresh-pick cost is sum_i w_i M_pi(i) for some pairing pi.
By the rearrangement inequality every order lies between

    C_min = sum w_(i) desc . M_(i) asc          C_max = sum w_(i) desc . M_(i) desc

and two velocity-blind orders (fifo, lifo) are EXCHANGEABLE pairings -- the expected gap
between them is exactly zero, with a random-permutation spread

    Var = (1 / (n - 1)) sum (w - w_bar)^2 sum (M - M_bar)^2.

Read with the realised w (retrodiction): the gap an oracle order could win, C_actual - C_min,
and the null spread, summed over days and classes, as shares of the window's pick time.

    python .scratch/aisle-churn/assets/s09_order.py <run_root> <cell> <channel> <arm> [<lo> <hi>]
"""
from __future__ import annotations

import glob
import math
import os
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def rearrangement(w, M):
    """(C_actual, C_min, C_max, sd_random) for packs `w` paired in order with bins `M`."""
    n = len(w)
    act = sum(a * b for a, b in zip(w, M))
    ws, Ms = sorted(w, reverse=True), sorted(M)
    cmin = sum(a * b for a, b in zip(ws, Ms))
    cmax = sum(a * b for a, b in zip(ws, reversed(Ms)))
    if n < 2:
        return act, cmin, cmax, 0.0
    wb, Mb = sum(w) / n, sum(M) / n
    var = sum((a - wb) ** 2 for a in w) * sum((b - Mb) ** 2 for b in M) / (n - 1)
    return act, cmin, cmax, math.sqrt(var)


def main(root, cell, channel, arm, lo=0, hi=40):
    from s06_pick import _geometry, _pick_cfg
    from s09_front import _M
    from Warehouse.generation.generate_inventory import load_run_inventory
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(db)))
    geo = _geometry(os.path.join(pair_dir, 'warehouse.db'))
    cfg = _pick_cfg(channel)
    br = cfg.height_brackets
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    place = {}
    for b, a, x, y, sku in con.execute(
            "select batch_id, aisle_id, bayX, bayY, sku from bin_placement where cause = "
            "'reorder' and batch_id >= ? and batch_id < ? order by batch_id", (lo, hi)):
        place.setdefault((sku, a, x, y), b)
    w = defaultdict(float)                 # (sku, bin) -> handling seconds / M picked later
    for b, a, x, y, sku, q in con.execute(
            'select batch_id, aisle_id, bayX, bayY, sku, quantity from picks '
            'where batch_id >= ? and batch_id < ?', (lo, hi)):
        k = (sku, a, x, y)
        if k in place and place[k] <= b:
            o = orders[sku]
            w[k] += cfg.closed_form.evaluate(y=0.0, w=o.weight, vol=o.volume(), q=q)
    T = con.execute('select sum(task_makespan) from batch_stats where batch_id >= ? and '
                    'batch_id < ?', (lo, hi)).fetchone()[0]
    groups = defaultdict(lambda: ([], []))
    for (sku, a, x, y), b in place.items():
        g = groups[(b, geo.by_id[a].key)]
        g[0].append(w.get((sku, a, x, y), 0.0))
        g[1].append(_M(geo.by_id[a].y_of(y), br))
    act = cmin = cmax = var = 0.0
    for ws, Ms in groups.values():
        a_, lo_, hi_, sd = rearrangement(ws, Ms)
        act += a_; cmin += lo_; cmax += hi_; var += sd * sd
    print(f'{cell}/{channel}/{arm}: {len(place):,} fresh packs in {len(groups):,} (day, class) '
          f'groups; window pick time {T:,.0f} s')
    print(f'  fresh-pick at-location cost as placed   {act:12,.0f} s  ({act / T:.2%} of T)')
    print(f'  oracle order (rearrangement minimum)    {cmin:12,.0f} s  -> could win {(cmin - act) / T:+.3%}')
    print(f'  adversarial order (maximum)             {cmax:12,.0f} s  -> could lose {(cmax - act) / T:+.3%}')
    print(f'  two exchangeable orders: E[gap] = 0, sd {math.sqrt(2 * var):,.0f} s = '
          f'{math.sqrt(2 * var) / T:.3%} of T')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(*a[:4], *(int(x) for x in a[4:6]))
