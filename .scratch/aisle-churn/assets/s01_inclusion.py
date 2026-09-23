"""S01 -- the sampler's per-SKU inclusion probability against the line share.

For every cached batch script of a run root (`_batches_*.pkl`, one per channel section), count
how many of its batches ask for each SKU, and compare the realised daily inclusion probability
p_s with two closed forms:

  A. the line share the level record uses:            p_s = n * f_s / sum(f)
  B. successive (weighted, without-replacement) sampling, affinity ignored:
                                                       p_s = 1 - exp(-theta * f_s),
     theta solving sum_s p_s = n  (Rosen's approximation for sequential PPS sampling).

Reported by frequency decile (mean realised vs predicted, and the top/bottom-decile spread),
plus the section totals.  Writes `assets/results/S01_<root>_<section>.csv`.

    python .scratch/aisle-churn/assets/s01_inclusion.py <run_root> [<run_root> ...]
"""
from __future__ import annotations

import csv
import glob
import math
import os
import sys
from collections import Counter

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')


def _theta(freqs: list, n: float) -> float:
    """theta with sum(1 - exp(-theta f)) = n, by bisection (monotone in theta)."""
    lo, hi = 0.0, 1.0
    while sum(1 - math.exp(-hi * f) for f in freqs) < n:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if sum(1 - math.exp(-mid * f) for f in freqs) < n:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def analyse(root: str) -> list:
    from Optimization.simdriver.batch_precompute import read_batches_blob
    from Warehouse.generation.generate_inventory import load_run_inventory
    from Warehouse.kernel.regime import regime_of
    inv_dbs = glob.glob(os.path.join(root, '_frozen', '*', 'planned_inventory.db'))
    if not inv_dbs:
        raise SystemExit(f'{root}: no frozen planned inventory')
    orders = load_run_inventory(inv_dbs[0]).orders
    by_sku = {c.sku: c for c in orders}
    first_cell = sorted(d for d in os.listdir(root)
                        if os.path.isdir(os.path.join(root, d)) and not d.startswith('_'))[0]
    rows_out = []
    for pkl in sorted(glob.glob(os.path.join(root, first_cell, '*', '_batches_*.pkl'))):
        blob = read_batches_blob(pkl)
        if blob is None:
            continue
        batches = blob[1]
        K = len(batches)
        counts = Counter(s for b in batches for s in b.items)
        regimes = Counter(regime_of(by_sku[s]) for s in counts if s in by_sku)
        section = regimes.most_common(1)[0][0]
        sec = [c for c in orders if regime_of(c) == section]
        freqs = [c.demand.relative_frequency for c in sec]
        F = sum(freqs)
        n = sum(len(b.items) for b in batches) / K
        theta = _theta(freqs, n)
        recs = []
        for c in sec:
            f = c.demand.relative_frequency
            recs.append((f, counts.get(c.sku, 0) / K, n * f / F, 1 - math.exp(-theta * f)))
        recs.sort()
        dec = []
        m = len(recs)
        for d in range(10):
            chunk = recs[d * m // 10:(d + 1) * m // 10]
            dec.append(tuple(sum(r[i] for r in chunk) / len(chunk) for i in range(4)))
        tag = os.path.basename(root.rstrip('\\/'))
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f'S01_{tag}_{section}.csv'), 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['decile', 'mean_f', 'p_realised', 'p_line_share', 'p_successive'])
            for d, r in enumerate(dec):
                w.writerow([d + 1, *(f'{x:.6g}' for x in r)])
        print(f'\n{tag} / {section}: {len(sec):,} SKUs, {K} batches, n = {n:.1f} lines/batch, '
              f'theta = {theta:.4g}')
        print(f'  realised sum p = {sum(r[1] for r in recs):.2f}  line-share sum = '
              f'{sum(r[2] for r in recs):.2f}  successive sum = {sum(r[3] for r in recs):.2f}')
        print(f'  {"decile":>6} {"mean f":>8} {"realised":>9} {"share":>9} {"success":>9}')
        for d, r in enumerate(dec):
            print(f'  {d + 1:6d} {r[0]:8.4f} {r[1]:9.5f} {r[2]:9.5f} {r[3]:9.5f}')
        for i, name in ((1, 'realised'), (2, 'line share'), (3, 'successive')):
            print(f'  top/bottom decile spread, {name}: {dec[9][i] / max(dec[0][i], 1e-12):.2f}x')
        rows_out.append((tag, section, dec))
    return rows_out


if __name__ == '__main__':
    for r in sys.argv[1:]:
        analyse(r)
