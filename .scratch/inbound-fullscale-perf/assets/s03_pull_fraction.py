"""S03 -- how much of the yard ranking does a drain consume?  (O1's ceiling.)

`plan_order` ranks EVERY standing trailer (T rounds of a greedy that costs 2 x remaining
placements each), but a drain stages only as many as there are free doors plus the refills
its unload reaches.  Drives the calltree yard ladder's deepest rung in process and reads the
inbound probe per drain: T at the freeze and the trailers pulled off the ranking.

    python .scratch/inbound-fullscale-perf/assets/s03_pull_fraction.py [--policy gain_myopic]
        [--strategy uni_rank_labor_norsl] [--batches 10]
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, 'Tests', 'calltree'))

import Inbound.receiving as receiving  # noqa: E402
from Warehouse.kernel import perf_probe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy', default='gain_myopic')
    ap.add_argument('--strategy', default='uni_rank_labor_norsl')
    ap.add_argument('--batches', type=int, default=10)
    ap.add_argument('--doors', type=int, default=1)
    a = ap.parse_args()
    from calltree_scenarios import build_assets, run_meso

    per_drain = []
    real = receiving.SiteReceiving.receive

    def receive(self, leaves, deadline):
        perf_probe.drain()
        out = real(self, leaves, deadline)
        _t, c = perf_probe.drain()
        per_drain.append((c.get('yard_T_sum', 0), c.get('yard_pulls', 0),
                          c.get('plan_rounds', 0), c.get('plan_places', 0)))
        return out

    receiving.SiteReceiving.receive = receive
    try:
        assets = build_assets(n_skus=2_400, bins_per_aisle=100, coverage=10.0, safety=2.0,
                              inbound=True, trailer_type='53', dock_doors=a.doors,
                              door_team=1, recv_crew=2, put_timing=True, lead_minutes=0.0,
                              lead_spread=0.0, yard_policy=a.policy, dock_policy=a.policy,
                              strategy=a.strategy)
        run_meso(assets, n_batches=a.batches, recv_deadline=80.0)
    finally:
        receiving.SiteReceiving.receive = real
    deep = [d for d in per_drain if d[0] > 1]
    T = sum(d[0] for d in deep)
    P = sum(d[1] for d in deep)
    print(f'{a.strategy} / {a.policy}, {a.doors} door(s): {len(per_drain)} drains, '
          f'{len(deep)} with T > 1')
    for d in deep:
        t, p, r, pl = d
        full = t * (t + 1)
        # rounds a lazy plan needs = pulls (one round per trailer handed out); placements a
        # lazy plan pays: sum over the first p rounds of 2 x remaining = p(2t - p + 1)
        lazy = p * (2 * t - p + 1)
        print(f'  T={t:3d} pulled={p:3d}  ({p / t:5.1%})  eager places {full:5d}  '
              f'lazy {lazy:5d}  ({lazy / full:5.1%})')
    if T:
        print(f'overall pulled {P}/{T} = {P / T:.1%} of the ranked trailers')


if __name__ == '__main__':
    main()
