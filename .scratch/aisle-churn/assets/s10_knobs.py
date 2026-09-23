"""S10 -- the grid's knob check: what demand density k and first-time confidence c do to the
declared levels, BEFORE any run.  For each (section, k, c): the solved line floor f, the share of
SKUs on the floor, the sum of Q (the stock the warehouse is sized for), and the median cover
Q / d in days -- through the record's own solver (`coverage.solve_floor_lines`) at the pair's
recorded lead.

    python .scratch/aisle-churn/assets/s10_knobs.py <run_root>
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

KS = {'store': (1, 3, 10, 30), 'fulfillment': (1, 3, 10)}
CS = (0.95, 0.80, 0.60)


def main(root):
    from Optimization.simconfig import coverage as cov
    from Optimization.simconfig.staffing import regime_orders
    from Optimization.simdriver.era_coverage import transit_of
    from Warehouse.generation.generate_inventory import load_run_inventory
    spec = json.load(open(os.path.join(root, 'run_spec.json'), encoding='utf-8'))
    cal = list(spec['staffing']['calibration'].values())[0]['coverage']
    lead = cal['lead']
    orders = load_run_inventory(glob.glob(os.path.join(root, '_frozen', '*',
                                                       'planned_inventory.db'))[0]).orders
    print(f'{"section":12s} {"k":>3} {"c":>5}  {"f":>7} {"fill":>7} {"on floor":>9} '
          f'{"sum Q":>10} {"median cover d":>15}')
    for sec, n1 in cal['lines_per_day'].items():
        section = regime_orders(orders, sec)
        for k in KS[sec]:
            for c in CS:
                n = n1 * k
                sol = cov.solve_floor_lines(section, n, coverage_days=cal['coverage_days'],
                                            safety_days=cal['safety_days'],
                                            fill_min=math.sqrt(c), transit=transit_of(lead),
                                            lead_unit_days=float(lead.get('lead_unit_days')
                                                                 or 1.0))
                f = sol['floor_lines']
                tot = sum(o.demand.relative_frequency for o in section)
                sumQ = on = 0
                covers = []
                for o in section:
                    line = o.demand.line
                    Eq = line.mean()
                    d = n * o.demand.relative_frequency / tot * Eq
                    L = cov.line_floor(line, f)
                    Qc = round(cal['coverage_days'] * d)
                    Q = max(L, Qc)
                    on += Qc <= L
                    sumQ += Q
                    covers.append(Q / d if d > 0 else math.inf)
                print(f'{sec:12s} {k:3d} {c:5.2f}  {f:7.4f} {sol["fill_rate"]:7.4f} '
                      f'{on / len(section):9.1%} {sumQ:10,d} {statistics.median(covers):15,.0f}')


if __name__ == '__main__':
    main(sys.argv[1])
