"""O9 sizing -- what one `coverage.fill_rate` evaluation costs on the 400k reference catalogue,
and where the time goes.  The parent's warehouse freeze runs ~25 of these per run (17 in
each line-floor solve, the rest in the stamped fill and its transit curve).

    python .scratch/inbound-fullscale-perf/assets/s06_fill_profile.py [--channel store]
"""
from __future__ import annotations

import argparse
import cProfile
import glob
import os
import pstats
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)

from Optimization.simconfig import coverage as cov  # noqa: E402
from Optimization.simdriver import era_coverage as ec  # noqa: E402
from Optimization.simconfig import staffing  # noqa: E402
from Warehouse.generation.generate_inventory import load_run_inventory  # noqa: E402

LEAD = {'transit_days': 1.7655681878633653, 'provenance': 'derived', 'trailer_type': '53',
        'lead_s': 28800.0, 'lead_sigma': 0.7, 'day_seconds': 28800.0, 'releases_per_day': 1,
        'lead_unit_days': 1.0}
LINES = {'store': 588.6518923, 'fulfillment': 2903.204556}
FLOOR = {'store': 1.3078, 'fulfillment': 1.4994}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', default='store')
    ap.add_argument('--catalogue', default=os.path.join(
        os.environ.get('PROFILE_INPUT_DIR', ''), '..', 'catalogue_reference_lt0'))
    a = ap.parse_args()
    dbs = glob.glob(os.path.join(a.catalogue, '*', '*', 'inventory', '*.db'))
    if not dbs:
        raise SystemExit(f'no inventory DB under {a.catalogue}')
    t0 = time.perf_counter()
    inv = load_run_inventory(dbs[0])
    print(f'loaded {len(inv.orders):,} orders in {time.perf_counter() - t0:.1f}s')
    section = staffing.regime_orders(inv.orders, a.channel)
    transit = ec.transit_of(LEAD)
    n = LINES[a.channel]
    cov.rescale_section(section, n, coverage_days=10.0, safety_days=2.0,
                        floor_lines=FLOOR[a.channel], transit_days=LEAD['transit_days'],
                        lead_unit_days=1.0)
    t0 = time.perf_counter()
    r = cov.fill_rate(section, n, transit=transit, lead_unit_days=1.0)
    print(f'{a.channel}: {len(section):,} SKUs, fill {r["fill_rate"]:.6f}, one evaluation '
          f'{time.perf_counter() - t0:.2f}s')
    prof = cProfile.Profile()
    prof.enable()
    cov.fill_rate(section, n, transit=transit, lead_unit_days=1.0)
    prof.disable()
    pstats.Stats(prof).sort_stats('tottime').print_stats(12)


if __name__ == '__main__':
    main()
