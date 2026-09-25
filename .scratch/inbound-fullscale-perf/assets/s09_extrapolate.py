"""S09 -- what would the 20260920 campaign (40 batches, 11 cells, 44 units, 12 workers) cost
on today's code?

Per leaf of the campaign, split total_s into reord_s and the rest, and scale each by the
run B / run A ratio measured for the same (cell kind, arm, channel):

  * cell kind: 'gain' for every gain-policy cell (gmyopic, gforecast, ggated_*, fsight_*,
    gmyopic_k8), 'fifo' for the rest (fifo, lifo, inb_off).  Runs A/B measured one cell
    of each kind, gmyopic and fifo; the forecast/gated/futuresight cells price through the
    SAME place_load pool path, with the predicted tier added, so they inherit gmyopic's
    ratio.  That is an assumption, stated, not a measurement.
  * arms O3 does not touch (fifo, tmin) get ratio 1.0: their measured 0.93-1.02x is the
    A/B's noise floor, and carrying noise into a projection would invent a saving.

A coupled unit's wall is the larger of its two leaves (the store leaf carries the
sibling's setup inside its total).  The campaign's wall bound is max(slowest unit,
sum of units / workers).

    python .scratch/inbound-fullscale-perf/assets/s09_extrapolate.py <campaign> <runA> <runB>
"""
from __future__ import annotations

import os
import sqlite3
import sys

GAIN_CELLS = ('gmyopic', 'gforecast', 'ggated', 'fsight')
UNTOUCHED = ('fifo', 'tmin')


def _db(root):
    base = os.environ.get('COMPARISON_OUTPUT_DIR', '')
    path = os.path.join(root if os.path.isdir(root) else os.path.join(base, root),
                        'runtime_metrics.db')
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute('select * from runtime')]
    con.close()
    return rows


def kind(cell: str) -> str:
    return 'gain' if any(g in cell for g in GAIN_CELLS) else 'fifo'


def rule(arm: str) -> str:
    return arm.split('_', 1)[1].rsplit('_', 1)[0]          # uni_rank_minlabor_norsl -> rank_minlabor


def ratios(a_rows, b_rows) -> dict:
    key = lambda r: (kind(r['cell']), r['arm'], r['channel'])  # noqa: E731
    bi = {key(r): r for r in b_rows}
    out = {}
    for r in a_rows:
        s = bi.get(key(r))
        if s is None:
            continue
        if rule(r['arm']) in UNTOUCHED:
            out[key(r)] = (1.0, 1.0)
            continue
        rest_a, rest_b = r['total_s'] - r['reord_s'], s['total_s'] - s['reord_s']
        out[key(r)] = (s['reord_s'] / r['reord_s'], rest_b / rest_a)
    return out


def main():
    camp, a, b = sys.argv[1:4]
    workers = 12
    rat = ratios(_db(a), _db(b))
    for k, v in sorted(rat.items()):
        if v != (1.0, 1.0):
            print(f'  ratio {k}: reord {v[0]:.3f}  rest {v[1]:.3f}')
    units: dict = {}
    for r in _db(camp):
        k = (kind(r['cell']), r['arm'], r['channel'])
        rr, rs = rat.get(k, (1.0, 1.0))
        now = r['total_s']
        new = r['reord_s'] * rr + (r['total_s'] - r['reord_s']) * rs
        u = units.setdefault((r['cell'], r['pair'], r['arm'].split('_')[0],
                              'winner' if rule(r['arm']) != 'fifo' else 'rider'),
                             [0.0, 0.0])
        u[0] = max(u[0], now)
        u[1] = max(u[1], new)
    tot0 = sum(u[0] for u in units.values())
    tot1 = sum(u[1] for u in units.values())
    mx0 = max(units.items(), key=lambda kv: kv[1][0])
    mx1 = max(units.items(), key=lambda kv: kv[1][1])
    print(f'{len(units)} units')
    print(f'  sum of unit walls  {tot0 / 3600:6.1f} h -> {tot1 / 3600:6.1f} h  ({tot0 / tot1:.2f}x)')
    print(f'  slowest unit       {mx0[1][0] / 3600:6.2f} h {mx0[0][:1]} -> '
          f'{mx1[1][1] / 3600:6.2f} h {mx1[0][:1]}')
    b0 = max(mx0[1][0], tot0 / workers)
    b1 = max(mx1[1][1], tot1 / workers)
    print(f'  sim-stage bound    {b0 / 3600:6.2f} h -> {b1 / 3600:6.2f} h  '
          f'(max(slowest, sum/{workers}))')
    print('  per cell (slowest unit, h):')
    cells = sorted({k[0] for k in units})
    for c in cells:
        a0 = max(v[0] for k, v in units.items() if k[0] == c)
        a1 = max(v[1] for k, v in units.items() if k[0] == c)
        print(f'    {c:22s} {a0 / 3600:5.2f} -> {a1 / 3600:5.2f}')


if __name__ == '__main__':
    main()
