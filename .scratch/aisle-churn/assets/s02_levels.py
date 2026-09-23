"""S02 -- the LEVELS model against every SKU a frozen inventory declared.

For each run root: load the frozen planned inventory, read the declaration's own inputs off
`run_spec.json` (lines per day and solved floor lines per section, the transit law, coverage
and safety days), evaluate `Optimization.simconfig.models.levels.LEVELS` per SKU, and compare
Q, rp and P with the stamped `equilibrium_qty`, `reorder_point` and `pipeline_qty`.  Then
describe what the declaration IS: the share of SKUs on the floor, the days of cover Q/d it
implies, and where the units come from.

    python .scratch/aisle-churn/assets/s02_levels.py <run_root> [<run_root> ...]
"""
from __future__ import annotations

import glob
import json
import os
import statistics as st
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _q(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(p * (len(xs) - 1)))))]


def analyse(root: str) -> None:
    from Optimization.simconfig.models.levels import section
    from Optimization.simconfig.staffing import regime_orders
    from Warehouse.generation.generate_inventory import load_run_inventory
    spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
    (pair, cal), = spec['staffing']['calibration'].items()
    cov = cal['coverage']
    inv = glob.glob(os.path.join(root, '_frozen', '*', 'planned_inventory.db'))[0]
    orders = load_run_inventory(inv).orders
    tag = os.path.basename(root.rstrip('\\/'))
    print(f'\n{tag}  ({pair})  C = {cov["coverage_days"]} d, safety = {cov["safety_days"]} d, '
          f'transit = {cov["lead"]["transit_days"]:.4f} d')
    for ch, regime in (('store', 'store'), ('fulfillment', 'fulfillment')):
        sec = regime_orders(orders, regime)
        if not sec:
            continue
        rows = section(sec, lines_per_day=cov['lines_per_day'][ch],
                       floor_lines=cov['floor'][ch]['floor_lines'],
                       transit_days=cov['lead']['transit_days'],
                       lead_unit_days=cov['lead'].get('lead_unit_days', 1.0),
                       coverage_days=cov['coverage_days'], safety_days=cov['safety_days'])
        mis = {'Q': 0, 'rp': 0, 'P': 0}
        cover, units, onf, Ls = [], 0, 0, []
        for c, r in rows:
            mis['Q'] += int(r['Q'] != c.equilibrium_qty)
            mis['rp'] += int(r['rp'] != c.reorder_point)
            mis['P'] += int(r['P'] != (getattr(c, 'pipeline_qty', 0) or 0))
            units += r['Q']
            onf += int(r['on_floor'])
            Ls.append(r['L'])
            if r['d'] > 0:
                cover.append(r['Q'] / r['d'])
        n = len(rows)
        print(f'  {ch}: {n:,} SKUs, {cov["lines_per_day"][ch]:.1f} lines/day, floor f = '
              f'{cov["floor"][ch]["floor_lines"]}')
        print(f'    model vs stamped: Q {n - mis["Q"]:,}/{n:,} equal, rp {n - mis["rp"]:,}/{n:,}, '
              f'P {n - mis["P"]:,}/{n:,}')
        print(f'    on the floor (Q = L, base stock): {onf / n:.2%}; units declared {units:,}; '
              f'mean Q {units / n:.2f}; L median {st.median(Ls)}')
        print(f'    days of cover Q/d: p10 {_q(cover, .1):,.0f}  median {_q(cover, .5):,.0f}  '
              f'p90 {_q(cover, .9):,.0f}  (C = {cov["coverage_days"]})')


if __name__ == '__main__':
    for r in sys.argv[1:]:
        analyse(r)
