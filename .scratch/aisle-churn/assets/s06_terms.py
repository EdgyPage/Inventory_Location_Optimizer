"""S06b -- pick labour split into travel, cart swaps and handling: closed form (c) vs realised.

For one leaf DB and one keyframe window [K, end): the realised per-day travel, swap count and
handling from `picker_events` (`Simulation_Analytics.task_time_breakdown` charges a gap ending
at 'arrive' or 'cart_swap' to travel, at 'pick' to handling), against `expected_travel` over the
keyframe's placement with the drain-order fix and the window's realised per-SKU lines, at the
window's own lines per day.

    python .scratch/aisle-churn/assets/s06_terms.py <run_root> <cell> <channel> <arm> <K> <end> [<offset>]
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def main(root, cell, channel, arm, K, end, offset=0):
    from s06_pick import _dist, _geometry, _pick_cfg
    from Optimization.metrics.Simulation_Analytics import task_time_breakdown
    from Optimization.persistence.Picking_Data import load_picker_events
    from Optimization.simconfig import expected_travel as et
    from Optimization.simconfig.staffing import regime_orders
    from Optimization.simdriver.batch_precompute import read_batches_blob
    from Warehouse.generation.generate_inventory import load_run_inventory
    orders = load_run_inventory(glob.glob(os.path.join(root, '_frozen', '*',
                                                       'planned_inventory.db'))[0]).orders
    sec = regime_orders(orders, channel)
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    kf = db.replace('.db', '.keyframes.db')
    pair_dir = os.path.dirname(os.path.dirname(os.path.dirname(db)))
    geo, cfg = _geometry(os.path.join(pair_dir, 'warehouse.db')), _pick_cfg(channel)
    script = None
    for pkl in glob.glob(os.path.join(pair_dir, '_batches_*.pkl')):
        blob = read_batches_blob(pkl)
        if blob and blob[1] and next(iter(blob[1][0].items)) in {c.sku for c in sec}:
            script = blob[1]
    win = script[K - offset:end - offset]
    cnt = Counter(s for b in win for s in b.items)
    B = end - K
    n = sum(cnt.values()) / B
    bm = defaultdict(list)
    kcon = sqlite3.connect('file:' + kf + '?mode=ro', uri=True)
    for a, bx, by, sku, q in kcon.execute(
            'select aisle_id, bayX, bayY, sku, qty from bin_keyframe where batch_id = ?', (K,)):
        bm[sku].append((a, bx, by, q))
    sec_w = [SimpleNamespace(sku=c.sku, weight=c.weight, volume=c.volume,
                             demand=SimpleNamespace(relative_frequency=cnt.get(c.sku, 0) + 1e-9,
                                                    line=c.demand.line)) for c in sec]
    r = et.accumulate(sec_w, cfg, _dist(bm, geo, True), geo)
    cf = et.expected_pick(r, geo, cfg, n, 0.0)
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    run_id = con.execute('select min(run_id) from batch_stats').fetchone()[0]
    tr = hd = ot = 0.0
    swaps = 0
    tasks = 0
    units = 0
    for b in range(K, end):
        ev = load_picker_events(db, run_id, b)
        t, h, o = task_time_breakdown(ev)
        tr += t; hd += h; ot += o
        swaps += sum(1 for e in ev if e.event_type == 'cart_swap')
    tasks, units = con.execute('select sum(num_tasks), sum(total_items) from batch_stats '
                               'where batch_id >= ? and batch_id < ?', (K, end)).fetchone()
    coef = float(cfg.cart_swap_coef)
    real = {'lines/day': n, 'units/day': units / B, 'tasks/day': tasks / B,
            'swaps/day': swaps / B, 'swap_s/day': swaps * coef / B,
            'travel_s/day': (tr - swaps * coef) / B, 'handling_s/day': hd / B,
            'total_s/day': (tr + hd) / B}
    pred = {'lines/day': n, 'units/day': cf['units'], 'tasks/day': cf['tasks'],
            'swaps/day': cf['swaps'], 'swap_s/day': cf['swap_s'],
            'travel_s/day': cf['travel_x_s'] + cf['travel_y_s'],
            'handling_s/day': cf['handling_s'], 'total_s/day': cf['total_s']}
    print(f'{cell}/{channel}/{arm}  batches {K}-{end - 1}  (other {ot / B:,.0f} s/day)')
    for k in real:
        print(f'  {k:15s} realised {real[k]:12,.1f}   closed form {pred[k]:12,.1f}   '
              f'{pred[k] / real[k] - 1 if real[k] else float("nan"):+7.1%}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3], int(a[4]), int(a[5]), int(a[6]) if len(a) > 6 else 0)
