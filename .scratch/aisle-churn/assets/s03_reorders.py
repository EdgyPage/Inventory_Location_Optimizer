"""S03 -- reorders fired and units ordered: the REORDERS model against the simulator.

Per leaf DB of a run root, over a pick window: the realised lines per SKU come from `picks`
(one line = one (batch, SKU) with units taken), and the model predicts the fires those lines
cause, sum_s lines_s / E[N_s] (E[N] = 1 when P = 0), and the units ordered, sum of units taken.
The simulator's own counts are `batch_stats.skus_reordered` / `units_ordered`, which fire at the
START of the next batch -- so picks in [lo, hi - 1) are compared with fires in [lo + 1, hi).

    python .scratch/aisle-churn/assets/s03_reorders.py <run_root> <lo> <hi>
"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def main(root: str, lo: int, hi: int) -> None:
    from Optimization.simconfig.models.reorders import renewal_lines, renewal_lines_noisy
    from Warehouse.generation.generate_inventory import load_run_inventory
    inv = glob.glob(os.path.join(root, '_frozen', '*', 'planned_inventory.db'))[0]
    by_sku = {c.sku: c for c in load_run_inventory(inv).orders}
    ncache: dict = {}

    def n_of(c, noisy=True):
        key = (c.demand.line.params['lam'], getattr(c, 'pipeline_qty', 0) or 0,
               round(float(getattr(c, 'supply_cv', 0.0) or 0.0), 4) if noisy else 0.0)
        if key not in ncache:
            ncache[key] = renewal_lines_noisy(*key)
        return ncache[key]

    tot = defaultdict(float)
    print(f'{"leaf":60s} {"lines":>7} {"pred fires":>10} {"sim fires":>9} {"err":>7}  '
          f'{"picked":>7} {"ordered":>8} {"err":>7}')
    for db in sorted(glob.glob(os.path.join(root, 'k1_off_*', '*', '*', '*', 'sim_*.db'))):
        if db.endswith('keyframes.db'):
            continue
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        lines = defaultdict(int)
        units = 0
        for b, sku, q in con.execute(
                'select batch_id, sku, sum(quantity) from picks where batch_id >= ? and '
                'batch_id < ? group by batch_id, sku', (lo, hi - 1)):
            lines[sku] += 1
            units += q
        pred = sum(n / n_of(by_sku[s]) for s, n in lines.items())
        pred0 = sum(n / n_of(by_sku[s], noisy=False) for s, n in lines.items())
        fill = sum(getattr(by_sku[s], 'pipeline_qty', 0) or 0 for s in lines)
        fires, ordered = con.execute(
            'select sum(skus_reordered), sum(units_ordered) from batch_stats where batch_id > ? '
            'and batch_id < ?', (lo, hi)).fetchone()
        nl = sum(lines.values())
        rel = os.path.relpath(db, root).replace(os.sep, '/')
        print(f'{rel[:60]:60s} {nl:7d} {pred:10.1f} {fires:9d} {fires / pred - 1:+7.2%}  '
              f'{units:7d} {ordered:8d} {ordered / (units + fill) - 1:+7.2%}   '
              f'(noise-free {fires / pred0 - 1:+.2%}; pipeline fill {fill:,} u)')
        for k, v in (('lines', nl), ('pred', pred), ('fires', fires), ('units', units + fill),
                     ('ordered', ordered)):
            tot[k] += v
    print(f'{"ALL":60s} {int(tot["lines"]):7d} {tot["pred"]:10.1f} {int(tot["fires"]):9d} '
          f'{tot["fires"] / tot["pred"] - 1:+7.2%}  {int(tot["units"]):7d} '
          f'{int(tot["ordered"]):8d} {tot["ordered"] / tot["units"] - 1:+7.2%}')
    naive = tot['lines']
    print(f'every-line-fires (E[N] = 1) would predict {naive:.0f} fires: '
          f'{tot["fires"] / naive - 1:+.2%}')


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
