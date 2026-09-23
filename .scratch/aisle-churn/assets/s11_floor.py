"""S11b -- the noise floor in closed form against the bootstrap it replaces.

`run_unload_ranking.measured_floor` takes a moving-block bootstrap of the paired per-batch
relative difference between two cells.  The closed form (`models.churn.noise_floor`) is
z sigma sqrt(tau / n): sigma the sd of the per-batch difference, tau its integrated
autocorrelation (Geyer's initial positive sequence).  Compared pair by pair over the grid's
lifo-vs-fifo readings (the order question's null, where the floor IS the answer).

    python .scratch/aisle-churn/assets/s11_floor.py <grid_runs.json>
"""
from __future__ import annotations

import json
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def main(book):
    from Optimization.run_unload_ranking import measured_floor
    from Optimization.simconfig.models import churn
    from s11_measure import _dbs, _series
    runs = json.load(open(book, encoding='utf-8'))
    ratios = []
    print(f'{"point":9s} {"pair":44s} {"sigma %":>8} {"tau":>5} {"closed form":>12} {"bootstrap":>10}')
    for tag, r in runs.items():
        if r['n_batches'] != 40:
            continue
        dbs = _dbs(r['root'])
        for (cell, ch, arm), db in sorted(dbs.items()):
            if cell != 'k1_off_lifo' or arm.startswith('opt_fifo'):
                continue
            a, b = _series(db, 5, 40), _series(dbs[('k1_off_fifo', ch, arm)], 5, 40)
            nf = churn.noise_floor(a, b)
            ci = measured_floor({'ref': b, 'x': a}, 'ref')['cells']['x']['ci_pct']
            boot = (ci[1] - ci[0]) / 2.0
            ratios.append(nf['floor'] / boot)
            print(f'{tag:9s} {ch + "/" + arm:44s} {nf["sigma"]:8.2f} {nf["tau"]:5.2f} '
                  f'{nf["floor"]:12.3f} {boot:10.3f}')
    print(f'closed form / bootstrap: median {statistics.median(ratios):.2f}, '
          f'range {min(ratios):.2f}-{max(ratios):.2f} over {len(ratios)} pairs')


if __name__ == '__main__':
    main(sys.argv[1])
