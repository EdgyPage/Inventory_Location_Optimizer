"""S04b -- the pick side's parallelism ceiling: one picker per aisle per day.

The era's crew is sized as if a day's picking were divisible (capacity = crew x shift), but the
simulator hands each aisle's day to ONE picker (`task_stats`: a task is an aisle visit).  An
aisle whose day of picking takes longer than the shift is cut at the shift end whatever the
crew, and its units are carried -- so the day-cut backlog grows once

    k W_a(1) > S          for the busiest aisles,   k* = S / max_a W_a(1),

where W_a(1) is aisle a's mean daily task seconds at the declared demand.  The predicted
overflow share at density k is sum_a max(0, k W_a - S) / sum_a k W_a (travel held per task,
handling scaled; an upper bound, since a task's travel does not scale with its picks).

Measured on the grid: the share of task-days that hit the shift, and the day-cut carry per day.

    python .scratch/aisle-churn/assets/s04_aisle_ceiling.py <grid_runs.json> <channel>
"""
from __future__ import annotations

import glob
import json
import sqlite3
import statistics
import sys
from collections import defaultdict

S = 28_800.0


def aisle_loads(db, lo=5, hi=40):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    per = defaultdict(float)
    days = set()
    for b, a, d in con.execute('select batch_id, aisle_id, duration from task_stats where '
                               'batch_id >= ? and batch_id < ?', (lo, hi)):
        per[a] += d
        days.add(b)
    n = max(1, len(days))
    return {a: v / n for a, v in per.items()}


def main(book, channel):
    runs = json.load(open(book, encoding='utf-8'))
    db1 = glob.glob(f"{runs['k1_c95']['root']}/k1_off_fifo/*/*/{channel}/sim_uni_fifo_norsl.db")[0]
    W = aisle_loads(db1)
    top = max(W.values())
    print(f'{channel}: {len(W)} aisles picked at k=1; busiest aisle {top:,.0f} s/day -> '
          f'k* = {S / top:.1f}')
    print(f'{"point":9s} {"k":>3} {"pred overflow":>14} {"tasks at shift":>15} {"daycut carry/picked":>20}')
    for tag, r in runs.items():
        if r['n_batches'] != 40 or r['c'] != 0.95:
            continue
        k = r['store_k'] if channel == 'store' else r['ff_k']
        tot = sum(k * w for w in W.values())
        over = sum(max(0.0, k * w - S) for w in W.values()) / tot
        db = glob.glob(f"{r['root']}/k1_off_fifo/*/*/{channel}/sim_uni_fifo_norsl.db")[0]
        con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
        n, hit = con.execute('select count(*), sum(duration >= ?) from task_stats where '
                             'batch_id between 5 and 39', (0.99 * S,)).fetchone()
        cut = con.execute("select sum(qty) from carryover where reason = 'unpicked_daycut' and "
                          "batch_id between 35 and 39").fetchone()[0] or 0
        picked = con.execute('select sum(quantity) from picks where batch_id between 35 and 39'
                             ).fetchone()[0] or 1
        print(f'{tag:9s} {k:3d} {over:14.1%} {hit / n:15.1%} {cut / picked:20.2f}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
