"""S09 feasibility -- how deep does `_MinLaborPool`'s prefix walk go?

At a SKU-run boundary the pool computes every live aisle's best-bracket cost and SORTS
them (O(A K + A log A)); each take then walks that order until the affinity prune fires.
If the walk is short, a LAZY K-way merge of per-bracket head lists (sorted once, SKU-
independent) would produce the same prefix without the full rebuild.  This counts, per
take, the entries the walk reads against the live aisle count.

    python .scratch/placement-sortmatch/assets/s09_walk_depth.py [--strategy uni_rank_minlabor_norsl]
        [--skus 2400] [--batches 6]
"""
from __future__ import annotations

import argparse
import os
import statistics as st
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, 'Tests', 'calltree'))

from Warehouse.placement import Assignment_Functions as af  # noqa: E402


class _CountingList(list):
    """`sel` with its iteration counted: the walk is `for ent in sel`."""
    reads = 0

    def __iter__(self):
        for x in list.__iter__(self):
            _CountingList.reads += 1
            yield x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='uni_rank_minlabor_norsl')
    ap.add_argument('--skus', type=int, default=2_400)
    ap.add_argument('--batches', type=int, default=6)
    a = ap.parse_args()
    from calltree_scenarios import build_assets, run_meso

    depth = []
    real = af._MinLaborPool.take

    def take(self, unit):
        boundary = unit.order.sku != self._last_sku
        before = _CountingList.reads
        out = real(self, unit)
        if boundary and not isinstance(self._sel, _CountingList):
            self._sel = _CountingList(self._sel)
            # first take of the run walked the plain list: re-derive its depth is not
            # possible after the fact, so count from the next take onward
        elif isinstance(self._sel, _CountingList):
            depth.append((_CountingList.reads - before, len(self._sel), boundary))
        return out

    af._MinLaborPool.take = take
    try:
        assets = build_assets(n_skus=a.skus, strategy=a.strategy, put_timing=True)
        run_meso(assets, n_batches=a.batches)
    finally:
        af._MinLaborPool.take = real
    fr = [d / n for d, n, _b in depth if n]
    print(f'{a.strategy} at {a.skus} SKUs: {len(depth)} takes measured; live aisles median '
          f'{st.median([n for _d, n, _b in depth]):.0f}; walk reads median '
          f'{st.median([d for d, _n, _b in depth]):.0f} ({st.median(fr):.1%} of live), '
          f'mean {st.mean(fr):.1%}, p90 {sorted(fr)[int(0.9 * (len(fr) - 1))]:.1%}')


if __name__ == '__main__':
    main()
