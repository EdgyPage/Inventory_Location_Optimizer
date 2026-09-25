"""S05b -- does O3 pay at the PUT DRAIN's shape, not only the gain evaluator's?

`bench_pool_open.py` times a 12-unit open (the gain evaluator's virtual placement).  Run A
says the put drain opens pools of ~470 units (306,870 units over 650 opens), in runs of
the same SKU.  O3 made a take O(A) (`_TravelVec.pick`'s argmin) and a min-labour take
O(A log A) (`_MinLabVec._rekey`'s argsort) where the heap and the sorted list were
O(log A) and O(A) -- cheap in C at A = 1,400, but paid ~470 times per open.  This times the
eager open + seat at that shape on whichever repo it is pointed at, and prints the emitted
sequence's digest so two repos can be checked for the same placements.

    python .scratch/inbound-fullscale-perf/assets/s05b_put_shape.py --repo <snapshot> [--units 470]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import random
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--units', type=int, default=470)
    ap.add_argument('--skus', type=int, default=120)
    ap.add_argument('--repeats', type=int, default=7)
    a = ap.parse_args()
    sys.path.insert(0, a.repo)
    sys.path.insert(0, os.path.join(a.repo, 'Tests', 'bench'))
    import bench_pool_open as bp

    scene = bp.build_scene(bp.CAMPAIGN, seed=7)
    rng = random.Random(11)
    orders = {u.order.sku: u.order for u in scene.units}
    skus = rng.sample(range(1, scene.shape.catalogue + 1), a.skus)
    proto = scene.units[0]
    units = []
    while len(units) < a.units:
        s = rng.choice(skus)
        o = orders.get(s)
        if o is None:
            o = orders[s] = type(proto.order)(s, 0.5 + (s % 7) * 0.2, 1.0 / (1 + s % 50),
                                              1.0 + s % 9, 10.0 - (s % 5),
                                              1.0 + 0.25 * (s % 4))
        for _ in range(rng.randint(1, 8)):
            units.append(type(proto)(o))
    units = units[:a.units]
    for fam in bp.FAMILIES:
        seq = bp.seat(bp.open_pool(scene, fam, scene.tier.slice(scene.excluded)), units)
        dig = hashlib.blake2b(repr(seq).encode(), digest_size=8).hexdigest()
        cp = bp._time(lambda f=fam: copy.deepcopy(
            scene.state_travel if f == 'travel' else scene.state_minlabor), a.repeats)
        t = bp._time(lambda f=fam: bp.seat(
            bp.open_pool(scene, f, scene.tier.slice(scene.excluded)), units), a.repeats)
        seated = sum(1 for x in seq if x is not None)
        print(f'{fam:9s} units={len(units)} seated={seated} open+seat={1e3 * (t - cp):8.1f} ms '
              f'({1e6 * (t - cp) / max(1, len(units)):6.1f} us/unit)  seq={dig}')


if __name__ == '__main__':
    main()
