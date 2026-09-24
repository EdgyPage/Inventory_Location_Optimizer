"""S00 -- where a finished run's WALL went: parent setup, simulate pool, analyse pool.

Reads `<root>/run.log` (timestamps HH:MM:SS, rolling over midnight) and
`<root>/runtime_metrics.db`, and reports:

  * the simulate stage: first `[pool] simulate` line -> last one, and its lower bound
    max(slowest unit, sum of unit walls / workers) from runtime_metrics (a coupled unit's
    wall is the larger `total_s` of its two leaf rows);
  * the analyse stage: first -> last `[pool] analyse` line;
  * everything before the first simulate line (parent setup: freeze, staffing, batches);
  * the parent's serial `Warehouse : ... (shape only -- analysis ...)` builds.

    python .scratch/inbound-fullscale-perf/assets/s00_stages.py <run_root> [--workers 12]
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from collections import defaultdict

_TS = re.compile(r'^(\d\d):(\d\d):(\d\d)\s')


def _times(path):
    """[(seconds since the first line, line)], unrolling midnight."""
    out, day, last, t0 = [], 0, None, None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = _TS.match(line)
            if not m:
                continue
            s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if last is not None and s < last - 3600:
                day += 86400
            last = s
            s += day
            if t0 is None:
                t0 = s
            out.append((s - t0, line.rstrip()))
    return out


def _units(root):
    con = sqlite3.connect('file:' + os.path.join(root, 'runtime_metrics.db') + '?mode=ro',
                          uri=True)
    by = defaultdict(float)
    for cell, pair, arm, tot in con.execute('select cell, pair, arm, total_s from runtime'):
        k = (cell, pair, arm.split('_', 1)[0] + '_' + arm)   # one unit = one arm pair
        by[(cell, pair, arm[:3])] = max(by[(cell, pair, arm[:3])], tot or 0.0)
    return by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--workers', type=int, default=12)
    a = ap.parse_args()
    lines = _times(os.path.join(a.root, 'run.log'))
    sim = [t for t, l in lines if '[pool] simulate' in l]
    ana = [t for t, l in lines if '[pool] analyse' in l]
    shape = [t for t, l in lines if 'shape only' in l]
    end = lines[-1][0]
    h = lambda s: f'{s / 3600:6.2f} h'
    print(f'run wall                  {h(end)}')
    if sim:
        print(f'parent setup (to 1st sim) {h(sim[0])}')
        print(f'simulate stage            {h(sim[-1] - sim[0])}  ({sim[0] / 3600:.2f} -> {sim[-1] / 3600:.2f} h)')
    if ana:
        print(f'analyse stage             {h(ana[-1] - ana[0])}  ({ana[0] / 3600:.2f} -> {ana[-1] / 3600:.2f} h)')
    if shape:
        gaps = [b - a2 for a2, b in zip(shape, shape[1:]) if 0 < b - a2 < 900]
        print(f'"shape only" builds       {len(shape)}  (median spacing {sorted(gaps)[len(gaps) // 2] / 60 if gaps else 0:.1f} min)')
    u = _units(a.root)
    walls = sorted(u.values(), reverse=True)
    lb = max(walls[0], sum(walls) / a.workers)
    print(f'units                     {len(walls)}; sum {h(sum(walls))}; slowest {h(walls[0])}; '
          f'bound max(slowest, sum/{a.workers}) = {h(lb)}')
    if sim:
        print(f'simulate stage / bound    {(sim[-1] - sim[0]) / lb:.2f}x')


if __name__ == '__main__':
    main()
