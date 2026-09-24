"""S02 / S08 -- read a run's `runtime_metrics.db` into the inbound anatomy tables.

Per arm: total_s and its named sections, the inbound overlays as shares of reord_s, the
setup columns, the planner counters, and the unnamed gap (total_s minus the stacked
sections), with sib_setup_s beside it.  With two roots, it prints per-column ratios
B / A for arms present in both (matched on cell, pair, channel, arm).

    python .scratch/inbound-fullscale-perf/assets/s02_read_run.py <run_root> [<run_root_B>]
"""
from __future__ import annotations

import os
import sqlite3
import sys

STACK = ('reord_s', 'build_s', 'pre_s', 'sim_s', 'extract_s', 'inv_s', 'save_s', 'smpl_s',
         'task_s', 'kf_s')
INB = ('inb_pre_s', 'inb_freeze_s', 'inb_pack_s', 'inb_yplan_s', 'inb_dplan_s',
       'inb_unload_s', 'inb_handoff_s', 'put_s', 'put_open_s')
CNT = ('inb_drains', 'yard_T_sum', 'yard_T_max', 'yard_pulls', 'plan_rounds', 'plan_places',
       'put_opens', 'put_units')
SETUP = ('startup_s', 'sib_setup_s')


def _root(arg: str) -> str:
    if os.path.isdir(arg):
        return arg
    base = os.environ.get('COMPARISON_OUTPUT_DIR', '')
    return os.path.join(base, arg)


def rows(root: str) -> list[dict]:
    db = os.path.join(_root(root), 'runtime_metrics.db')
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute('select * from runtime order by cell, pair, arm')]
    con.close()
    return out


def _f(v, w=8, p=1):
    return f'{v:{w}.{p}f}' if isinstance(v, (int, float)) else f'{"-":>{w}}'


def anatomy(rs: list[dict]) -> None:
    print(f'{"cell":24s} {"channel":11s} {"arm":28s} {"total":>8s} {"reord":>8s} '
          f'{"gap":>7s} {"sib":>7s} {"start":>7s} | ' + ' '.join(f'{c[:-2][-7:]:>7s}' for c in INB))
    for r in rs:
        tot = r['total_s'] or 0.0
        stacked = sum((r.get(c) or 0.0) for c in STACK)
        gap = tot - stacked
        re = r['reord_s'] or 0.0
        shares = ' '.join(
            f'{100 * (r.get(c) or 0.0) / re:6.1f}%' if re and r.get(c) is not None else f'{"-":>7s}'
            for c in INB)
        print(f'{r["cell"][:24]:24s} {str(r["channel"])[:11]:11s} {r["arm"][:28]:28s} '
              f'{_f(tot)} {_f(re)} {_f(gap, 7)} {_f(r.get("sib_setup_s"), 7)} '
              f'{_f(r.get("startup_s"), 7)} | {shares}')
    print()
    print(f'{"cell":24s} {"channel":11s} {"arm":28s} ' + ' '.join(f'{c[-10:]:>10s}' for c in CNT))
    for r in rs:
        print(f'{r["cell"][:24]:24s} {str(r["channel"])[:11]:11s} {r["arm"][:28]:28s} '
              + ' '.join(f'{(r.get(c) if r.get(c) is not None else "-"):>10}' for c in CNT))


def compare(a: list[dict], b: list[dict]) -> None:
    key = lambda r: (r['cell'], r['pair'], r['channel'], r['arm'])  # noqa: E731
    bi = {key(r): r for r in b}
    cols = ('total_s', 'reord_s') + INB + SETUP + ('plan_places',)
    print(f'{"cell":24s} {"arm":28s} ' + ' '.join(f'{c[:-2] if c.endswith("_s") else c:>10s}'[:11] for c in cols))
    for r in a:
        s = bi.get(key(r))
        if s is None:
            continue
        cells = []
        for c in cols:
            x, y = r.get(c), s.get(c)
            cells.append(f'{y / x:9.2f}x' if x and y is not None else f'{"-":>10s}')
        print(f'{r["cell"][:24]:24s} {r["arm"][:28]:28s} ' + ' '.join(cells))


def main():
    a = rows(sys.argv[1])
    anatomy(a)
    if len(sys.argv) > 2:
        b = rows(sys.argv[2])
        print('\n== B')
        anatomy(b)
        print('\n== B / A')
        compare(a, b)


if __name__ == '__main__':
    main()
