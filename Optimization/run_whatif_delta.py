"""run_whatif_delta.py — compare what-if scenarios against a reference cell.

Reads the four steady-state success metrics straight from each scenario's sim_*.db (no run_analysis
needed), keyed by (pair, pick-config, channel, arm), and diffs every cell against the reference cell:

    task makespan  (Σ task time = total labor)          batch makespan (last-picker finish)
    throughput / task makespan (items / Σ task time)    throughput / batch makespan (items / makespan)

Emits whatif_delta.csv (all four deltas), whatif_delta.json (the docs site's matrix table), and a
labor-saving-vs-throughput-gain scatter (one point per cell×channel×arm) plus a median summary.  This
is what makes a scheduling win legible: LPT should show Δbatch-makespan ↓ and Δthroughput/batch ↑
while Δtask-makespan (labor) and Δthroughput/task stay ≈flat.

    python -m Optimization.run_whatif_delta <comparison_whatif_...>   # --reference defaults from config

Tree access goes through the versioned run-tree resolver (Optimization/runschema), never raw globs:
the previous relpath-splitting scan required a `<channel>` segment and therefore silently dropped
EVERY store-only run, whose sim DBs sit directly under `<config>/`.
"""
from __future__ import annotations

import argparse
import csv
import json
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


def _channel_of(cr) -> str:
    """The channel a run belongs to.  ChannelRun.channel is None on a store-only layout (no channel
    subdir) — that IS the store channel, so name it rather than emitting a blank column."""
    return cr.channel or 'store'


def _scan(rt, cell: str) -> dict:
    """{(pair, pickcfg, channel, arm): metrics} for one cell, via the run-tree resolver."""
    out = {}
    for _cell, cr, db in rt.sim_dbs(cell):
        m = _metrics(db)
        if m and m['task_ms']:
            out[(cr.pair, cr.config, _channel_of(cr), rt.strategy_of(db))] = m
    return out


def _cell_labels(layout_cell: dict) -> tuple[str, str]:
    """(layout, zoning_label) for one descriptor cell — the prose the docs matrix shows."""
    split = layout_cell.get('split')
    if not split:
        lay = 'whole aisle'
    else:
        lay = f"k={split.get('k')}" + (f", loss={split.get('capacity_loss', 0.0):.0%}"
                                       if split.get('capacity_loss') else '')
    z = layout_cell.get('zoning') or {}
    zone = z.get('mode', 'on') if z.get('enabled') else 'off'
    return lay, zone


def _write_delta_json(base_dir, rt, rows, reference, cell_names) -> str:
    """Write whatif_delta.json — the per-cell × per-channel MEDIAN table the docs site renders.

    Previously this file had no producer at all: docs/macros.py:whatif_matrix read it, and the only
    way to get one was to hand-copy and rename whatif_labor.json.  The four-metric key names
    (dthr_batch / dtask_ms / dbatch_ms / dthr_task) are the ones that macro prefers.
    """
    by_cell: dict = {}
    for r in rows:
        by_cell.setdefault(r['cell'], {}).setdefault(r['channel'], []).append(r)

    def _med(vs, key):
        vals = [v[key] for v in vs if v[key] == v[key]]      # drop NaN
        return statistics.median(vals) if vals else None

    descriptor = {c['name']: c for c in (rt.layout.get('cells') or ())}
    cells_out = []
    for name in cell_names:
        if name == reference:
            continue
        lay, zone = _cell_labels(descriptor.get(name, {}))
        by_channel = {}
        for ch, vs in sorted(by_cell.get(name, {}).items()):
            by_channel[ch] = {
                'dthr_batch': {'med': _med(vs, 'd_thr_batch_pct'), 'n': len(vs)},
                'dtask_ms'  : {'med': _med(vs, 'd_task_ms_pct'),   'n': len(vs)},
                'dbatch_ms' : {'med': _med(vs, 'd_batch_ms_pct'),  'n': len(vs)},
                'dthr_task' : {'med': _med(vs, 'd_thr_task_pct'),  'n': len(vs)},
            }
        cells_out.append({'name': name, 'layout': lay, 'zoning_label': zone,
                          'by_channel': by_channel})

    doc = {
        'schema_id': rt.schema_id,
        'reference': reference,
        'reference_label': reference,
        'metrics': ['dthr_batch', 'dtask_ms', 'dbatch_ms', 'dthr_task'],
        'window_batches': WIN,
        'arms': len({r['arm'] for r in rows}),
        'pairs': sorted({r['pair'] for r in rows}),
        'channels': sorted({r['channel'] for r in rows}),
        'cells': cells_out,
    }
    path = os.path.join(base_dir, 'whatif_delta.json')
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)
    return path


def _pct(a, b, higher_better):
    """Signed % change of b vs reference a; + always = better.  NaN on missing/zero ref/cell."""
    if a is None or b is None or not a or a != a or b != b:
        return float('nan')
    return (b - a) / a * 100 if higher_better else (a - b) / a * 100


def run(base_dir, reference=None, log=None):
    """Engine: diff every cell under base_dir vs the reference cell; write whatif_delta.csv +
    the labor-vs-throughput scatter, and return the CSV path.  Importable so the analysis hub
    calls it in-process (no argv).  A single-cell run has nothing to diff and is a no-op."""
    from Optimization.runschema import resolver_for
    _say = log.info if log is not None else print
    rt = resolver_for(base_dir)
    if reference is None:
        # Prefer the run's OWN descriptor (correct when re-analyzing an old sweep); fall back to the
        # currently-committed spec only when the descriptor has none.
        reference = rt.layout.get('reference')
    if reference is None:
        try:
            from Optimization.config.whatif_config import WHATIF
            reference = WHATIF.get('reference', 'k1_off')
        except Exception:
            reference = 'k1_off'

    cell_names = [name for name, _dir in rt.cells()]
    if reference not in cell_names:
        raise SystemExit(f'reference cell {reference!r} not found in {cell_names}')
    scans = {c: _scan(rt, c) for c in cell_names}
    ref = scans[reference]

    rows = []
    for cell in cell_names:
        if cell == reference:
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
    csv_path = os.path.join(base_dir, 'whatif_delta.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    _say(f'wrote {csv_path}  ({len(rows)} rows)')

    json_path = _write_delta_json(base_dir, rt, rows, reference, cell_names)
    _say(f'wrote {json_path}')

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
        ax.set_title(f'What-if scenarios vs "{reference}"  —  point = cell × channel × arm\n'
                     f'up at flat x = a scheduling win (batch makespan ↓ at flat task makespan)')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, title='scenario / channel')
        png = os.path.join(base_dir, 'whatif_delta.png')
        fig.savefig(png, dpi=150, bbox_inches='tight')
        plt.close(fig)
        _say(f'wrote {png}')

        _say(f'\nMedian Δ vs "{reference}" by cell × channel  '
             f'(+ = better; task_ms=labor, batch_ms=makespan):')
        agg: dict = {}
        for r in rows:
            agg.setdefault((r['cell'], r['channel']), []).append(r)
        _med = lambda vs, k: (statistics.median(v[k] for v in vs if v[k] == v[k])
                              if any(v[k] == v[k] for v in vs) else float('nan'))
        for (cell, ch), vs in sorted(agg.items()):
            _say(f'  {cell:16} {ch:12}  '
                 f'Δtask_ms={_med(vs, "d_task_ms_pct"):+5.1f}%  '
                 f'Δbatch_ms={_med(vs, "d_batch_ms_pct"):+5.1f}%  '
                 f'Δthr/batch={_med(vs, "d_thr_batch_pct"):+6.1f}%  '
                 f'Δthr/task={_med(vs, "d_thr_task_pct"):+6.1f}%')
    return csv_path


def main():
    from Optimization.runschema import resolve_base_dir
    ap = argparse.ArgumentParser(description='Diff what-if scenarios vs a reference cell.')
    ap.add_argument('base_dir')
    ap.add_argument('--reference', default=None,
                    help="reference cell to diff against (default: the run's own run_layout.json "
                         "reference, else WHATIF['reference'] from whatif_config.py)")
    args = ap.parse_args()
    run(resolve_base_dir(args.base_dir), args.reference)


if __name__ == '__main__':
    main()
