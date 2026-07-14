"""run_whatif_delta.py — compare what-if scenarios against a reference cell.

Reads the four steady-state success metrics straight from each scenario's sim_*.db (no run_analysis
needed), keyed by (pair, pick-config, channel, arm), and diffs every cell against the reference cell:

    task makespan  (Σ task time = total labor)          batch makespan (last-picker finish)
    throughput / task makespan (items / Σ task time)    throughput / batch makespan (items / makespan)

Emits whatif_delta.csv (all four deltas) + a labor-saving-vs-throughput-gain scatter (one point per
cell×channel×arm) and a median summary.  This is what makes a scheduling win legible: LPT should show
Δbatch-makespan ↓ and Δthroughput/batch ↑ while Δtask-makespan (labor) and Δthroughput/task stay ≈flat.

    python -m Optimization.run_whatif_delta <comparison_whatif_...>   # --reference defaults from config
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sqlite3
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

WIN = 50   # steady-state window (batches), matching series.py


def _metrics(db: str):
    """Steady-state means (last WIN batches) of the four success metrics from batch_stats:

        task_ms   = task makespan  = Σ task time = total labor        (metric a)
        batch_ms  = batch makespan = last-picker finish (wall-clock)  (metric b)
        thr_batch = items / batch makespan                            (metric d)
        thr_task  = items / task makespan                             (metric c)

    New runs carry task_makespan/thr_task as columns; legacy DBs fall back to the task_stats join.
    """
    con = sqlite3.connect(db)
    try:
        maxb = con.execute('SELECT MAX(batch_id) FROM batch_stats').fetchone()[0]
        if maxb is None:
            return None
        lo = maxb - WIN + 1
        cols = {r[1] for r in con.execute('PRAGMA table_info(batch_stats)')}
        if 'task_makespan' in cols:
            # thr_batch counts 0-duration batches as 0 (matches series ss_thr); thr_task SKIPS
            # 0-labor batches (CASE with no ELSE → NULL → excluded by AVG), matching series ss_thr_task.
            row = con.execute(
                'SELECT AVG(task_makespan), AVG(duration), '
                'AVG(CASE WHEN duration>0 THEN CAST(total_items AS REAL)/duration ELSE 0 END), '
                'AVG(CASE WHEN task_makespan>0 THEN CAST(total_items AS REAL)/task_makespan END) '
                'FROM batch_stats WHERE batch_id>=?', (lo,)).fetchone()
            return {'task_ms': row[0], 'batch_ms': row[1], 'thr_batch': row[2], 'thr_task': row[3]}
        # legacy fallback — task makespan (and its throughput) from the task_stats join
        task_ms = con.execute('SELECT AVG(s) FROM (SELECT batch_id, SUM(duration) s '
                             'FROM task_stats WHERE batch_id>=? GROUP BY batch_id)', (lo,)).fetchone()[0]
        row = con.execute('SELECT AVG(duration), AVG(CASE WHEN duration>0 '
                          'THEN CAST(total_items AS REAL)/duration ELSE 0 END) '
                          'FROM batch_stats WHERE batch_id>=?', (lo,)).fetchone()
        thr_task = con.execute(
            'SELECT AVG(CASE WHEN t.s>0 THEN CAST(b.total_items AS REAL)/t.s END) '
            'FROM batch_stats b JOIN (SELECT batch_id, SUM(duration) s FROM task_stats '
            'WHERE batch_id>=? GROUP BY batch_id) t ON b.batch_id=t.batch_id '
            'WHERE b.batch_id>=?', (lo, lo)).fetchone()[0]
        return {'task_ms': task_ms, 'batch_ms': row[0], 'thr_batch': row[1], 'thr_task': thr_task}
    finally:
        con.close()


def _scan(cell_dir: str) -> dict:
    """{(pair, pickcfg, channel, arm): metrics} for one scenario subtree."""
    out = {}
    for db in glob.glob(os.path.join(cell_dir, '**', 'sim_*.db'), recursive=True):
        if db.endswith('.keyframes.db'):
            continue
        rel = os.path.relpath(db, cell_dir).replace('\\', '/').split('/')
        if len(rel) < 4:          # expect pair/pickcfg/channel/sim_<arm>.db
            continue
        pair, pickcfg, channel = rel[0], rel[1], rel[2]
        arm = os.path.basename(db)[4:-3]
        m = _metrics(db)
        if m and m['task_ms']:
            out[(pair, pickcfg, channel, arm)] = m
    return out


def main():
    ap = argparse.ArgumentParser(description='Diff what-if scenarios vs a reference cell.')
    ap.add_argument('base_dir')
    ap.add_argument('--reference', default=None,
                    help="reference cell to diff against (default: WHATIF['reference'] from "
                         "whatif_config.py, else 'k1_off')")
    args = ap.parse_args()
    if args.reference is None:
        # Single source of truth: the sweep's reference cell lives in whatif_config.
        try:
            from Optimization.whatif_config import WHATIF
            args.reference = WHATIF.get('reference', 'k1_off')
        except Exception:
            args.reference = 'k1_off'

    cells = [d for d in sorted(os.listdir(args.base_dir))
             if os.path.isdir(os.path.join(args.base_dir, d)) and not d.startswith('_')]
    if args.reference not in cells:
        raise SystemExit(f'reference cell {args.reference!r} not found in {cells}')
    scans = {c: _scan(os.path.join(args.base_dir, c)) for c in cells}
    ref = scans[args.reference]

    def _pct(a, b, higher_better):
        """Signed % change of b vs reference a; + always = better.  NaN on missing/zero ref/cell."""
        if a is None or b is None or not a or a != a or b != b:
            return float('nan')
        return (b - a) / a * 100 if higher_better else (a - b) / a * 100

    rows = []
    for cell in cells:
        if cell == args.reference:
            continue
        for key, m in scans[cell].items():
            r = ref.get(key)
            if not r or not r['task_ms'] or not r['thr_batch']:
                continue
            pair, pickcfg, channel, arm = key
            rows.append({
                'cell': cell, 'pair': pair, 'pickcfg': pickcfg, 'channel': channel, 'arm': arm,
                'ref_task_ms': r['task_ms'], 'cell_task_ms': m['task_ms'],
                'd_task_ms_pct': _pct(r['task_ms'], m['task_ms'], False),      # + = less task makespan (labor)
                'ref_batch_ms': r['batch_ms'], 'cell_batch_ms': m['batch_ms'],
                'd_batch_ms_pct': _pct(r['batch_ms'], m['batch_ms'], False),   # + = shorter batch makespan
                'ref_thr_batch': r['thr_batch'], 'cell_thr_batch': m['thr_batch'],
                'd_thr_batch_pct': _pct(r['thr_batch'], m['thr_batch'], True), # + = more throughput / batch ms
                'ref_thr_task': r['thr_task'], 'cell_thr_task': m['thr_task'],
                'd_thr_task_pct': _pct(r['thr_task'], m['thr_task'], True),    # + = more throughput / task ms
            })

    cols = ['cell', 'pair', 'pickcfg', 'channel', 'arm',
            'ref_task_ms', 'cell_task_ms', 'd_task_ms_pct',
            'ref_batch_ms', 'cell_batch_ms', 'd_batch_ms_pct',
            'ref_thr_batch', 'cell_thr_batch', 'd_thr_batch_pct',
            'ref_thr_task', 'cell_thr_task', 'd_thr_task_pct']
    csv_path = os.path.join(args.base_dir, 'whatif_delta.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f'wrote {csv_path}  ({len(rows)} rows)')

    if rows:
        fig, ax = plt.subplots(figsize=(10, 7))
        cellset = sorted(set(r['cell'] for r in rows))
        cmap = plt.cm.tab10
        colors = {c: cmap(i % 10) for i, c in enumerate(cellset)}
        markers = {'store': 's', 'fulfillment': 'o'}
        for r in rows:
            ax.scatter(r['d_task_ms_pct'], r['d_thr_batch_pct'], color=colors[r['cell']],
                       marker=markers.get(r['channel'], 'o'), s=70,
                       edgecolors='white', linewidths=0.5, zorder=3)
        for c in cellset:
            ax.scatter([], [], color=colors[c], label=c)
        ax.scatter([], [], color='#888', marker='o', label='• fulfillment')
        ax.scatter([], [], color='#888', marker='s', label='■ store')
        ax.axhline(0, color='k', lw=0.8)
        ax.axvline(0, color='k', lw=0.8)
        ax.set_xlabel('Task-makespan (labor) saving % vs reference (→ better)')
        ax.set_ylabel('Throughput / batch-makespan gain % vs reference (↑ better)')
        ax.set_title(f'What-if scenarios vs "{args.reference}"  —  point = cell × channel × arm\n'
                     f'up at flat x = a scheduling win (batch makespan ↓ at flat task makespan)')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, title='scenario / channel')
        png = os.path.join(args.base_dir, 'whatif_delta.png')
        fig.savefig(png, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f'wrote {png}')

        print(f'\nMedian Δ vs "{args.reference}" by cell × channel  '
              f'(+ = better; task_ms=labor, batch_ms=makespan):')
        agg: dict = {}
        for r in rows:
            agg.setdefault((r['cell'], r['channel']), []).append(r)
        _med = lambda vs, k: (statistics.median(v[k] for v in vs if v[k] == v[k])
                              if any(v[k] == v[k] for v in vs) else float('nan'))
        for (cell, ch), vs in sorted(agg.items()):
            print(f'  {cell:16} {ch:12}  '
                  f'Δtask_ms={_med(vs, "d_task_ms_pct"):+5.1f}%  '
                  f'Δbatch_ms={_med(vs, "d_batch_ms_pct"):+5.1f}%  '
                  f'Δthr/batch={_med(vs, "d_thr_batch_pct"):+6.1f}%  '
                  f'Δthr/task={_med(vs, "d_thr_task_pct"):+6.1f}%')


if __name__ == '__main__':
    main()
