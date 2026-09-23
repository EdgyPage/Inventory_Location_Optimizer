"""S09 -- the LPT balancer's aisle concentration as water-filling.

Rank_labor / Rank_cartlabor send each unit to the aisle minimising load_a + fq * cost_a, where
load_a is the aisle's expected picking labour, sum over its SKUs of e_s = f q cost1 (the
ledger's pick_load_sum, SKU-once).  With the within-aisle cost a small perturbation, that is
water-filling: after adding W of labour to a class, every aisle below the level lambda is raised
to it,

        sum_a (lambda - L_a)^+ = W,        active aisles  A_on = #{a : L_a < lambda},

and aisle a receives a share (lambda - L_a)^+ / W of the placements.  The effective number of
receiving aisles is 1 / sum_a share_a^2.  Inputs: the loads at the keyframe (each aisle's
distinct SKUs' expected_labor) and the placed units' e_s -- read, not fitted.

    python .scratch/aisle-churn/assets/s09_waterfill.py <run_root> <cell> <channel> <arm> [<lo> <hi>]
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def water_level(loads, W):
    """lambda with sum (lambda - L)^+ = W, and each aisle's share of W."""
    Ls = sorted(loads)
    acc = 0.0
    lam = Ls[0]
    for i, L in enumerate(Ls):
        nxt = Ls[i + 1] if i + 1 < len(Ls) else float('inf')
        # raising the i+1 lowest aisles from L to nxt costs (i+1) (nxt - L)
        room = (i + 1) * (nxt - L)
        if acc + room >= W:
            lam = L + (W - acc) / (i + 1)
            break
        acc += room
    return lam, [max(0.0, lam - L) / W for L in loads]


def main(root, cell, channel, arm, lo=1, hi=10):
    from s06_pick import _geometry, _pick_cfg
    from Warehouse.generation.generate_inventory import load_run_inventory
    cfg = _pick_cfg(channel)
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    for o in orders.values():
        o.compute_labor_cost(cfg.pick_intercept, cfg.pick_weight_coef, cfg.pick_volume_coef,
                             cfg.pick_weight_fn, cfg.pick_volume_fn,
                             pick_per_item=cfg.pick_per_item)
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    kf = db.replace('.db', '.keyframes.db')
    geo = _geometry(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(db))),
                                 'warehouse.db'))
    kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
    K = kcon.execute('select min(batch_id) from bin_keyframe').fetchone()[0]
    skus = defaultdict(set)
    for a, sku in kcon.execute('select aisle_id, sku from bin_keyframe where batch_id = ?', (K,)):
        skus[a].add(sku)
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    got = defaultdict(Counter)
    add = defaultdict(float)
    for a, sku in con.execute("select aisle_id, sku from bin_placement where cause = 'reorder' "
                              "and batch_id >= ? and batch_id < ?", (lo, hi)):
        key = geo.by_id[a].key
        got[key][a] += 1
        add[key] += orders[sku].expected_labor
    print(f'{cell}/{channel}/{arm}: batches {lo}-{hi - 1}')
    print(f'  {"class":46s} {"aisles":>6} {"measured: recv / eff":>21} {"water-fill: on / eff":>21}')
    for key, cnt in sorted(got.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
        aisles = [a.aisle_id for a in geo.by_class[key]]
        loads = [sum(orders[s].expected_labor for s in skus.get(a, ())) for a in aisles]
        lam, share = water_level(loads, add[key])
        n = sum(cnt.values())
        eff_m = 1.0 / sum((v / n) ** 2 for v in cnt.values())
        on = sum(1 for s in share if s > 0)
        eff_w = 1.0 / sum(s * s for s in share if s > 0)
        print(f'  {"/".join(key):46s} {len(aisles):6d} {len(cnt):9d} / {eff_m:6.1f}   '
              f'{on:9d} / {eff_w:6.1f}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(*a[:4], *(int(x) for x in a[4:6]))
