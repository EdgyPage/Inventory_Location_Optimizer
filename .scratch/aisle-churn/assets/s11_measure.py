"""S11 -- measure the registered predictions P1, P3-P6 on one `_churn_probe` grid root.
(P2, the ground-share trajectory, is `s09_front.py ... aisle-waterfill` on the same root.)

  P1  served share: units picked from bins a reorder filled in the window / units picked
      (`run_unload_ranking.inbound_repick`), per channel and arm;
  P3  the placement gap, uni rank vs uni fifo, per cell and channel;
  P4  the unloading-order gap, lifo cell vs fifo cell, per arm;
  P5  the initial layout, opt rank vs uni rank, per cell and channel;
  P6  the free pool, summed `free_index` at the first and last batch, per arm.

Every gap is pick labour (`batch_stats.task_makespan`) summed over the window, with the
paired per-batch moving-block 95% interval of `run_unload_ranking.measured_floor`.

    python .scratch/aisle-churn/assets/s11_measure.py <grid_root> [<lo> <hi>] [--json out.json]
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _dbs(root):
    """{(cell, channel, arm): db path}."""
    out = {}
    for db in glob.glob(os.path.join(root, 'k1_off_*', '**', 'sim_*.db'), recursive=True):
        if db.endswith('.keyframes.db'):
            continue
        rel = os.path.relpath(db, root).split(os.sep)
        out[(rel[0], rel[-2], os.path.basename(db)[4:-3])] = db
    return out


def _series(db, lo, hi):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    return {b: t for b, t in con.execute(
        'select batch_id, sum(task_makespan) from batch_stats where batch_id >= ? and '
        'batch_id < ? group by batch_id', (lo, hi))}


def _free(db, lo, hi):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    rows = dict(con.execute('select batch_id, sum(free) from free_index where batch_id >= ? '
                            'and batch_id < ? group by batch_id', (lo, hi)))
    if not rows:
        return None
    return rows[min(rows)], rows[max(rows)]


def _served(db, arm, lo, hi):
    from Optimization.persistence.Picking_Data import (find_run, load_bin_placements,
                                                      load_pick_bins)
    from Optimization.run_unload_ranking import inbound_repick
    run_id = find_run(db, arm)
    picks = [p for p in load_pick_bins(db, run_id) if lo <= int(p['batch_id']) < hi]
    places = [p for p in load_bin_placements(db, run_id)
              if p.get('cause') != 'reorder' or lo <= int(p['batch_id']) < hi]
    return inbound_repick(picks, places)


def _gap(a, b):
    """(total-ratio gap %, paired mean gap %, ci) of series `a` against reference `b`."""
    from Optimization.run_unload_ranking import measured_floor
    tot = (sum(a.values()) / sum(b.values()) - 1.0) * 100.0
    fl = measured_floor({'ref': b, 'x': a}, 'ref')
    c = fl['cells'].get('x') or {}
    return tot, c.get('gap_pct'), c.get('ci_pct')


def main(root, lo=0, hi=40, out=None):
    dbs = _dbs(root)
    S = {k: _series(v, lo, hi) for k, v in dbs.items()}
    # the run root by NAME (relative to COMPARISON_OUTPUT_DIR): a tracked result never
    # carries a machine-local path (CLAUDE.md section 5)
    res = {'root': os.path.basename(os.path.normpath(root)), 'window': [lo, hi], 'P1': {}, 'P3': {}, 'P4': {}, 'P5': {}, 'P6': {}}
    # every non-fifo rule the run swept, per channel (the winner pair, or S15's supplement)
    rank = defaultdict(set)
    for (_c, ch, arm) in dbs:
        if arm.startswith('uni_') and not arm.startswith('uni_fifo'):
            rank[ch].add(arm[len('uni_'):-len('_norsl')])

    def row(tag, key, g):
        tot, mean, ci = g
        ci_s = f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else 'n/a'
        sig = '*' if ci and (ci[0] > 0 or ci[1] < 0) else ' '
        print(f'  {tag:44s} total {tot:+6.2f}%  paired {mean if mean is not None else float("nan"):+6.2f}% '
              f'95% {ci_s} {sig}')
        res[key][tag] = {'total_pct': tot, 'paired_pct': mean, 'ci_pct': ci}

    print(f'{root}  batches {lo}-{hi - 1}')
    print('P1 served share (units):')
    for (cell, ch, arm), db in sorted(dbs.items()):
        if cell != 'k1_off_fifo':
            continue
        m = _served(db, arm, lo, hi)
        res['P1'][f'{ch}/{arm}'] = m.get('served_share')
        print(f'  {ch:12s} {arm:28s} {m.get("served_share", float("nan")):7.2%}   re-picked '
              f'{m.get("repicked_share", float("nan")):7.2%}')
    cells = sorted({c for c, _ch, _a in dbs})
    print('P3 placement gap, uni rank vs uni fifo:')
    for cell in cells:
        for ch, rules in rank.items():
            for r in sorted(rules):
                a, b = S.get((cell, ch, f'uni_{r}_norsl')), S.get((cell, ch, 'uni_fifo_norsl'))
                tag = f'{cell}/{ch}' if len(rules) == 1 else f'{cell}/{ch}/{r}'
                if a and b:
                    row(tag, 'P3', _gap(a, b))
    print('P5 initial layout, opt rank vs uni rank:')
    for cell in cells:
        for ch, rules in rank.items():
            for r in sorted(rules):
                a, b = S.get((cell, ch, f'opt_{r}_norsl')), S.get((cell, ch, f'uni_{r}_norsl'))
                tag = f'{cell}/{ch}' if len(rules) == 1 else f'{cell}/{ch}/{r}'
                if a and b:
                    row(tag, 'P5', _gap(a, b))
    print('P4 unloading order, lifo cell vs fifo cell:')
    for (cell, ch, arm) in sorted(dbs):
        if cell != 'k1_off_lifo':
            continue
        a, b = S.get((cell, ch, arm)), S.get(('k1_off_fifo', ch, arm))
        if a and b:
            row(f'{ch}/{arm}', 'P4', _gap(a, b))
    print('P6 free pool (sum of free_index), first -> last batch:')
    for (cell, ch, arm), db in sorted(dbs.items()):
        if cell != 'k1_off_fifo':
            continue
        f = _free(db, lo, hi)
        if f:
            res['P6'][f'{ch}/{arm}'] = f
            print(f'  {ch:12s} {arm:28s} {f[0]:9,d} -> {f[1]:9,d}  ({f[1] / f[0] - 1:+.2%})')
    if out:
        json.dump(res, open(out, 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    o = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
    if o in a:
        a.remove(o)
    main(a[0], *(int(x) for x in a[1:3]), out=o)
