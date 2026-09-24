"""S07 speed -- one production placement group at campaign shape, per pool family.

The production drain (`IM._stock_ranked`) opens one pool per BinKey group over the live
candidate LIST and seats the group.  This times exactly that -- open + (prepare) + seat
the load -- on `Tests/bench/bench_pool_open.py`'s campaign scene (1,400 aisles x 3
brackets x 6 bins, a 12-unit load of mostly distinct SKUs, ~10% of bins excluded), for:

  travel     `_TravelBalancedPool` with the cart term   (rank_cartlabor, store winner)
  minlabor   `_MinLaborPool`                            (rank_minlabor, ff winner)
  tmin       `_RankedAssignPool`, travel-only key       (tmin)
  sortmatch  `_SortMatchPool`, prepare + take           (rank_sortmatch)

    python .scratch/placement-sortmatch/assets/s07_pool_bench.py [--repeats 20] [--units 12]
"""
from __future__ import annotations

import argparse
import copy
import os
import statistics
import sys
import time
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, 'Tests', 'bench'))

import bench_pool_open as bpo  # noqa: E402
from Warehouse.catalog.Demand import Demand  # noqa: E402
from Warehouse.inventory.aisle_ledger import AisleLedger  # noqa: E402
from Warehouse.placement import Assignment_Functions as af  # noqa: E402


class _SMOrder:
    """The bench order plus what the sort-match key reads: a line law."""
    __slots__ = ('sku', 'handle_var', 'demand', 'expected_labor', 'labor_cost')

    def __init__(self, o):
        self.sku, self.handle_var = o.sku, o.handle_var
        self.demand = Demand.from_rates(o.demand.relative_frequency,
                                        max(1.0, o.demand.quantity_rate * 10))
        self.expected_labor, self.labor_cost = o.expected_labor, o.labor_cost


class _SMUnit:
    __slots__ = ('order', 'quantity')

    def __init__(self, o, q):
        self.order, self.quantity = o, q


def _time(fn, repeats):
    fn()
    xs = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - t0)
    return statistics.median(xs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repeats', type=int, default=20)
    ap.add_argument('--units', type=int, default=12)
    a = ap.parse_args()
    shape = bpo.Shape(aisles=1400, bins_per_bracket=6, units=a.units, catalogue=4000)
    sc = bpo.build_scene(shape)
    cands = [b for b in sc.bins if id(b) not in sc.excluded]
    units = sc.units

    def run_family(fam):
        pool = bpo.open_pool(sc, fam, list(cands), cart=(fam == 'travel'))
        bpo.seat(pool, units)

    def run_tmin():
        st = copy.deepcopy(sc.state_travel)
        pool = af._RankedAssignPool(list(cands), sc.affinity, sc.wp, st['aisle_sku_sets'],
                                    st['aisle_idx_sets'], st['aisle_demand_sum'],
                                    sc.freq_by_idx, sc.freq_by_sku, sc.qty_by_sku, 1.0, True)
        bpo.seat(pool, units)

    sm_units = [_SMUnit(_SMOrder(u.order), 10 + (u.order.sku % 30)) for u in units]
    orders = [u.order for u in sm_units]
    hbar = af.sortmatch_hbar(orders, sc.wp)

    def run_sortmatch():
        st = copy.deepcopy(sc.state_travel)
        led = SimpleNamespace(sku_sets=st['aisle_sku_sets'], idx_sets=st['aisle_idx_sets'],
                              demand_sum=st['aisle_demand_sum'])
        fn = af.build_sortmatch_pool_fn(sc.affinity, sc.wp, led, sc.freq_by_idx,
                                        sc.freq_by_sku, sc.qty_by_sku, hbar)
        pool = fn(list(cands))
        pool.prepare(sm_units)
        for u in sm_units:                       # arrival order: the k_cap = 1 case
            pool.take(u)

    def state_copy():
        copy.deepcopy(sc.state_travel)

    copy_s = _time(state_copy, a.repeats)
    rows = [('travel (rank_cartlabor)', _time(lambda: run_family('travel'), a.repeats)),
            ('minlabor (rank_minlabor)', _time(lambda: run_family('minlabor'), a.repeats)),
            ('tmin', _time(run_tmin, a.repeats)),
            ('sortmatch (rank_sortmatch)', _time(run_sortmatch, a.repeats))]
    print(f'campaign shape: {len(cands):,} candidate bins, {shape.aisles} aisles, '
          f'{len(units)} units per group; state deep-copy {1e3 * copy_s:.1f} ms subtracted')
    sm = rows[-1][1] - copy_s
    for name, s in rows:
        net = s - copy_s
        print(f'  {name:28s} {1e3 * net:8.1f} ms per group   '
              f'{net / sm:5.1f}x sortmatch')


if __name__ == '__main__':
    main()
