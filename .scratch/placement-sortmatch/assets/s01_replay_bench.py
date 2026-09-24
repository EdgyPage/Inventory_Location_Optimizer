"""S01 -- time `plan_order` on a merge bundle: the set path vs the frontier replay.

A campaign-shaped yard: T trailers of U units each, spread over a few BinKeys whose tiers
hold `bins` empty bins apiece, plus a predicted tier.  Both paths run on the same scene;
the orders are asserted equal so a timing is never of a wrong answer.

    python .scratch/placement-sortmatch/assets/s01_replay_bench.py [--T 4 8 17 25]
        [--bins 1000 4000 16000] [--units 160] [--repeats 3] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from Inbound.gain import plan_order  # noqa: E402

from Tests.unit.test_gain_plan import (  # noqa: E402
    _Bin, _Order, _Unit, _bundle, _trailer, _view)

_KEYS = [('conveyable', cat, size, 'pallet')
         for cat in ('food', 'dry', 'chem') for size in ('medium', 'large')]


def scene(T, bins, units, seed=0):
    rng = random.Random(seed)
    orders = [_Order(sku=i + 1, freq=rng.uniform(0.01, 1.0), qty_rate=rng.uniform(1, 8),
                     labor=rng.uniform(0.5, 3.0), hvar=rng.uniform(0.1, 1.0),
                     category=rng.choice(('food', 'dry', 'chem')))
              for i in range(4000)]
    empties = {k: [_Bin(rng.randrange(1400), rng.uniform(0, 2400),
                        rng.choice((40.0, 150.0, 300.0))) for _ in range(bins)]
               for k in _KEYS}
    predicted = {k: [_Bin(rng.randrange(1400), rng.uniform(0, 2400), 40.0)
                     for _ in range(bins // 10)] for k in _KEYS}
    trailers = [_trailer(s, 100.0 * s,
                         [_Unit(rng.choice(orders), rng.randint(1, 40),
                                size=rng.choice(('medium', 'medium', 'large')))
                          for _ in range(units)])
                for s in range(T)]
    return trailers, _view(empties, predicted=predicted)


def timed(fn, repeats):
    walls = []
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        walls.append(time.perf_counter() - t0)
    return st.median(walls), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--T', type=int, nargs='+', default=[4, 8, 17, 25])
    ap.add_argument('--bins', type=int, nargs='+', default=[1000, 4000, 16000])
    ap.add_argument('--units', type=int, default=160)
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--json')
    a = ap.parse_args()
    rows = []
    print(f'{"T":>3} {"bins":>6} {"set s":>8} {"replay s":>9} {"speedup":>8}')
    for bins in a.bins:
        for T in a.T:
            trailers, view = scene(T, bins, a.units)
            for predicted in (True,):
                ws, o1 = timed(lambda: plan_order(trailers, _bundle(), view,
                                                  predicted=predicted, replay=False),
                               a.repeats)
                wr, o2 = timed(lambda: plan_order(trailers, _bundle(), view,
                                                  predicted=predicted, replay=True),
                               a.repeats)
                assert [t.seq for t in o1] == [t.seq for t in o2], 'orders differ'
                rows.append(dict(T=T, bins=bins, units=a.units, predicted=predicted,
                                 set_s=ws, replay_s=wr, speedup=ws / wr))
                print(f'{T:>3} {bins:>6} {ws:>8.3f} {wr:>9.4f} {ws / wr:>7.1f}x',
                      flush=True)
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(rows, fh, indent=1)


if __name__ == '__main__':
    main()
