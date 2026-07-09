"""run_whatif_delta.py — compare what-if scenarios against a reference cell.

Reads the steady-state ss_prod_hours (total labor) and ss_thr (throughput) straight from each
scenario's sim_*.db (no run_analysis needed), keyed by (pair, pick-config, channel, arm), and diffs
every cell against the reference cell.  Emits whatif_delta.csv + a labor-saving-vs-throughput-gain
scatter (one point per cell×channel×arm) and a median summary — tracking BOTH axes, since for
fulfillment the throughput gain is the interesting lever (total labor is cart-swap-dominated).

    python Optimization/run_whatif_delta.py <comparison_whatif_...> --reference base
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
    con = sqlite3.connect(db)
    try:
        maxb = con.execute('SELECT MAX(batch_id) FROM batch_stats').fetchone()[0]
        if maxb is None:
            return None
        lo = maxb - WIN + 1
        prod = con.execute('SELECT AVG(s) FROM (SELECT batch_id, SUM(duration) s '
                           'FROM task_stats WHERE batch_id>=? GROUP BY batch_id)', (lo,)).fetchone()[0]
        row = con.execute('SELECT AVG(duration), AVG(CASE WHEN duration>0 '
                          'THEN CAST(total_items AS REAL)/duration ELSE 0 END) '
                          'FROM batch_stats WHERE batch_id>=?', (lo,)).fetchone()
        return {'prod': prod, 'dur': row[0], 'thr': row[1]}
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
        if m and m['prod']:
            out[(pair, pickcfg, channel, arm)] = m
    return out


def main():
    ap = argparse.ArgumentParser(description='Diff what-if scenarios vs a reference cell.')
    ap.add_argument('base_dir')
    ap.add_argument('--reference', default='base')
    args = ap.parse_args()

    cells = [d for d in sorted(os.listdir(args.base_dir))
             if os.path.isdir(os.path.join(args.base_dir, d)) and not d.startswith('_')]
    if args.reference not in cells:
        raise SystemExit(f'reference cell {args.reference!r} not found in {cells}')
    scans = {c: _scan(os.path.join(args.base_dir, c)) for c in cells}
    ref = scans[args.reference]

    rows = []
    for cell in cells:
        if cell == args.reference:
            continue
        for key, m in scans[cell].items():
            r = ref.get(key)
            if not r or not r['prod'] or not r['thr']:
                continue
            pair, pickcfg, channel, arm = key
            rows.append({
                'cell': cell, 'pair': pair, 'pickcfg': pickcfg, 'channel': channel, 'arm': arm,
                'ref_prod': r['prod'], 'cell_prod': m['prod'],
                'dlabor_pct': (r['prod'] - m['prod']) / r['prod'] * 100,   # + = less labor
                'ref_thr': r['thr'], 'cell_thr': m['thr'],
                'dthr_pct': (m['thr'] - r['thr']) / r['thr'] * 100,        # + = more throughput
            })

    cols = ['cell', 'pair', 'pickcfg', 'channel', 'arm', 'ref_prod', 'cell_prod',
            'dlabor_pct', 'ref_thr', 'cell_thr', 'dthr_pct']
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
            ax.scatter(r['dlabor_pct'], r['dthr_pct'], color=colors[r['cell']],
                       marker=markers.get(r['channel'], 'o'), s=70,
                       edgecolors='white', linewidths=0.5, zorder=3)
        for c in cellset:
            ax.scatter([], [], color=colors[c], label=c)
        ax.scatter([], [], color='#888', marker='o', label='• fulfillment')
        ax.scatter([], [], color='#888', marker='s', label='■ store')
        ax.axhline(0, color='k', lw=0.8)
        ax.axvline(0, color='k', lw=0.8)
        ax.set_xlabel('Total-labor saving % vs reference (→ better)')
        ax.set_ylabel('Throughput gain % vs reference (↑ better)')
        ax.set_title(f'What-if scenarios vs "{args.reference}"  —  point = cell × channel × arm')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, title='scenario / channel')
        png = os.path.join(args.base_dir, 'whatif_delta.png')
        fig.savefig(png, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f'wrote {png}')

        print(f'\nMedian Δ vs "{args.reference}" by cell × channel:')
        agg: dict = {}
        for r in rows:
            agg.setdefault((r['cell'], r['channel']), []).append((r['dlabor_pct'], r['dthr_pct']))
        for (cell, ch), vals in sorted(agg.items()):
            dl = statistics.median(v[0] for v in vals)
            dt = statistics.median(v[1] for v in vals)
            print(f'  {cell:16} {ch:12}  Δlabor={dl:+5.1f}%   Δthr={dt:+6.1f}%')


if __name__ == '__main__':
    main()
