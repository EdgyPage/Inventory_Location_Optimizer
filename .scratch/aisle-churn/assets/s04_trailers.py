"""S04c -- trailers per day in closed form, against the site yard.

The site's one transit (coupled channels share it) loads every fired unit FIFO, next-fit, onto
48-inch-cube load pallets, 26 positions to a 53-foot trailer, and the open trailer departs at
every daily release whether full or not (`Inbound/transit.py:_load`, `release`).  So

    pallets/day  = expected_swaps(V_d, E[v], E[v^2], 48^3)      (next-fit renewal, unit-weighted)
    trailers/day = pallets/day / 26 + 1/2                       (the release's partial trailer)

with V_d the day's shipped volume.  Fed the REALISED shipped units (reorder placements, which
is what arrived) so the test is the packing law alone, not the demand.

    python .scratch/aisle-churn/assets/s04_trailers.py <grid_runs.json> [<tag> ...]
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


def main(book, tags=None):
    from Optimization.simconfig.models import inbound
    from Warehouse.generation.generate_inventory import load_run_inventory
    runs = json.load(open(book, encoding='utf-8'))
    print(f'{"point":9s} {"V/day (ft3)":>12} {"E[v] in3":>9} {"pred trailers/d":>16} {"measured":>9}')
    for tag, r in runs.items():
        if (tags and tag not in tags) or r['n_batches'] != 40:
            continue
        root = r['root']
        vol = {o.sku: o.volume() for o in load_run_inventory(glob.glob(os.path.join(
            root, '_frozen', '*', 'planned_inventory.db'))[0]).orders}
        units = []                                   # (qty, unit volume) shipped, days 5-39
        for ch in ('store', 'fulfillment'):
            db = glob.glob(f'{root}/k1_off_fifo/*/*/{ch}/sim_uni_fifo_norsl.db')[0]
            con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
            units += [(q, vol[s]) for s, q in con.execute(
                "select sku, qty from bin_placement where cause = 'reorder' and "
                "batch_id between 5 and 39")]
        pred = inbound.trailers_per_day(units, days=35)
        site = sorted(glob.glob(f'{root}/k1_off_fifo/*/_site/*.db'))[0]
        con = sqlite3.connect('file:' + site + '?mode=ro', uri=True)
        # trailers DISPATCHED in days 5-39: arrivals shifted by the median lead, so count by
        # arrival in days 6-40 (lead 8 h = one site day)
        meas = con.execute('select count(*) from yard_trailers where arrived_s >= ? and '
                           'arrived_s < ?', (6 * 28_800, 41 * 28_800)).fetchone()[0] / 35
        print(f'{tag:9s} {pred["volume_per_day"] / 1728:12,.0f} {pred["e_v"]:9,.0f} '
              f'{pred["trailers_per_day"]:16.1f} {meas:9.1f}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:] or None)
