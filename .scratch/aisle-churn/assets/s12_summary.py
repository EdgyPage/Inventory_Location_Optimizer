"""S12 -- the (k, c) grid against its registered predictions (S10).

Reads the grid book (`grid_logs/grid_runs.json` written by `s11_grid.py`), measures every
finished point with `s11_measure.main` (cached as `results/S11_<tag>.json`), and prints:

  P1  fresh-bin share, measured (mean over the reference cell's arms, units) vs predicted (lines)
  P3  store placement gap (uni rank vs uni fifo) per cell vs the registered scaling
      gap_k = gap_1 * [phi(k)/phi(1)] * [dM_k/dM_1], anchored on the grid's own k = 1 at the
      same c (the S10 registration)
  P4  unloading-order gaps (lifo vs fifo, 8 arms): how many 95% intervals exclude zero
  P5  initial layout (opt rank vs uni rank), store, vs gap_1 * (1 - phi(k)) / (1 - phi(1))
  P6  the largest free-pool drift over the window

and draws the predicted-vs-realised figures into `results/`.

    python .scratch/aisle-churn/assets/s12_summary.py <grid_runs.json>
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

RES = os.path.join(_HERE, 'results')
# S10's registered P1 (lines), by section and k
P1 = {'store': {1: 0.0777, 3: 0.2034, 10: 0.4587, 30: 0.6930},
      'fulfillment': {1: 0.2657, 3: 0.5446, 10: 0.7976}}
TAU, DM_STAR, DM_AMP, H = 22.0, -0.023, 0.20, 40.0


def dM(k: float) -> float:
    """S10's registered mean height deficiency over a window compressed k-fold."""
    return DM_STAR - DM_AMP * TAU / (H * k) * (1.0 - math.exp(-H * k / TAU))


def p3_ratio(k: int) -> float:
    return (P1['store'][k] / P1['store'][1]) * (dM(k) / dM(1))


def p5_ratio(k: int) -> float:
    return (1.0 - P1['store'][k]) / (1.0 - P1['store'][1])


def measured(tag: str, root: str, nb: int) -> dict:
    import contextlib
    import io
    from s11_measure import main as measure
    path = os.path.join(RES, f'S11_{tag}.json')
    if not os.path.exists(path):
        with contextlib.redirect_stdout(io.StringIO()):
            measure(root, 0, min(nb, 40), out=path)
    return json.load(open(path, encoding='utf-8'))


def main(book):
    from Optimization.Performance_Evaluations.closed_form import render as draw
    runs = json.load(open(book, encoding='utf-8'))
    os.makedirs(RES, exist_ok=True)
    M = {tag: measured(tag, r['root'], r['n_batches']) for tag, r in runs.items()
         if r.get('rc') == 0 and r.get('root')}
    rows_p1, rows_p3 = [], []
    print(f'{"point":12s} {"P1 store m/p":>14} {"P1 ff m/p":>14} {"P3 store fifo | lifo cell":>30} '
          f'{"P3 pred":>8} {"P5 m / pred":>16} {"P4 sig":>7} {"P6 max":>7}')
    for tag, r in runs.items():
        if tag not in M:
            continue
        m, ks, kf, c = M[tag], r['store_k'], r['ff_k'], r['c']
        p1s = statistics.mean(v for a, v in m['P1'].items() if a.startswith('store/'))
        p1f = statistics.mean(v for a, v in m['P1'].items() if a.startswith('fulfillment/'))
        g = {cell: m['P3'].get(f'{cell}/store', {}).get('total_pct')
             for cell in ('k1_off_fifo', 'k1_off_lifo')}
        anchor_tag = f'k1_c{int(round(c * 100))}'
        a3 = M.get(anchor_tag, {}).get('P3', {})
        g1 = statistics.mean(v['total_pct'] for k_, v in a3.items() if k_.endswith('/store')) \
            if a3 else None
        pred3 = None if g1 is None or ks not in P1['store'] else g1 * p3_ratio(ks)
        a5 = M.get(anchor_tag, {}).get('P5', {})
        g5_1 = statistics.mean(v['total_pct'] for k_, v in a5.items() if k_.endswith('/store')) \
            if a5 else None
        g5 = statistics.mean(v['total_pct'] for k_, v in m['P5'].items() if k_.endswith('/store'))
        pred5 = None if g5_1 is None else g5_1 * p5_ratio(ks)
        sig = sum(1 for v in m['P4'].values() if v['ci_pct'] and
                  (v['ci_pct'][0] > 0 or v['ci_pct'][1] < 0))
        drift = max(abs(b / a - 1.0) for a, b in m['P6'].values())
        print(f'{tag:12s} {p1s:6.1%}/{P1["store"][ks]:5.1%}  {p1f:6.1%}/{P1["fulfillment"].get(kf, float("nan")):5.1%}  '
              f'{g["k1_off_fifo"]:+7.2f}% | {g["k1_off_lifo"]:+7.2f}%          '
              f'{(pred3 if pred3 is not None else float("nan")):+7.2f}%  {g5:+6.2f}/{(pred5 if pred5 is not None else float("nan")):+6.2f}%  '
              f'{sig:3d}/{len(m["P4"])}  {drift:6.2%}')
        if r['n_batches'] == 40:
            rows_p1.append({'label': f'store {tag}', 'predicted': 100 * P1['store'][ks],
                            'realised': 100 * p1s})
            rows_p1.append({'label': f'fulfillment {tag}',
                            'predicted': 100 * P1['fulfillment'][kf], 'realised': 100 * p1f})
            if ks != 1 and pred3 is not None:
                cis = [m['P3'][f'{cell}/store']['ci_pct'] for cell in g
                       if m['P3'].get(f'{cell}/store', {}).get('ci_pct')]
                rows_p3.append({'label': f'{tag}', 'predicted': pred3,
                                'realised': statistics.mean(g.values()),
                                'lo': min(ci[0] for ci in cis) if cis else None,
                                'hi': max(ci[1] for ci in cis) if cis else None})
    if rows_p1:
        draw.predicted_vs_realised(rows_p1, os.path.join(RES, 'S12_P1_fresh_share.png'),
                                   title='Fresh-bin share over the grid',
                                   subtitle='predicted in lines before any run; realised in units')
    if rows_p3:
        draw.predicted_vs_realised(rows_p3, os.path.join(RES, 'S12_P3_placement_gap.png'),
                                   title='Store placement gap over the grid',
                                   subtitle='uni rank vs uni fifo; mean of the fifo and lifo '
                                            'cells, bar spans both 95% intervals')


if __name__ == '__main__':
    main(sys.argv[1])
