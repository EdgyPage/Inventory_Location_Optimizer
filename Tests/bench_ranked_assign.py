"""bench_ranked_assign.py — perf benchmark for _ranked_assign_impl (lift waves).

Times one ranked-minimizing reorder wave on a synthetic candidate pool at growing
bin counts, and reports the cost of the OLD per-unit idx-union rebuild vs the new
build-once approach.  Demonstrates:
  - Fix 1: union built once per wave, not once per unit (O(U·Σ) -> O(Σ)).
  - Fix 2: candidate pool bucketed once, not re-scanned per unit
           (O(U·bucket_bins) -> O(bucket log bucket + U·n_aisles)).

Not part of the pass/fail suite.  Run:  cd Tests && python bench_ranked_assign.py
"""
from __future__ import annotations

import os
import random
import sys
import time
import types
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Assignment_Functions import build_ranked_minimizing_assignment_fn


class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')
    def __init__(self, aid, bx, by, x_phys, y_phys):
        self.location = (aid, bx, by)
        self.x_phys = x_phys
        self.y_phys = y_phys


def _make_units(n, rng):
    units = []
    for i in range(n):
        c = types.SimpleNamespace(
            sku=i, weight=rng.randint(1, 50),
            demand=types.SimpleNamespace(relative_frequency=rng.random()),
            volume=lambda: 100)
        units.append(types.SimpleNamespace(order=c))
    return units


def _bench(n_bins, n_aisles, n_units, placed_idx_total, rng):
    bins_per_aisle = max(1, n_bins // n_aisles)
    cands = [_Bin(a, i, 0, x_phys=i * 4, y_phys=0)
             for a in range(n_aisles) for i in range(bins_per_aisle)]
    candidates_fn = lambda unit: cands

    # null affinity (co_occur term short-circuits) — we measure the union + pool cost
    aff = types.SimpleNamespace(_matrix=None, _sku_to_idx={})
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=0.5,
                                pick_intercept=1.0, pick_weight_coef=0.5, pick_volume_coef=1e-4)
    aisle_sku_sets   = defaultdict(set)
    aisle_idx_sets   = defaultdict(set)
    aisle_demand_sum = defaultdict(float)
    # pre-seed placed SKU indices spread across aisles (simulates a filled warehouse)
    per = max(1, placed_idx_total // n_aisles)
    k = 0
    for a in range(n_aisles):
        for _ in range(per):
            aisle_idx_sets[a].add(k); k += 1
    freq_by_idx = {i: rng.random() for i in range(k)}

    units = _make_units(n_units, rng)
    fn = build_ranked_minimizing_assignment_fn(
        aff, wp, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum,
        freq_by_idx, freq_by_sku={}, qty_by_sku={}, beta=1.0)

    t0 = time.perf_counter()
    res = fn(units, candidates_fn)
    dt = time.perf_counter() - t0

    placed = sum(1 for _u, b in res if b is not None)
    # projected OLD per-unit union cost: time to build the union once × n_units
    t1 = time.perf_counter()
    _ = set().union(*aisle_idx_sets.values())
    union_once = time.perf_counter() - t1
    old_proj = union_once * n_units

    print(f'  bins={n_bins:>8,} aisles={n_aisles:>5} units={n_units:>5} '
          f'placed_idx={k:>8,} | new={dt*1000:8.1f} ms  placed={placed:>5} | '
          f'old union-rebuild proj ~= {old_proj*1000:9.1f} ms')


if __name__ == '__main__':
    print('\n_ranked_assign_impl benchmark (one ranked-minimizing wave)\n')
    rng = random.Random(1)
    for n_bins, n_aisles in [(30_000, 60), (100_000, 200), (300_000, 600)]:
        _bench(n_bins, n_aisles, n_units=500, placed_idx_total=n_bins // 2, rng=rng)
    print('\nDone.')
