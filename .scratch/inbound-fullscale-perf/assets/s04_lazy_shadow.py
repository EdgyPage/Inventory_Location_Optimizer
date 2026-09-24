"""S04 -- O1's proof and its price.  The lazy yard plan must pull EXACTLY the prefix of the
eager plan it replaced, drain by drain, and must pay only for what it pulls.

Three modes over the calltree yard ladder's deep rung (`s03_pull_fraction.py`'s scenario):

  shadow  every `YardTransit.yard_ranking` also computes the EAGER order (`yard_order`) on a
          fresh gain cache -- so a lazy plan that leaned on a cache the eager one built
          first could not hide -- and every pull is asserted equal to the eager order's
          next entry.  Prints drains checked, pulls checked, mismatches (must be 0).
  lazy    HEAD as it runs.
  eager   `yard_ranking` forced back to `deque(yard_order(ctx))`, the pre-O1 behaviour.

`lazy` and `eager` print the run's outcome (picks, placements, reorders) -- which must
agree -- and the yard plan's placements and wall, which is the saving.

    python .scratch/inbound-fullscale-perf/assets/s04_lazy_shadow.py --mode shadow
        [--policy gain_myopic] [--door-fill drain|asap] [--doors 1] [--batches 10]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, 'Tests', 'calltree'))

from Inbound import transit as _transit  # noqa: E402
from Inbound.priorities import LazyRanking  # noqa: E402
from Warehouse.kernel import perf_probe  # noqa: E402


class _Shadowed:
    """A pull queue whose every pull is checked against the eager order."""

    def __init__(self, inner, eager, stats):
        self.inner, self.eager, self.i, self.stats = inner, eager, 0, stats

    def __len__(self):
        return len(self.inner)

    def __bool__(self):
        return bool(self.inner)

    def popleft(self):
        t = self.inner.popleft()
        want = self.eager[self.i]
        self.stats['pulls'] += 1
        if t is not want:
            self.stats['mismatch'] += 1
            print(f'  MISMATCH at pull {self.i}: lazy seq {t.seq}, eager seq {want.seq}')
        self.i += 1
        return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=('shadow', 'lazy', 'eager'), default='shadow')
    ap.add_argument('--policy', default='gain_myopic')
    ap.add_argument('--strategy', default='uni_rank_labor_norsl')
    ap.add_argument('--door-fill', choices=('drain', 'asap'), default='drain')
    ap.add_argument('--batches', type=int, default=10)
    ap.add_argument('--doors', type=int, default=1)
    a = ap.parse_args()
    from calltree_scenarios import build_assets, run_meso

    stats = {'drains': 0, 'pulls': 0, 'mismatch': 0, 'lazy_objs': 0}
    real = _transit.YardTransit.yard_ranking

    def shadow(self, ctx):
        saved = ctx.gain_cache
        ctx.gain_cache = None if saved is None else {}
        try:
            eager = self.yard_order(ctx)
        finally:
            ctx.gain_cache = saved
        out = real(self, ctx)
        stats['drains'] += 1
        stats['lazy_objs'] += isinstance(out, LazyRanking)
        return _Shadowed(out, eager, stats)

    def eager(self, ctx):
        return deque(self.yard_order(ctx))

    if a.mode == 'shadow':
        _transit.YardTransit.yard_ranking = shadow
    elif a.mode == 'eager':
        _transit.YardTransit.yard_ranking = eager
    try:
        assets = build_assets(n_skus=2_400, bins_per_aisle=100, coverage=10.0, safety=2.0,
                              inbound=True, trailer_type='53', dock_doors=a.doors,
                              door_team=1, recv_crew=2, put_timing=True, lead_minutes=0.0,
                              lead_spread=0.0, yard_policy=a.policy, dock_policy=a.policy,
                              strategy=a.strategy)
        if a.door_fill == 'asap':
            assets.mgr.transit.door_fill = 'asap'
        perf_probe.drain()
        t0 = time.perf_counter()
        res = run_meso(assets, n_batches=a.batches, recv_deadline=80.0)
        wall = time.perf_counter() - t0
        spans, counts = perf_probe.drain()
    finally:
        _transit.YardTransit.yard_ranking = real
    print(f'{a.mode}: {a.strategy} / {a.policy}, {a.doors} door(s), fill {a.door_fill}, '
          f'{a.batches} batches')
    print(f'  outcome   picks={res.picks} placements={res.placements} '
          f'reorders={res.reorders} skipped={res.skipped}')
    print(f'  yard plan {spans.get("inb_yplan", 0.0):8.2f} s   '
          f'dock plan {spans.get("inb_dplan", 0.0):6.2f} s   '
          f'rounds {counts.get("plan_rounds", 0):6d}   '
          f'places {counts.get("plan_places", 0):8d}   '
          f'T_sum {counts.get("yard_T_sum", 0)}  pulls {counts.get("yard_pulls", 0)}')
    print(f'  t_reord   {res.sections["t_reord"]:8.2f} s   wall {wall:8.2f} s')
    if a.mode == 'shadow':
        print(f'  shadow: {stats["drains"]} rankings ({stats["lazy_objs"]} lazy), '
              f'{stats["pulls"]} pulls checked, {stats["mismatch"]} MISMATCHES')
        if stats['mismatch']:
            sys.exit(1)


if __name__ == '__main__':
    main()
