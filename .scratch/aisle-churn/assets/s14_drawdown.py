"""S14 -- the free pool's drift as a stock drawdown: a run starts every SKU at Q on hand with
nothing on order, and settles to the mean on-hand of its order-up-to cycle.

Position IP runs between the reorder point and S = Q + P (uniform over (rp, S] for small lines);
the pipeline holds d * l on average, so on hand settles at

    E[OH] ~ (rp + 1 + Q + P) / 2 - d l

and the section frees (or fills) bins in proportion to sum_s (Q_s - E[OH]_s), at the section's own
bins-per-unit ratio at the start (occupied bins at keyframe 0 over sum Q).  On the floor
(rp = Q - 1, P = 0) the drawdown is d l + ~0: small.  Off the floor Q = C d is far above the
cycle's mean, so the pool GROWS; a pipeline allowance P >= 1 on a floor SKU makes it SHRINK
(the first fire orders P extra).  Everything from the run's record and catalogue; the measured
free pool (free_index, first -> last batch) is only read to compare.

    python .scratch/aisle-churn/assets/s14_drawdown.py <grid_runs.json> <channel> [<tag> ...]
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def main(book, channel, tags=None):
    from Optimization.simconfig.models import levels
    from Optimization.simconfig.staffing import regime_orders
    from Warehouse.generation.generate_inventory import load_run_inventory
    runs = json.load(open(book, encoding='utf-8'))
    print(f'{"point":9s} {"on floor":>9} {"sum Q":>10} {"sum E[OH]":>10} {"pred dF/F0":>11} '
          f'{"meas dF/F0":>11}')
    for tag, r in runs.items():
        if (tags and tag not in tags) or r['n_batches'] != 40:
            continue
        root = r['root']
        spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
        cov = list(spec['staffing']['calibration'].values())[0]['coverage']
        orders = regime_orders(load_run_inventory(glob.glob(os.path.join(
            root, '_frozen', '*', 'planned_inventory.db'))[0]).orders, channel)
        lead = cov['lead']
        rows = levels.section(orders, lines_per_day=cov['lines_per_day'][channel],
                              floor_lines=cov['floor'][channel]['floor_lines'],
                              transit_days=lead['transit_days'],
                              lead_unit_days=lead.get('lead_unit_days') or 1.0,
                              coverage_days=cov['coverage_days'],
                              safety_days=cov['safety_days'])
        sQ = sum(x['Q'] for _o, x in rows)
        sOH = sum(max(0.0, (x['rp'] + 1 + x['Q'] + x['P']) / 2.0 - x['d'] * x['ell'])
                  for _o, x in rows)
        on = sum(x['on_floor'] for _o, x in rows) / len(rows)
        db = glob.glob(f'{root}/k1_off_fifo/*/*/{channel}/sim_uni_fifo_norsl.db')[0]
        kf = db.replace('.db', '.keyframes.db')
        occ0 = sqlite3.connect('file:' + kf + '?mode=ro', uri=True).execute(
            'select count(*) from bin_keyframe where batch_id = (select min(batch_id) from '
            'bin_keyframe)').fetchone()[0]
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        free = dict(con.execute('select batch_id, sum(free) from free_index where batch_id < 40 '
                                'group by batch_id'))
        F0, F1 = free[min(free)], free[max(free)]
        pred = (sQ - sOH) * (occ0 / sQ) / F0
        print(f'{tag:9s} {on:9.1%} {sQ:10,.0f} {sOH:10,.0f} {pred:+11.1%} {F1 / F0 - 1:+11.1%}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
