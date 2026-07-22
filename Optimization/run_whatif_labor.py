"""run_whatif_labor.py — cross-cell THROUGHPUT / LABOR-HOURS what-if (re-analyze, no re-sim).

Reads the SAME per-cell sim_*.db as run_whatif_delta, but tells the story in MODELED HOURS and
adds the picker-scheduler axis that lives only in the CELL directory name (k1_off_rr / k1_off_lpt).

  per (channel, scheduler, assignment-fn, initial):
    labor_hours  = Σ task_makespan / 3.6e6     total serial labor over the run (all batches)
    batch_hours  = Σ duration      / 3.6e6     parallel wall-clock makespan
    thr_items_hr = mean thr_batch  × 3.6e6     items per hour
    labor_saved  = labor(fifo) − labor(arm)    absolute hours a better assignment fn saves vs FIFO

Two levers: the ASSIGNMENT FUNCTION sets total labor (labor_saved vs FIFO); the LPT SCHEDULER
converts leftover picker imbalance into throughput at ~flat labor (batch_hours ↓, labor flat).

*** CAVEAT: hours here are SIM-MODELED pick-time (batch_stats ms ÷ 3.6e6), NOT wall-clock or
    staffing hours.  They are a modeled-effort figure for comparing arms, not a schedule. ***

Reuses run_whatif_delta's engine (_scan-style glob + _metrics steady-state means); adds full-run
sums for the true total labor.  Outputs to the run root: whatif_labor.csv, whatif_labor.json
(whatif_matrix()-compatible), and four PNGs.

  python -m Optimization.run_whatif_labor <comparison_whatif_...> [--baseline fifo]
         [--baseline-initial match|uni|opt] [--reference k1_off_rr] [--pairs sum|median]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sqlite3
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from Optimization.run_whatif_delta import _metrics, WIN   # steady-state (last WIN) means

MS_PER_HOUR = 3.6e6
_SCHED = {'rr': 'round_robin', 'lpt': 'lpt'}


def _scheduler_of(cell: str) -> str:
    """'k1_off_lpt' -> 'lpt'; 'k1_off_rr' -> 'round_robin' (the swept scheduler is the cell suffix)."""
    return _SCHED.get(cell.rsplit('_', 1)[-1], 'round_robin')


def _parse_arm(arm: str):
    """'uni_rank_labor_norsl' -> ('uni', 'rank_labor', 'norsl'); assignment may contain '_'."""
    reslot = 'norsl' if arm.endswith('_norsl') else ''
    body = arm[:-len('_norsl')] if reslot else arm
    initial, _, assignment = body.partition('_')
    return initial, assignment, reslot


def _hours(db: str):
    """Full-run SUMS over ALL batches (the true totals): labor/batch hours + items + n_batches."""
    con = sqlite3.connect(db)
    try:
        row = con.execute('SELECT SUM(task_makespan), SUM(duration), SUM(total_items), COUNT(*) '
                          'FROM batch_stats').fetchone()
        if not row or row[0] is None:
            return None
        return {'labor_hours': row[0] / MS_PER_HOUR, 'batch_hours': row[1] / MS_PER_HOUR,
                'items': int(row[2] or 0), 'n_batches': int(row[3] or 0)}
    finally:
        con.close()


def _scan_labor(cell_dir: str) -> dict:
    """{(pair, pickcfg, channel, arm): {**steady means (whatif_delta), **full-run hours}}."""
    out = {}
    for db in glob.glob(os.path.join(cell_dir, '**', 'sim_*.db'), recursive=True):
        if db.endswith('.keyframes.db'):
            continue
        rel = os.path.relpath(db, cell_dir).replace('\\', '/').split('/')
        if len(rel) < 4:                       # expect pair/pickcfg/channel/sim_<arm>.db
            continue
        m, h = _metrics(db), _hours(db)
        if m and h and m.get('task_ms'):
            out[(rel[0], rel[1], rel[2], os.path.basename(db)[4:-3])] = {**m, **h}
    return out


def _pct(a, b, higher_better):
    """Signed % change of b vs reference a; + always = better.  NaN on missing/zero/NaN."""
    if a is None or b is None or not a or a != a or b != b:
        return float('nan')
    return (b - a) / a * 100 if higher_better else (a - b) / a * 100


def _med(vals):
    vals = [v for v in vals if v == v]
    return statistics.median(vals) if vals else float('nan')


# ── charts ───────────────────────────────────────────────────────────────────────────────
_SCHED_COLOR = {'round_robin': '#4c78a8', 'lpt': '#f58518'}
_HOURS_NOTE = 'modeled sim pick-time hours (batch_stats ms / 3.6e6) — not wall-clock'


def _agg(rows, keyfn, field, how):
    """Aggregate `field` over rows grouped by keyfn: how in {'sum','mean'}."""
    groups: dict = {}
    for r in rows:
        v = r.get(field)
        if v is None or v != v:
            continue
        groups.setdefault(keyfn(r), []).append(v)
    return {k: (sum(vs) if how == 'sum' else statistics.mean(vs)) for k, vs in groups.items()}


def _channels(rows):
    return sorted(set(r['channel'] for r in rows))


def _throughput_scatter(rows, out_path):
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(6.2 * len(chans), 5.2), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch]
        # one point per (scheduler, assignment): labor summed over pairs, throughput averaged
        labor = _agg(sub, lambda r: (r['scheduler'], r['assignment']), 'labor_hours', 'sum')
        thr = _agg(sub, lambda r: (r['scheduler'], r['assignment']), 'thr_items_hr', 'mean')
        for sched in ('round_robin', 'lpt'):
            xs = [labor[k] for k in labor if k[0] == sched]
            ys = [thr[k] for k in labor if k[0] == sched]
            ax.scatter(xs, ys, s=60, color=_SCHED_COLOR[sched], edgecolors='white',
                       linewidths=0.5, label=sched, zorder=3)
        ax.set_xlabel('total labor hours (Σ task-makespan)')
        ax.set_ylabel('throughput (items / hour)')
        ax.set_title(f'{ch}: throughput vs labor hours')
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, title='scheduler')
    fig.suptitle('Throughput vs labor hours — LPT lifts throughput at flat labor  '
                 f'({_HOURS_NOTE})', fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _labor_saved_bars(rows, baseline, out_path):
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(6.2 * len(chans), 6.4), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch and r['assignment'] != baseline]
        # labor saved is ~scheduler-independent (LPT saves 0 labor): sum over pairs, mean over sched/initial
        saved = _agg(sub, lambda r: r['assignment'], 'labor_saved', 'mean')
        items = sorted(saved.items(), key=lambda kv: kv[1])
        names = [k for k, _ in items]
        vals = [v for _, v in items]
        colors = ['#54a24b' if v >= 0 else '#e45756' for v in vals]
        ax.barh(range(len(names)), vals, color=colors)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8)
        ax.axvline(0, color='k', lw=0.8)
        ax.set_xlabel(f'labor hours saved vs {baseline} (mean over pairs/schedulers)')
        ax.set_title(f'{ch}: labor hours saved by assignment fn')
        ax.grid(alpha=0.3, axis='x')
    fig.suptitle(f'Total labor hours saved by assignment function (baseline = {baseline})  '
                 f'({_HOURS_NOTE})', fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _batch_hours_bars(rows, out_path):
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(7.0 * len(chans), 6.4), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch]
        rr = _agg([r for r in sub if r['scheduler'] == 'round_robin'],
                  lambda r: r['assignment'], 'batch_hours', 'sum')
        lpt = _agg([r for r in sub if r['scheduler'] == 'lpt'],
                   lambda r: r['assignment'], 'batch_hours', 'sum')
        names = sorted(set(rr) | set(lpt), key=lambda a: rr.get(a, 0), reverse=True)
        y = range(len(names))
        ax.barh([i + 0.2 for i in y], [rr.get(a, 0) for a in names], height=0.4,
                color=_SCHED_COLOR['round_robin'], label='round_robin')
        ax.barh([i - 0.2 for i in y], [lpt.get(a, 0) for a in names], height=0.4,
                color=_SCHED_COLOR['lpt'], label='lpt')
        ax.set_yticks(list(y))
        ax.set_yticklabels(names, fontsize=8)
        ax.set_xlabel('batch-makespan hours (Σ duration)')
        ax.set_title(f'{ch}: batch-time hours, round-robin vs LPT')
        ax.grid(alpha=0.3, axis='x')
        ax.legend(fontsize=8)
    fig.suptitle(f'LPT shortens batch makespan (wall-clock) at flat labor  ({_HOURS_NOTE})',
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _uplift_bars(uplift, out_path):
    chans = sorted(set(ch for ch, _a in uplift))
    fig, axes = plt.subplots(1, len(chans), figsize=(6.0 * len(chans), 6.4), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        items = sorted(((a, uplift[(ch, a)]) for _c, a in uplift if _c == ch),
                       key=lambda kv: kv[1])
        names = [a for a, _v in items]
        vals = [v for _a, v in items]
        ax.barh(range(len(names)), vals, color=_SCHED_COLOR['lpt'])
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8)
        ax.axvline(0, color='k', lw=0.8)
        ax.set_xlabel('throughput uplift % (LPT vs round-robin)')
        ax.set_title(f'{ch}: scheduler throughput uplift')
        ax.grid(alpha=0.3, axis='x')
    fig.suptitle('LPT throughput uplift at ~flat labor — the scheduling win', fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def run(base_dir, baseline='fifo', baseline_initial='match', reference=None, pairs='sum', log=None):
    """Engine: cross-cell throughput / labor-hours over base_dir; write whatif_labor.csv/json +
    the four PNGs, and return the CSV path.  Importable so the analysis hub calls it in-process
    (no argv).  `pairs` in {'sum','median'} blends the catalogs; a single-cell run still emits
    the CSV/JSON (its cross-cell deltas are empty)."""
    import types
    args = types.SimpleNamespace(base_dir=base_dir, baseline=baseline,
                                 baseline_initial=baseline_initial, reference=reference, pairs=pairs)
    if args.reference is None:
        try:
            from Optimization.whatif_config import WHATIF
            args.reference = WHATIF.get('reference', 'k1_off_rr')
        except Exception:
            args.reference = 'k1_off_rr'

    cells = [d for d in sorted(os.listdir(args.base_dir))
             if os.path.isdir(os.path.join(args.base_dir, d)) and not d.startswith('_')]
    if not cells:
        raise SystemExit(f'no scenario cells in {args.base_dir}')
    scans = {c: _scan_labor(os.path.join(args.base_dir, c)) for c in cells}

    # ── long rows at (cell, pair, pickcfg, channel, arm) grain ─────────────────────────────
    rows = []
    for cell in cells:
        sched = _scheduler_of(cell)
        for (pair, pickcfg, channel, arm), m in scans[cell].items():
            initial, assignment, _rsl = _parse_arm(arm)
            rows.append({
                'cell': cell, 'scheduler': sched, 'pair': pair, 'pickcfg': pickcfg,
                'channel': channel, 'arm': arm, 'initial': initial, 'assignment': assignment,
                'labor_hours': m['labor_hours'], 'batch_hours': m['batch_hours'],
                'thr_items_hr': (m['thr_batch'] or 0) * MS_PER_HOUR,
                'thr_task_items_hr': (m['thr_task'] or 0) * MS_PER_HOUR,
                'task_ms': m['task_ms'], 'batch_ms': m['batch_ms'],
                'thr_batch': m['thr_batch'], 'items': m['items'], 'n_batches': m['n_batches'],
            })

    # ── labor hours saved vs the baseline assignment fn (same cell/pair/pickcfg/channel) ──
    base = {}
    for r in rows:
        if r['assignment'] == args.baseline:
            base[(r['cell'], r['pair'], r['pickcfg'], r['channel'], r['initial'])] = r['labor_hours']
    for r in rows:
        bi = r['initial'] if args.baseline_initial == 'match' else args.baseline_initial
        b = base.get((r['cell'], r['pair'], r['pickcfg'], r['channel'], bi))
        r['labor_saved'] = (b - r['labor_hours']) if b is not None else float('nan')

    csv_path = os.path.join(args.base_dir, 'whatif_labor.csv')
    cols = ['cell', 'scheduler', 'pair', 'pickcfg', 'channel', 'arm', 'initial', 'assignment',
            'labor_hours', 'batch_hours', 'thr_items_hr', 'thr_task_items_hr', 'labor_saved',
            'items', 'n_batches']
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r['channel'], r['assignment'], r['scheduler'],
                                                r['initial'], r['pair'])))
    print(f'wrote {csv_path}  ({len(rows)} rows)')

    # ── scheduler uplift (lpt vs reference) per (channel, assignment) ──────────────────────
    ref_scan = scans.get(args.reference, {})
    uplift_samples: dict = {}
    for cell in cells:
        if cell == args.reference:
            continue
        for key, m in scans[cell].items():
            r = ref_scan.get(key)
            if not r:
                continue
            _pair, _pc, channel, arm = key
            _i, assignment, _rsl = _parse_arm(arm)
            uplift_samples.setdefault((channel, assignment), []).append(
                _pct(r['thr_batch'], m['thr_batch'], True))
    uplift = {k: _med(v) for k, v in uplift_samples.items()}

    # ── whatif_matrix()-compatible JSON (medians vs reference, per treatment cell) ─────────
    json_cells = []
    for cell in cells:
        if cell == args.reference:
            continue
        by_channel: dict = {}
        for key, m in scans[cell].items():
            r = ref_scan.get(key)
            if not r:
                continue
            ch = key[2]
            by_channel.setdefault(ch, {'dtask_ms': [], 'dthr_batch': [], 'hours': []})
            by_channel[ch]['dtask_ms'].append(_pct(r['task_ms'], m['task_ms'], False))
            by_channel[ch]['dthr_batch'].append(_pct(r['thr_batch'], m['thr_batch'], True))
            by_channel[ch]['hours'].append(m['labor_hours'])
        chan_out = {}
        for ch, d in by_channel.items():
            dt, dth = [x for x in d['dtask_ms'] if x == x], [x for x in d['dthr_batch'] if x == x]
            chan_out[ch] = {
                'dtask_ms': {'med': _med(dt), 'min': min(dt) if dt else None, 'max': max(dt) if dt else None},
                'dthr_batch': {'med': _med(dth), 'min': min(dth) if dth else None, 'max': max(dth) if dth else None},
                'n': len(dth), 'pos_thr': sum(1 for x in dth if x > 0),
                'labor_hours_med': _med(d['hours']),
            }
        json_cells.append({
            'name': cell, 'k': 1, 'capacity_loss': 0.0, 'zoning': 'off', 'zoning_label': 'off',
            'layout': f'no split · {_scheduler_of(cell)} scheduler', 'by_channel': chan_out,
        })
    summary = {
        'reference': args.reference, 'reference_label': f'{_scheduler_of(args.reference)} scheduler (baseline)',
        'n_batches': max((r['n_batches'] for r in rows), default=0),
        'arms': len(set(r['arm'] for r in rows)),
        'pairs': sorted(set(r['pair'] for r in rows)),
        'channels': _channels(rows),
        'metric': f'steady-state medians over the last {WIN} batches vs the {args.reference} reference cell',
        'labor_baseline': args.baseline, 'hours_note': _HOURS_NOTE,
        'cells': json_cells,
    }
    json_path = os.path.join(args.base_dir, 'whatif_labor.json')
    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f'wrote {json_path}')

    # ── charts ─────────────────────────────────────────────────────────────────────────────
    _throughput_scatter(rows, os.path.join(args.base_dir, 'whatif_labor_throughput_scatter.png'))
    _labor_saved_bars(rows, args.baseline, os.path.join(args.base_dir, 'whatif_labor_saved_bars.png'))
    _batch_hours_bars(rows, os.path.join(args.base_dir, 'whatif_batch_hours_rr_vs_lpt.png'))
    _uplift_bars(uplift, os.path.join(args.base_dir, 'whatif_scheduler_uplift.png'))
    for p in ('whatif_labor_throughput_scatter.png', 'whatif_labor_saved_bars.png',
              'whatif_batch_hours_rr_vs_lpt.png', 'whatif_scheduler_uplift.png'):
        print(f'wrote {os.path.join(args.base_dir, p)}')

    # ── console headline ───────────────────────────────────────────────────────────────────
    print(f'\nHeadline (baseline={args.baseline}, {_HOURS_NOTE}):')
    for ch in _channels(rows):
        sub = [r for r in rows if r['channel'] == ch]
        saved = _agg([r for r in sub if r['assignment'] != args.baseline],
                     lambda r: r['assignment'], 'labor_saved', 'mean')
        best = max(saved.items(), key=lambda kv: kv[1]) if saved else ('n/a', float('nan'))
        ups = [uplift[(ch, a)] for _c, a in uplift if _c == ch and uplift[(ch, a)] == uplift[(ch, a)]]
        print(f'  {ch:12}  best labor-saver vs {args.baseline}: {best[0]} ({best[1]:+.1f} h)  '
              f'median LPT throughput uplift: {_med(ups):+.1f}%')
    return csv_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir')
    ap.add_argument('--baseline', default='fifo',
                    help="assignment fn to measure labor-hours-saved against (default 'fifo')")
    ap.add_argument('--baseline-initial', default='match', choices=('match', 'uni', 'opt'),
                    help="baseline arm's initial placement: 'match' compares opt-vs-opt / uni-vs-uni "
                         "(isolates the assignment fn); 'uni'/'opt' pins it (default 'match')")
    ap.add_argument('--reference', default=None,
                    help="scheduler reference cell (default WHATIF['reference'], else 'k1_off_rr')")
    ap.add_argument('--pairs', default='sum', choices=('sum', 'median'),
                    help='how to blend the two catalogs for hours (sum = additive workload; default sum)')
    args = ap.parse_args()
    run(args.base_dir, baseline=args.baseline, baseline_initial=args.baseline_initial,
        reference=args.reference, pairs=args.pairs)


if __name__ == '__main__':
    main()
