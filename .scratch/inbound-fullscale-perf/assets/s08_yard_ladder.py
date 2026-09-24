"""S08 -- the yard-depth growth curve, before and after.

The calltree growth ladder's `yard` rungs (lead 960 -> 0 min, T ~ 2 -> 32) on its
`inbound_gain_pool` config (gain_forecast on both knobs over the rank_labor pool adapter),
one rung per fresh interpreter, with the depth and the plan's work read off the inbound
probe -- which exists on both sides of the comparison (de605170), so the x axis means the
same thing before and after.  (`calltree_growth` itself changed its x from a T(T+1)
inversion to the probe in O1; running the old ladder against the new would compare two
different axes.)

    python .scratch/inbound-fullscale-perf/assets/s08_yard_ladder.py [--mode native|eager]
        [--out results/s08_<tag>.json] [--tag <label>]

`--mode eager` forces the pre-O1 yard ranking on code that has O1 (O3 still on), which
separates the two optimisations' shares.  Run from the snapshot whose code is under test.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))


def _repo():
    return os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))


def one(rung_i: int, mode: str, repo: str) -> dict:
    sys.path.insert(0, repo)
    sys.path.insert(0, os.path.join(repo, 'Tests', 'calltree'))
    import calltree_growth as cg
    import calltree_scenarios as sc
    from Warehouse.kernel import perf_probe
    from Inbound import transit as _transit
    if mode == 'eager':
        if not hasattr(_transit.YardTransit, 'yard_ranking'):
            raise SystemExit('eager mode needs code with O1 (yard_ranking)')
        _transit.YardTransit.yard_ranking = lambda self, ctx: deque(self.yard_order(ctx))
    cfg = cg.CONFIGS['inbound_gain_pool']
    rung = cg._MESO_LADDERS['yard'][rung_i]
    build, run_kw, nb = cg._split_kwargs(dict(cfg.overlay, **rung), 42)
    assets = sc.build_assets(**build)
    perf_probe.drain()
    t0 = time.perf_counter()
    res = sc.run_meso(assets, n_batches=nb, seed=42, **run_kw)
    wall = time.perf_counter() - t0
    spans, counts = perf_probe.drain()
    d = counts.get('inb_drains', 0) or 1
    return {'rung': rung_i, 'lead': rung.get('lead_minutes'), 'mode': mode,
            'T': counts.get('yard_T_sum', 0) / d, 'T_max': counts.get('yard_T_max', 0),
            'drains': counts.get('inb_drains', 0), 'pulls': counts.get('yard_pulls', 0),
            'plan_rounds': counts.get('plan_rounds', 0),
            'plan_places': counts.get('plan_places', 0),
            'yplan_s': spans.get('inb_yplan', 0.0), 'dplan_s': spans.get('inb_dplan', 0.0),
            'reord_s': res.sections['t_reord'], 'wall_s': wall,
            'picks': res.picks, 'placements': res.placements, 'reorders': res.reorders}


def slope(xs, ys):
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 2:
        return float('nan')
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else float('nan')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=('native', 'eager'), default='native')
    ap.add_argument('--tag', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--repo', default='')
    ap.add_argument('--one', type=int, default=-1)
    a = ap.parse_args()
    repo = a.repo or os.getcwd()
    if a.one >= 0:
        print('RESULT ' + json.dumps(one(a.one, a.mode, repo)))
        return
    rows = []
    for i in range(5):
        p = subprocess.run([sys.executable, '-X', 'utf8', os.path.abspath(__file__),
                            '--one', str(i), '--mode', a.mode, '--repo', repo],
                           capture_output=True, text=True, cwd=repo)
        line = [ln for ln in p.stdout.splitlines() if ln.startswith('RESULT ')]
        if not line:
            print(p.stdout[-2000:], p.stderr[-4000:])
            raise SystemExit(f'rung {i} failed')
        r = json.loads(line[0][7:])
        rows.append(r)
        print(f'  lead {r["lead"]:5.0f}  T={r["T"]:5.2f} (max {r["T_max"]:3d})  '
              f'places {r["plan_places"]:7d}  yplan {r["yplan_s"]:7.2f} s  '
              f'reord {r["reord_s"]:7.2f} s  picks {r["picks"]} placements {r["placements"]}',
              flush=True)
    xs = [r['T'] for r in rows]
    fit = {k: slope(xs, [r[k] for r in rows]) for k in ('plan_places', 'yplan_s', 'reord_s')}
    print(f'{a.tag or a.mode}: log-log slope vs T  places {fit["plan_places"]:.2f}  '
          f'yplan {fit["yplan_s"]:.2f}  reord {fit["reord_s"]:.2f}')
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump({'tag': a.tag, 'mode': a.mode, 'rows': rows, 'fit': fit}, f, indent=1)


if __name__ == '__main__':
    main()
