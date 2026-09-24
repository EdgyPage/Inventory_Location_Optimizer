"""S07 -- `rank_sortmatch` against the other rules on one `_sortmatch_probe` root.

Per channel and stock mode: pick labour (sum of `batch_stats.task_makespan` over the
window) of every rule against sortmatch, with the paired per-batch moving-block 95%
interval of `run_unload_ranking.measured_floor` (a gap is significant when the interval
excludes 0); put seconds (`recv_seconds` is receiving; put-away is `work_events`' put
kind if present, else skipped); the day-cut carry; and the arm's placement compute from
`runtime_metrics.db`.

    python .scratch/placement-sortmatch/assets/s07_measure.py <run_root> [lo hi] [--json out]
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, _REPO)


def _ro(p):
    return sqlite3.connect('file:' + p + '?mode=ro', uri=True)


def _dbs(root):
    out = {}
    for db in glob.glob(os.path.join(root, 'k1_off_*', '**', 'sim_*.db'), recursive=True):
        if db.endswith('.keyframes.db'):
            continue
        rel = os.path.relpath(db, root).split(os.sep)
        out[(rel[-2], os.path.basename(db)[4:-3])] = db
    return out


def _series(db, lo, hi):
    return dict(_ro(db).execute(
        'select batch_id, sum(task_makespan) from batch_stats where batch_id >= ? and '
        'batch_id < ? group by batch_id', (lo, hi)))


def _carry(db):
    try:
        return _ro(db).execute('select sum(standing_carry_labour) from shift_days').fetchone()[0]
    except sqlite3.Error:
        return None


def _gap(a, b):
    from Optimization.run_unload_ranking import measured_floor
    tot = (sum(a.values()) / sum(b.values()) - 1.0) * 100.0
    fl = measured_floor({'ref': b, 'x': a}, 'ref')
    c = fl['cells'].get('x') or {}
    return tot, c.get('gap_pct'), c.get('ci_pct')


def _runtime(root):
    """{(channel, arm): {reord_s, inv_s, total_s}} -- runtime_metrics' `runtime` table
    (reord_s is the put-away drain, where a placement rule's compute lands)."""
    p = os.path.join(root, 'runtime_metrics.db')
    if not os.path.exists(p):
        return {}
    con = _ro(p)
    return {(ch, arm): dict(reord_s=r, inv_s=i, total_s=t) for ch, arm, r, i, t in con.execute(
        'select channel, arm, avg(reord_s), avg(inv_s), avg(total_s) from runtime '
        'group by channel, arm')}


def main(root, lo=0, hi=40, out=None):
    dbs = _dbs(root)
    S = {k: _series(v, lo, hi) for k, v in dbs.items()}
    res = {'root': os.path.basename(os.path.normpath(root)), 'window': [lo, hi], 'rows': []}
    print(f'{os.path.basename(root)}  batches {lo}-{hi - 1}')
    for ch in sorted({c for c, _a in dbs}):
        for mode in ('uni', 'opt'):
            ref = (ch, f'{mode}_rank_sortmatch_norsl')
            if ref not in S:
                continue
            print(f'\n{ch} / {mode}: each rule vs sortmatch (positive = sortmatch cheaper)')
            for (c2, arm) in sorted(dbs):
                if c2 != ch or not arm.startswith(mode + '_') or (c2, arm) == ref:
                    continue
                tot, mean, ci = _gap(S[(c2, arm)], S[ref])
                sig = '*' if ci and (ci[0] > 0 or ci[1] < 0) else ' '
                ci_s = f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else 'n/a'
                print(f'  {arm:30s} total {tot:+6.2f}%  paired {mean if mean is not None else float("nan"):+6.2f}%  95% {ci_s} {sig}')
                res['rows'].append(dict(channel=ch, mode=mode, arm=arm, total_pct=tot,
                                        paired_pct=mean, ci_pct=ci))
            print(f'  day-cut carry labour: ' + ', '.join(
                f'{a[len(mode) + 1:-6]} {_carry(dbs[(c2, a)]) or 0:,.0f}'
                for (c2, a) in sorted(dbs) if c2 == ch and a.startswith(mode + '_')))
    rt = _runtime(root)
    if rt:
        print('\nruntime_metrics (per arm; reord_s = the put-away drain):')
        for k in sorted(rt, key=str):
            v = rt[k]
            print(f'  {str(k[0]):12s} {k[1]:30s} ' + ' '.join(
                f'{n}={x:.2f}' for n, x in v.items() if x is not None))
        res['runtime'] = {f'{k[0]}/{k[1]}': v for k, v in rt.items()}
    if out:
        json.dump(res, open(out, 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    o = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
    main(a[0], *(int(x) for x in a[1:3]), out=o)
