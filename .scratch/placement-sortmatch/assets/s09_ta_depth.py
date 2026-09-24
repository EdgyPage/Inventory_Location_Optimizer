"""S09 feasibility -- how many aisles would an EXACT threshold search visit at a SKU-run
boundary of `_TravelBalancedPool`, against the full rebuild's every live aisle?

At a boundary the pool scores every live aisle
    sc_a = load_a + fq * min_m (pp_m + D_am)   [+ cart_a >= 0]
and heaps them all.  Fagin's threshold algorithm over K + 1 sorted lists -- aisles by
load, and per height bracket m aisles by their head D -- visits aisles in round-robin
list order and can stop once the best exact score seen is strictly below the threshold
    tau = load_(cursor) + fq * min_m (pp_m + D_(cursor, m)),
a lower bound on every unseen aisle (each term is monotone, the cart term is >= 0).
This instrument wraps `take`, and at every boundary counts the aisles TA would visit
before stopping; the pool itself runs unchanged.

    python .scratch/placement-sortmatch/assets/s09_ta_depth.py [--strategy uni_rank_cartlabor_norsl]
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

from Warehouse.kernel.cost_model import per_pick  # noqa: E402
from Warehouse.placement import Assignment_Functions as af  # noqa: E402


def ta_depth(pool, unit):
    """(aisles TA visits, live aisles) for the boundary `unit` opens."""
    c = unit.order
    var = c.handle_var
    fq = pool._fbs.get(c.sku, 0.0) * pool._qbs.get(c.sku, 0.0)
    pp = {}
    heads = {}
    for aid in pool._by_aisle:
        hv = []
        for m, h in pool._by_aisle[aid].items():
            t = h.head
            if t is not None:
                hv.append((m, t[0]))
        if hv:
            heads[aid] = hv
    if not heads:
        return 0, 0
    for aid, hv in heads.items():
        for m, _d in hv:
            if m not in pp:
                pp[m] = per_pick(m, pool._intercept, var, 1, pool._per_item)
    rank = {aid: i for i, aid in enumerate(pool._by_aisle)}

    def score(aid):
        best = min(pp[m] + d for m, d in heads[aid])
        sc = pool._load[aid] + fq * best
        if pool._cart_on:
            add = 0.0 if c.sku in pool._ass[aid] else pool._svp.get(c.sku, 0.0)
            sc += pool._cart_coef * max(0.0, (pool._vol_load[aid] + add) / pool._cap_raw - 1.0)
        return sc

    lists = [sorted(heads, key=lambda a: (pool._load[a], rank[a]))]
    brackets = sorted({m for hv in heads.values() for m, _d in hv})
    for m in brackets:
        lists.append(sorted((a for a in heads if any(mm == m for mm, _ in heads[a])),
                            key=lambda a, m=m: (dict(heads[a])[m], rank[a])))
    pos = [0] * len(lists)
    seen = set()
    best = None
    while True:
        progressed = False
        for li, lst in enumerate(lists):
            if pos[li] < len(lst):
                a = lst[pos[li]]
                pos[li] += 1
                progressed = True
                if a not in seen:
                    seen.add(a)
                    s = (score(a), rank[a])
                    if best is None or s < best:
                        best = s
        if not progressed:
            break
        # threshold from the values at the cursors (the last read in each list)
        lo = lists[0][min(pos[0], len(lists[0])) - 1]
        tau_load = pool._load[lo]
        tau_cost = None
        for li, m in enumerate(brackets, start=1):
            lst = lists[li]
            if not lst:
                continue
            a = lst[min(pos[li], len(lst)) - 1]
            v = pp[m] + dict(heads[a])[m]
            tau_cost = v if tau_cost is None else min(tau_cost, v)
        tau = tau_load + fq * (tau_cost if tau_cost is not None else 0.0)
        if best is not None and best[0] < tau:
            break
    return len(seen), len(heads)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='uni_rank_cartlabor_norsl')
    ap.add_argument('--skus', type=int, default=2_400)
    ap.add_argument('--batches', type=int, default=6)
    a = ap.parse_args()
    from calltree_scenarios import build_assets, run_meso

    depths = []
    real = af._TravelBalancedPool.take

    def take(self, unit):
        if unit.order.sku != self._run_sku:
            depths.append(ta_depth(self, unit))
        return real(self, unit)

    af._TravelBalancedPool.take = take
    try:
        assets = build_assets(n_skus=a.skus, strategy=a.strategy, put_timing=True)
        run_meso(assets, n_batches=a.batches)
    finally:
        af._TravelBalancedPool.take = real
    fr = [s / n for s, n in depths if n]
    live = [n for _s, n in depths if n]
    print(f'{a.strategy} at {a.skus} SKUs: {len(depths)} boundaries, live aisles median '
          f'{st.median(live):.0f}; TA visits median {st.median([s for s, n in depths if n]):.0f} '
          f'({st.median(fr):.1%} of live), p90 {sorted(fr)[int(0.9 * (len(fr) - 1))]:.1%}, '
          f'mean {st.mean(fr):.1%}')


if __name__ == '__main__':
    main()
