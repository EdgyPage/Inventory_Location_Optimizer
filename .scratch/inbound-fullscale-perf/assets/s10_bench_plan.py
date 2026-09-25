"""S10 -- one `plan_order` at the campaign shape, through the pool adapter of either phase-2
winner family: the thing the 400k gain cells spend ~85% of their reorder phase in.

The yard is T = 17 trailers of 12 units (the campaign's mean depth).  The tier is 1,400
aisles x 3 brackets x 6 bins split over three size classes; the aisle books are a real
ledger's, holding a 20,000-SKU catalogue dealt ~14 per aisle, so the copy-on-write views,
the ledger inverse and the partner folds all run at the campaign's width.  It started as a
snapshot-side script used between phase-2 rounds; it is kept here so run-to-run numbers in
the S10 whiteboard can be reproduced.

    python .scratch/inbound-fullscale-perf/assets/s10_bench_plan.py [--family travel|minlabor]
        [--reps 2] [--repo <snapshot>] [--profile]

`--repo` imports the code under test from a snapshot (default: this checkout).  Prints the
plan's order (the correctness anchor across snapshots) and its mean wall.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from types import SimpleNamespace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--family', choices=('travel', 'minlabor'), default='travel')
    ap.add_argument('--reps', type=int, default=2)
    ap.add_argument('--repo', default='')
    ap.add_argument('--profile', action='store_true')
    ap.add_argument('--T', type=int, default=17)
    a = ap.parse_args()
    repo = a.repo or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    sys.path.insert(0, repo)
    sys.path.insert(0, os.path.join(repo, 'Tests', 'bench'))

    from Inbound.gain import GainBundle, OneOwnerBundle, plan_order
    from Inbound.space import SpaceView
    from Inbound.trailer import Trailer, Trailer53
    from Optimization.metrics.Workload import WorkloadParams
    from Warehouse.catalog.Demand import Demand
    from Warehouse.catalog.Order import StorageHandleConfig
    from Warehouse.inventory.aisle_ledger import AisleLedger
    from Warehouse.inventory.inventory_common import binkey_of, tier_ranks_for
    from Warehouse.kernel.cost_model import SpeedProfile
    from Warehouse.placement import Assignment_Functions as af
    from bench_pool_open import _Affinity

    # A CAMPAIGN-SHAPED CATALOGUE: at 400k a SKU sits in about one aisle, so the aisle books
    # hold ~14 distinct SKUs each (20,000 dealt over 1,400 aisles) and a partner row lands in
    # a handful of aisles.  A 40-SKU catalogue held whole by every aisle made every partner
    # fold 1,400 wide -- a regime production never enters.
    A, B, PER, T, UPT, N_SKUS, PER_AISLE, PARTNERS = 1400, 3, 6, a.T, 12, 20_000, 14, 5
    wp = WorkloadParams()
    put = SpeedProfile(2.0, 4.0)
    sizes = ('small', 'medium', 'large')
    keys = [('conveyable', 'food', z, 'pallet') for z in sizes]

    class _Bin:
        __slots__ = ('location', 'x_phys', 'y_phys')

        def __init__(self, aid, x, y=0.0):
            self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)

    class _Order:
        __slots__ = ('sku', 'storage_handle_config', 'demand', 'labor_cost', 'handle_var',
                     'expected_popularity', 'expected_labor')

        def __init__(self, sku, freq=1.0, qty_rate=1.0, labor=1.0, hvar=0.5):
            self.sku = sku
            self.storage_handle_config = StorageHandleConfig('conveyable', 'food')
            self.demand = Demand.from_rates(freq, qty_rate)
            self.labor_cost = labor
            self.handle_var = hvar
            self.expected_popularity = freq * qty_rate
            self.expected_labor = self.expected_popularity * labor

    class _Unit:
        __slots__ = ('order', 'quantity', 'storage_size', 'unit_category')

        def __init__(self, order, quantity, size='medium'):
            self.order, self.quantity = order, quantity
            self.storage_size, self.unit_category = size, 'pallet'

    rng = random.Random(11)
    bins = [_Bin(aid, 12.0 * (k % 4), (40.0, 150.0, 300.0)[b])
            for aid in range(1, A + 1) for b in range(B) for k in range(PER)]
    rng.shuffle(bins)
    third = len(bins) // 3
    empties = {keys[0]: tuple(bins[:third]), keys[1]: tuple(bins[third:2 * third]),
               keys[2]: tuple(bins[2 * third:])}
    view = SpaceView(empties=empties, emptied_at={}, predicted={}, released_at=None,
                     versions=(0, 0, 0), frozen_at=0.0, window=None)
    orders = [_Order(s, 0.2 + 0.05 * (s % 7), 1.0 + (s % 5), 1.0 + 0.1 * (s % 3),
                     0.3 + 0.1 * (s % 5))
              for s in range(1, N_SKUS + 1)]
    trailers = []
    for seq in range(T):
        t = Trailer(Trailer53, seq)
        t.arrived_s = 100.0 * seq
        t.pending = [SimpleNamespace(unit=_Unit(rng.choice(orders), rng.randint(1, 20),
                                                rng.choice(sizes)))
                     for _ in range(UPT)]
        t.taken = 0
        trailers.append(t)

    skus = [o.sku for o in orders]
    r2 = random.Random(77)
    aff = _Affinity(skus, [(x, y, 1.5 + r2.random() * 3.0)
                           for x, y in (r2.sample(skus, 2)
                                        for _ in range(PARTNERS * N_SKUS // 2))])
    s2i = aff._sku_to_idx
    # THE BOOKS ARE A REAL LEDGER'S, as the owner's are in production: `idx_sets` is an
    # `_IdxSets` carrying the `partner_aisles` inverse, which `_MinLaborPool._partner_deltas`
    # folds through.  Plain dicts here would force its per-aisle fallback -- a path
    # production never takes -- and the bench would time the wrong thing.
    own = AisleLedger()
    deal = list(orders)
    r2.shuffle(deal)
    for aid in range(1, A + 1):
        held = deal[(aid - 1) * PER_AISLE: aid * PER_AISLE]
        for i, o in enumerate(held):
            own.add_sku(aid, o.sku, s2i[o.sku], demand=o.expected_popularity,
                        pick_load=0.7, vol=100.0)
            own.member_pos[aid][s2i[o.sku]].append(12.0 * (i % 4))
    live = {'aisle_sku_sets': own.sku_sets, 'aisle_idx_sets': own.idx_sets,
            'aisle_demand_sum': own.demand_sum, 'aisle_pick_load_sum': own.pick_load_sum,
            'aisle_vol_sum': own.vol_sum, 'aisle_member_pos': own.member_pos}
    fbs = {o.sku: o.demand.relative_frequency for o in orders}
    qbs = {o.sku: o.demand.quantity_rate for o in orders}
    fbi = {s2i[s]: f for s, f in fbs.items()}
    plp = {o.sku: 0.7 for o in orders}
    svp = {o.sku: 300.0 for o in orders}
    tot_f = sum(fbs.values())
    opens = []

    if a.family == 'travel':
        state_keys = ('aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum',
                      'aisle_pick_load_sum', 'aisle_vol_sum')

        def factory(cands, state, wp_local):
            opens.append(1)
            return af._TravelBalancedPool(
                cands, aff, wp_local, state['aisle_sku_sets'], state['aisle_idx_sets'],
                state['aisle_demand_sum'], state['aisle_pick_load_sum'], plp, fbs, qbs,
                cart=(state['aisle_vol_sum'], svp, 4.0, tot_f))
    else:
        state_keys = ('aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum',
                      'aisle_member_pos')

        def factory(cands, state, wp_local):
            opens.append(1)
            led = AisleLedger.over(**{k[len('aisle_'):]: v for k, v in state.items()})
            return af.build_ranked_minlabor_pool_fn(aff, wp_local, led, fbi, fbs, qbs,
                                                    beta=0.5)(cands)

    bundle = OneOwnerBundle(GainBundle(
        put_speed=put, wp_of=lambda u: wp, binkey_of=binkey_of,
        tier_ranks_for=tier_ranks_for, pool_factory=factory,
        freeze_tier=af.freeze_tier, aisle_state={k: live[k] for k in state_keys}))

    out = plan_order(trailers, bundle, view, predicted=False)
    print(f'{a.family}: tier {len(bins)} bins / {A} aisles, T={T}, {UPT} units/trailer')
    print(f'plan: {[t.seq for t in out]}')
    n0 = len(opens)

    def run():
        for _ in range(a.reps):
            plan_order(trailers, bundle, view, predicted=False)

    t0 = time.perf_counter()
    if a.profile:
        import cProfile
        import pstats
        pr = cProfile.Profile()
        pr.runcall(run)
        pstats.Stats(pr).sort_stats('tottime').print_stats(22)
    else:
        run()
    el = (time.perf_counter() - t0) / a.reps
    print(f'plan_order: {el:8.3f} s   ({(len(opens) - n0) // a.reps} pool opens per plan)')


if __name__ == '__main__':
    main()
