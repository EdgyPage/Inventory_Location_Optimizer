"""S17 -- what a VELOCITY-AWARE unloading order could win when puts stay FIFO.

The user's framing (2026-09-23): a real put runs in arrival order, +- a few placements, so the
only way to send fast packs to the best bins is to ORDER the unloading.  Under a FIFO put the
placement rule hands out the day's bins in its own order; an unloading order decides which pack
meets which of them.  The S09 rearrangement bound prices that pairing.  Three levels of freedom,
each a sort of the day's packs against the day's bins (per class, height multiplier M):

  oracle     packs sorted by their REALISED pick weight w (hindsight): the ceiling
  forecast   packs sorted by a FORECAST weight, scored with the realised w -- two forecasts:
             lambda_s h_s (the SKU's handling rate), and the pack's EXPECTED pick weight
             h_s q_pack (1 - exp(-lambda_s (H - t - l))): its handling times its units times
             the chance a later line of the SKU reaches it before the window ends
  trailer    the expected-weight order applied to whole TRAILERS only: the day's placements, in the
             sequence they were put (FIFO loading makes that the dispatch order), cut into the
             day's trailer count; trailers sorted by their mean forecast, packs within a
             trailer kept in order

each reported as the change in fresh-pick at-location seconds against the order the run used,
as a share of the window's pick time.

    python .scratch/aisle-churn/assets/s17_order_prize.py <run_root> <cell> <arm>
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


def _pair(w_sort, w_score, M):
    """Cost of pairing: packs ranked by `w_sort` desc get bins ranked by M asc; scored on
    `w_score`."""
    order = sorted(range(len(w_sort)), key=lambda i: -w_sort[i])
    Ms = sorted(M)
    return sum(w_score[i] * m for i, m in zip(order, Ms))


def main(root, cell, arm, H=40):
    from s06_pick import _geometry, _pick_cfg
    from s09_front import _M
    from Warehouse.generation.generate_inventory import load_run_inventory
    cfg = _pick_cfg('store')
    br = cfg.height_brackets
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    tot_f = sum(o.demand.relative_frequency for o in orders.values()) or 1.0
    db = glob.glob(os.path.join(root, cell, '*', '*', 'store', f'sim_{arm}.db'))[0]
    geo = _geometry(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(db))),
                                 'warehouse.db'))
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    place = {}
    rows = con.execute("select batch_id, seq, aisle_id, bayX, bayY, sku, qty from bin_placement "
                       "where cause = 'reorder' and batch_id < ? order by batch_id, seq",
                       (H,)).fetchall()
    qty = {}
    for b, sq, a, x, y, sku, q in rows:
        place.setdefault((sku, a, x, y), (b, sq))
        qty.setdefault((sku, a, x, y), q)
    import json, math
    spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
    cov = list(spec['staffing']['calibration'].values())[0]['coverage']
    n_lines = float(cov['lines_per_day']['store'])
    ell = 1.0 + float(cov['lead']['transit_days'])
    store_f = sum(o.demand.relative_frequency for o in orders.values()
                  if getattr(o, 'regime', None) in (None, 'store')) or tot_f
    w = defaultdict(float)
    for b, a, x, y, sku, q in con.execute('select batch_id, aisle_id, bayX, bayY, sku, '
                                          'quantity from picks where batch_id < ?', (H,)):
        k = (sku, a, x, y)
        if k in place and place[k][0] <= b:
            o = orders[sku]
            w[k] += cfg.closed_form.evaluate(y=0.0, w=o.weight, vol=o.volume(), q=q)
    T = con.execute('select sum(task_makespan) from batch_stats where batch_id < ?',
                    (H,)).fetchone()[0]
    site = sorted(glob.glob(os.path.join(root, cell, '*', '_site', '*.db')))
    tpd = {}
    if site:
        scon = sqlite3.connect('file:' + site[0] + '?mode=ro', uri=True)
        for (d,) in scon.execute('select cast(staged_s / 28800 as int) from yard_trailers'):
            tpd[d] = tpd.get(d, 0) + 1
    by_day = defaultdict(list)                     # day -> [(seq, key, class, M)]
    for (sku, a, x, y), (b, sq) in place.items():
        g = geo.by_id[a]
        by_day[b].append((sq, (sku, a, x, y), g.key, _M(g.y_of(y), br)))
    act = orac = fore = fore2 = trail = 0.0
    for b, items in by_day.items():
        items.sort()
        f = {k: (orders[k[0]].demand.relative_frequency / tot_f)
             * (orders[k[0]].labor_cost or 1.0) for _s, k, _c, _m in items}
        # the pack's EXPECTED pick weight: h x its units x P(re-picked before the window ends)
        f2 = {}
        for _s, k, _c, _m in items:
            o = orders[k[0]]
            lam = n_lines * o.demand.relative_frequency / store_f
            f2[k] = ((o.labor_cost or 1.0) * qty[k]
                     * (1.0 - math.exp(-lam * max(0.0, H - b - ell))))
        # trailers: the day's placements in put order, cut into that day's trailer count
        n_tr = max(1, tpd.get(b, 1))
        size = max(1, -(-len(items) // n_tr))
        chunk = {items[i][1]: i // size for i in range(len(items))}
        tr_mean = defaultdict(list)
        for _s, k, _c, _m in items:
            tr_mean[chunk[k]].append(f2[k])
        tr_rank = {t: sum(v) / len(v) for t, v in tr_mean.items()}
        seq_rank = {k: (-tr_rank[chunk[k]], i) for i, (_s, k, _c, _m) in enumerate(items)}
        for cls in {c for _s, _k, c, _m in items}:
            grp = [(k, m) for _s, k, c, m in items if c == cls]
            ks, Ms = [k for k, _m in grp], [m for _k, m in grp]
            wr = [w.get(k, 0.0) for k in ks]
            act += sum(x * m for x, m in zip(wr, Ms))
            orac += _pair(wr, wr, Ms)
            fore += _pair([f[k] for k in ks], wr, Ms)
            fore2 += _pair([f2[k] for k in ks], wr, Ms)
            # trailer level: bins handed out in the rule's order are matched to packs in
            # the new trailer sequence -- the i-th bin handed out goes to the i-th pack
            new = sorted(range(len(ks)), key=lambda i: seq_rank[ks[i]])
            trail += sum(wr[i] * m for i, m in zip(new, Ms))
    print(f'{arm}: fresh-pick at-location cost as run {act:,.0f} s ({act / T:.2%} of pick time T)')
    for name, v in (('oracle (hindsight, pack level)', orac),
                    ('forecast lambda*h, pack level', fore),
                    ('forecast h*q*P(re-pick), pack', fore2),
                    ('forecast, whole trailers only', trail)):
        print(f'  {name:32s} {v:12,.0f} s   change {(v - act) / T:+.3%} of T')


if __name__ == '__main__':
    main(*sys.argv[1:4])
