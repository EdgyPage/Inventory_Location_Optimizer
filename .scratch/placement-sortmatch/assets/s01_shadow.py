"""S01 run-level shadow check -- every live plan of a deep yard, priced both ways.

Drives the calltree yard ladder's deepest rung (`Tests/calltree/calltree_growth.py`
'yard': 2,400 SKUs, one door, one-person door team, zero lead, receiving whistle 80 s)
with a MERGE arm under a gain policy, in process, and wraps `Inbound.gain.plan_order`
so that every plan the simulation asks for is ALSO priced on the set path; any
difference in order or trace raises.  The simulation itself runs on the replay, so the
state every later plan sees is the replay's.

    python .scratch/placement-sortmatch/assets/s01_shadow.py [--strategy uni_tmin_norsl]
        [--policy gain_forecast] [--batches 10]
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, 'Tests', 'calltree'))

import Inbound.gain as gain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='uni_tmin_norsl')
    ap.add_argument('--policy', default='gain_forecast')
    ap.add_argument('--batches', type=int, default=10)
    ap.add_argument('--skus', type=int, default=2_400)
    a = ap.parse_args()

    from calltree_scenarios import build_assets, run_meso

    real = gain.plan_order
    stats = collections.Counter()
    walls = collections.defaultdict(float)

    def shadow(candidates, bundle, space, **kw):
        kw.pop('replay', None)
        t0 = time.perf_counter()
        tr_r = [] if kw.get('trace') is None else None
        kw_r = dict(kw, trace=tr_r if kw.get('trace') is None else kw['trace'])
        got = real(candidates, bundle, space, replay=True, **kw_r)
        walls['replay'] += time.perf_counter() - t0
        if kw.get('_ev') is None:
            t0 = time.perf_counter()
            tr_s: list = []
            want = real(candidates, bundle, space, replay=False,
                        **dict(kw, trace=tr_s))
            walls['set'] += time.perf_counter() - t0
            if [id(t) for t in got] != [id(t) for t in want] or (
                    tr_r is not None and tr_r != tr_s):
                raise AssertionError(f'replay diverged at T={len(candidates)}')
            stats['compared'] += 1
            stats[f'T={len(candidates)}'] += 1
        return got

    gain.plan_order = shadow
    try:
        assets = build_assets(n_skus=a.skus, bins_per_aisle=100, coverage=10.0,
                              safety=2.0, inbound=True, trailer_type='53', dock_doors=1,
                              door_team=1, recv_crew=2, put_timing=True,
                              lead_minutes=0.0, lead_spread=0.0,
                              yard_policy=a.policy, dock_policy=a.policy,
                              strategy=a.strategy)
        run_meso(assets, n_batches=a.batches, recv_deadline=80.0)
    finally:
        gain.plan_order = real
    Ts = sorted((int(k[2:]), v) for k, v in stats.items() if k.startswith('T='))
    deep = sum(v for t, v in Ts if t > 1)
    print(f'{a.strategy} / {a.policy}: {stats["compared"]} plans compared, all equal; '
          f'{deep} with T > 1; T histogram {Ts}')
    print(f'plan_order wall: replay {walls["replay"]:.2f} s, set {walls["set"]:.2f} s '
          f'({walls["set"] / max(walls["replay"], 1e-9):.1f}x)')


if __name__ == '__main__':
    main()
