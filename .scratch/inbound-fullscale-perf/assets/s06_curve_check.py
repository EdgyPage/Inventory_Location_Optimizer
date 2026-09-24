"""O9 proof -- the fill curve (stamp + twelve transit points) priced with the per-(group,
grid day) memo against without it, on the 400k reference catalogue: every point's float
must be identical, and the wall is what it saves.

    python .scratch/inbound-fullscale-perf/assets/s06_curve_check.py --catalogue <dir> [--channel store]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)

from Optimization.simconfig import coverage as cov  # noqa: E402
from Optimization.simconfig import staffing  # noqa: E402
from Optimization.simdriver import era_coverage as ec  # noqa: E402
from Warehouse.generation.generate_inventory import load_run_inventory  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s06_fill_profile import FLOOR, LEAD, LINES  # noqa: E402


def curve(section, n, memo_on):
    memo = {} if memo_on else None
    t0 = time.perf_counter()
    stamp = cov.fill_rate(section, n, transit=ec.transit_of(LEAD), lead_unit_days=1.0,
                          memo=memo)['fill_rate']
    if memo_on:
        pts = ec.fill_curve(section, n, LEAD, memo=memo)
    else:
        # the unmemoised path, point by point, exactly as fill_curve evaluated it before
        pts, seen = [], set()
        for sc in sorted(set(float(s) for s in ec.FILL_CURVE_SCALES) | {1.0}):
            law = ec.transit_of(LEAD, sc)
            t = float(law['transit_days'])
            if t in seen:
                continue
            seen.add(t)
            pts.append({'transit_days': t, 'fill_rate': float(cov.fill_rate(
                section, n, transit=law, lead_unit_days=1.0)['fill_rate'])})
        pts.sort(key=lambda p: p['transit_days'])
    return stamp, pts, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalogue', required=True)
    ap.add_argument('--channel', default='store')
    a = ap.parse_args()
    db = glob.glob(os.path.join(a.catalogue, '*', '*', 'inventory', '*.db'))[0]
    inv = load_run_inventory(db)
    section = staffing.regime_orders(inv.orders, a.channel)
    n = LINES[a.channel]
    cov.rescale_section(section, n, coverage_days=10.0, safety_days=2.0,
                        floor_lines=FLOOR[a.channel], transit_days=LEAD['transit_days'],
                        lead_unit_days=1.0)
    s0, p0, w0 = curve(section, n, memo_on=False)
    s1, p1, w1 = curve(section, n, memo_on=True)
    same = s0 == s1 and p0 == p1
    print(f'{a.channel}: {len(section):,} SKUs, {len(p0)} curve points')
    print(f'  unmemoised  {w0:7.1f} s   memoised {w1:7.1f} s   speedup {w0 / w1:4.1f}x')
    print(f'  stamp and every point bit-identical: {same}')
    if not same:
        for x, y in zip(p0, p1):
            if x != y:
                print('   DIFF', x, y)


if __name__ == '__main__':
    main()
