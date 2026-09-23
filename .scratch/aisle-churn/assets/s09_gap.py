"""S09 -- the rank-vs-fifo pick gap split by WHERE it is earned: picks served from bins a
reorder placed inside the window (the fresh bins, S08) against picks from the setup stock.

The at-location law (`PickConfig.closed_form`, output 'at_location') prices every pick row
M(y) (I + q (p + v_s)); summed per arm and split by the bin's provenance, the difference to the
fifo arm is the height mechanism's share of the gap, and the rest (travel, tasks, swaps) is
read off `batch_stats.task_makespan`.

    python .scratch/aisle-churn/assets/s09_gap.py <run_root> <cell> <channel> <arm> <fifo_arm> [<lo> <hi>]
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


def _arm(root, cell, channel, arm, lo, hi, orders, cfg, geo):
    from s09_front import _M
    db = glob.glob(os.path.join(root, cell, '*', '*', channel, f'sim_{arm}.db'))[0]
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    fresh = {}
    for b, a, x, y, sku in con.execute(
            "select batch_id, aisle_id, bayX, bayY, sku from bin_placement where cause = "
            "'reorder' and batch_id >= ? and batch_id < ?", (lo, hi)):
        k = (sku, a, x, y)
        fresh[k] = min(b, fresh.get(k, b))
    law = cfg.closed_form
    out = defaultdict(float)
    for b, a, x, y, sku, q in con.execute(
            'select batch_id, aisle_id, bayX, bayY, sku, quantity from picks '
            'where batch_id >= ? and batch_id < ?', (lo, hi)):
        o = orders[sku]
        yy = geo.by_id[a].y_of(y)
        t = law.evaluate(y=yy, w=o.weight, vol=o.volume(), q=q)
        M = _M(yy, cfg.height_brackets)
        side = 'fresh' if fresh.get((sku, a, x, y), hi) <= b else 'setup'
        out[side + '_t'] += t
        out[side + '_u'] += q
        out[side + '_Mu'] += M * q
        out[side + '_h'] += t / M
    out['makespan'] = con.execute('select sum(task_makespan) from batch_stats where batch_id '
                                  '>= ? and batch_id < ?', (lo, hi)).fetchone()[0]
    return out


def main(root, cell, channel, arm, fifo, lo=0, hi=40):
    from s06_pick import _geometry, _pick_cfg
    from Warehouse.generation.generate_inventory import load_run_inventory
    orders = {o.sku: o for o in load_run_inventory(glob.glob(os.path.join(
        root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
    pair_dir = os.path.dirname(glob.glob(os.path.join(root, cell, '*', 'warehouse.db'))[0])
    geo = _geometry(os.path.join(pair_dir, 'warehouse.db'))
    cfg = _pick_cfg(channel)
    r = _arm(root, cell, channel, arm, lo, hi, orders, cfg, geo)
    f = _arm(root, cell, channel, fifo, lo, hi, orders, cfg, geo)
    T = f['makespan']
    print(f'{cell}/{channel}: {arm} vs {fifo}, batches {lo}-{hi - 1}')
    print(f'  total pick time gap (task_makespan)          {r["makespan"] / T - 1:+.2%}')
    for side in ('fresh', 'setup'):
        print(f'  {side:5s}: units {r[side + "_u"]:9,.0f} / {f[side + "_u"]:9,.0f}   M per unit '
              f'{r[side + "_Mu"] / r[side + "_u"]:.3f} / {f[side + "_Mu"] / f[side + "_u"]:.3f}   '
              f'at-location s {r[side + "_t"]:12,.0f} / {f[side + "_t"]:12,.0f}   '
              f'gap share of T {(r[side + "_t"] - f[side + "_t"]) / T:+.2%}')
    at_r = r['fresh_t'] + r['setup_t']; at_f = f['fresh_t'] + f['setup_t']
    print(f'  at-location gap {(at_r - at_f) / T:+.2%} of T;  the rest (travel/tasks/swaps) '
          f'{(r["makespan"] - T - (at_r - at_f)) / T:+.2%}')
    # the closed-form height prediction: fresh-served units x h x (M_rank - M_fifo)
    hbar = f['fresh_h'] / f['fresh_u']
    dM = r['fresh_Mu'] / r['fresh_u'] - f['fresh_Mu'] / f['fresh_u']
    print(f'  height law on fresh picks: U_fresh h dM / T = {f["fresh_u"]:,.0f} x {hbar:.1f} x '
          f'{dM:+.3f} / {T:,.0f} = {f["fresh_u"] * hbar * dM / T:+.2%}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(*a[:5], *(int(x) for x in a[5:7]))
