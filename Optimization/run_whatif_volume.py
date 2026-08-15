"""run_whatif_volume.py — cross-cell CUMULATIVE-VOLUME what-if: how much work each scheduler
finishes per unit of elapsed time (re-analyze, no re-sim).

The per-config `compare.volume_curve` graph can only see ONE channel-run leaf, so it compares
assignment functions inside a single cell.  The scheduler axis lives only in the CELL directory name
(k1_off_rr / k1_off_lpt), so the round-robin-vs-LPT comparison has to happen here, at the run root,
alongside run_whatif_delta and run_whatif_labor.

  per (channel, scheduler, assignment-fn, initial):
    elapsed_hours      = Σ duration      / 3.6e6    running wall-clock the work took
    labor_hours        = Σ task_makespan / 3.6e6    running serial labor (what the work COST)
    mean_thr_items_hr  = items / elapsed_hours      the chord slope of the cumulative curve
    auc_gain_vs_rr_pct area between this cell's cumulative curve and the reference cell's,
                       over the reference's own area, on a shared hour grid

WHY THIS SEPARATES THE TWO LEVERS.  The assignment function changes labor_hours (less work).  The
scheduler does not — it changes elapsed_hours (the same work, less picker idle time).  Plotting
cumulative volume against elapsed hours makes that visible as a steeper line reaching the same
volume sooner, which is the honest picture of "more throughput at flat labor".

*** CAVEAT: hours are SIM-MODELED pick-time (batch_stats ms / 3.6e6), NOT wall-clock or staffing
    hours.  A modeled-effort figure for comparing arms, not a schedule. ***

  python -m Optimization.run_whatif_volume <comparison_whatif_...> [--reference k1_off_rr]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_whatif_volume.py <dir>); `-m` form needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from Schema import compat as _compat
from Schema import connect
from Optimization.run_whatif_delta import _channel_of
from Optimization.run_whatif_labor import (
    MS_PER_HOUR, _HOURS_NOTE, _SCHED_COLOR, _channels, _parse_arm, _scheduler_of)

log = logging.getLogger('analysis')

#: Every table/column this module's SQL reads out of a sim DB (`_series`), validated against the
#: sim_db guaranteed surface in CI (Tests/architecture/test_schema_compatibility.py) — the
#: cumulative-volume numbers the docs pages cite cannot silently lose a source column.
REQUIRES = _compat.Requires(
    family='sim_db',
    label='run_whatif_volume series reader',
    tables={
        'batch_stats': ('batch_id', 'duration', 'task_makespan', 'total_items'),
    })


def _series(db: str):
    """(hours, items) cumulative arrays for one arm, ordered by batch.

    A VETTED file binds to its own schema vintage (`Schema.dataset.bind`, immutable=True — the
    escape-hatch `.con` still carries the same aggregate SQL, unchanged); an unvetted one
    (synthetic fixtures, cold archives) opens through `Schema.connect.read_only` with the same
    read-only + immutable promise, replacing the hand-built `?mode=ro&immutable=1` URI this
    function used to assemble.  Either way no `-wal`/`-shm` sidecar is ever left beside an
    archived copy.
    """
    from Optimization.persistence import Picking_Data  # noqa: F401 — registers the sim_db family
    from Schema import dataset as _dataset
    from Schema import identity as _identity
    try:
        ds = _dataset.bind(db, 'sim_db', requires=REQUIRES, immutable=True)
    except _compat.RequirementUnmet:
        raise            # a vetted file that cannot serve the declared read must fail LOUDLY
    except (_identity.SchemaError, sqlite3.Error):
        ds = None        # unvetted → the plain read-only open below, historical behavior
    con = ds.con if ds is not None else connect.read_only(db, row_factory=False, immutable=True)
    try:
        rows = con.execute('SELECT duration, total_items, task_makespan FROM batch_stats '
                           'ORDER BY batch_id').fetchall()
    except sqlite3.Error:
        return None
    finally:
        (ds if ds is not None else con).close()
    if not rows:
        return None
    dur = np.array([r[0] or 0.0 for r in rows], dtype=float)
    items = np.array([r[1] or 0 for r in rows], dtype=float)
    task = np.array([r[2] or 0.0 for r in rows], dtype=float)
    return {'hours': np.cumsum(dur) / MS_PER_HOUR,
            'items': np.cumsum(items),
            'labor_hours': float(task.sum()) / MS_PER_HOUR}


def _metrics_of(s):
    """Endpoint + area metrics for one cumulative series.  See volume_curve for why shape_index is
    a stability diagnostic and not a score."""
    hrs, items = s['hours'], s['items']
    if hrs.size == 0 or hrs[-1] <= 0:
        return None
    x = np.concatenate(([0.0], hrs))
    y = np.concatenate(([0.0], items))
    trapz = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz
    auc = float(trapz(y, x))
    T, I = float(hrs[-1]), float(items[-1])
    tri = T * I / 2.0
    return {'items': I, 'elapsed_hours': T, 'labor_hours': s['labor_hours'],
            'mean_thr_items_hr': I / T, 'auc_item_hours': auc,
            'shape_index': (auc / tri) if tri > 0 else float('nan')}


def _gain(a, b):
    """% area between curve `a` and reference `b`, over b's area, on the hours BOTH cover."""
    hi = min(float(a['hours'][-1]), float(b['hours'][-1]))
    if hi <= 0:
        return float('nan')
    grid = np.linspace(0.0, hi, 200)
    ya = np.interp(grid, np.concatenate(([0.0], a['hours'])), np.concatenate(([0.0], a['items'])))
    yb = np.interp(grid, np.concatenate(([0.0], b['hours'])), np.concatenate(([0.0], b['items'])))
    trapz = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz
    base = float(trapz(yb, grid))
    return (float(trapz(ya - yb, grid)) / base * 100.0) if base > 0 else float('nan')


def scan(rt, cell):
    """{(pair, config, channel, arm): series} for one cell, via the versioned resolver.

    The key deliberately EXCLUDES the cell, so the same key in two cells is the same arm under two
    schedulers — the join run_whatif_delta and run_whatif_labor already use.
    """
    out = {}
    for _c, cr, db in rt.sim_dbs(cell):
        s = _series(db)
        if s is not None:
            out[(cr.pair, cr.config, _channel_of(cr), rt.strategy_of(db))] = s
    return out


def build_rows(rt, cells, reference):
    ref_scan = None
    scans = {}
    for c in cells:
        scans[c] = scan(rt, c)
    ref_scan = scans.get(reference, {})
    rows = []
    for cell in cells:
        for key, s in scans[cell].items():
            m = _metrics_of(s)
            if not m:
                continue
            pair, cfg, channel, arm = key
            initial, assignment, _rsl = _parse_arm(arm)
            r = {'cell': cell, 'scheduler': _scheduler_of(cell), 'pair': pair, 'pickcfg': cfg,
                 'channel': channel, 'arm': arm, 'initial': initial, 'assignment': assignment}
            r.update({k: m[k] for k in ('items', 'elapsed_hours', 'labor_hours',
                                        'mean_thr_items_hr', 'auc_item_hours', 'shape_index')})
            ref = ref_scan.get(key)
            r['auc_gain_vs_ref_pct'] = _gain(s, ref) if (ref is not None and cell != reference) \
                else (0.0 if cell == reference else float('nan'))
            rm = _metrics_of(ref) if ref is not None else None
            r['thr_gain_vs_ref_pct'] = (
                (m['mean_thr_items_hr'] / rm['mean_thr_items_hr'] - 1.0) * 100.0
                if rm and rm['mean_thr_items_hr'] else float('nan'))
            r['labor_delta_vs_ref_pct'] = (
                (m['labor_hours'] / rm['labor_hours'] - 1.0) * 100.0
                if rm and rm['labor_hours'] else float('nan'))
            rows.append(r)
    return rows, scans


def _pick_best(rows, channel):
    """Highest-throughput arm in the reference cell for this channel — the one worth drawing."""
    sub = [r for r in rows if r['channel'] == channel and r['scheduler'] == 'round_robin']
    return max(sub, key=lambda r: r['mean_thr_items_hr'])['arm'] if sub else None


def _curves_png(rows, scans, cells, out_path):
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(7.2 * len(chans), 5.6), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        arm = _pick_best(rows, ch)
        drew = False
        for cell in cells:
            for key, s in scans[cell].items():
                _pair, _cfg, channel, a = key
                if channel != ch or a != arm:
                    continue
                m = _metrics_of(s)
                if not m:
                    continue
                sched = _scheduler_of(cell)
                ax.plot(s['hours'], s['items'], color=_SCHED_COLOR.get(sched, '#888888'), lw=2.0,
                        label=f"{sched}  {m['mean_thr_items_hr']:,.0f} items/h · {m['elapsed_hours']:.2f} h")
                ax.plot([s['hours'][-1]], [s['items'][-1]], 'o', ms=6,
                        color=_SCHED_COLOR.get(sched, '#888888'))
                drew = True
                break
        ax.set_xlabel('elapsed hours')
        ax.set_ylabel('cumulative items picked')
        # "Same work" is only true because this is ONE arm under two schedulers — across DIFFERENT
        # arms the volumes differ (measured ~6.8%), so the caption names the arm to keep the claim
        # from being read more widely than the data supports.
        ax.set_title(f'{ch}: {arm or "?"} — identical arm, two schedulers\n'
                     f'same work, less elapsed time', fontsize=10)
        ax.grid(alpha=0.3)
        if drew:
            ax.legend(fontsize=8, title='scheduler')
    fig.suptitle('Cumulative volume vs elapsed time — round-robin vs LPT',
                 fontsize=12, fontweight='bold')
    fig.text(0.5, 0.005, f'elapsed hours = {_HOURS_NOTE}', ha='center', va='bottom',
             fontsize=8, color='#555555')
    plt.tight_layout(rect=(0, 0.04, 1, 0.92))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _gain_png(rows, out_path):
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(7.6 * len(chans), 5.6), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch and r['scheduler'] != 'round_robin'
               and r['thr_gain_vs_ref_pct'] == r['thr_gain_vs_ref_pct']]
        by_arm = {}
        for r in sub:
            by_arm.setdefault(r['assignment'], []).append(r['thr_gain_vs_ref_pct'])
        if not by_arm:
            ax.set_axis_off()
            continue
        names = sorted(by_arm, key=lambda a: float(np.median(by_arm[a])))
        vals = [float(np.median(by_arm[a])) for a in names]
        ax.barh(names, vals, color=_SCHED_COLOR['lpt'], alpha=0.85)
        ax.axvline(0, color='grey', lw=1.0)
        ax.set_xlabel('median throughput uplift, LPT vs round-robin (%)')
        ax.set_title(f'{ch}', fontsize=10)
        ax.grid(axis='x', alpha=0.3)
        ax.tick_params(axis='y', labelsize=7)
    fig.suptitle('LPT throughput uplift over round-robin, by assignment function',
                 fontsize=12, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


_FIELDS = ['cell', 'scheduler', 'pair', 'pickcfg', 'channel', 'arm', 'initial', 'assignment',
           'items', 'elapsed_hours', 'labor_hours', 'mean_thr_items_hr', 'auc_item_hours',
           'shape_index', 'auc_gain_vs_ref_pct', 'thr_gain_vs_ref_pct', 'labor_delta_vs_ref_pct']


def _writer_tree(rt):
    """The resolver this module's OUTPUTS render through — `rt` itself, or the head contract.

    A re-analysis reads an OLD run through the run's OWN contract (`rt`), but the artifacts this
    module WRITES are declared by whatever contract is current: one committed vintage
    (e69b6d392929, the 2026-07-29 scheduler sweeps) predates whatif_volume_csv/_json entirely,
    and `rt.path` on it would KeyError.  For such runs the outputs follow the HEAD document at
    the same root — exactly the strings the old hand-joins produced (today's basenames onto the
    old run root), so nothing moves; every READ still goes through the run's own document.
    """
    if 'whatif_volume_csv' in rt.artifacts and 'whatif_volume_json' in rt.artifacts:
        return rt
    from Optimization.runschema import contract as _contract
    from Optimization.runschema.resolver import RunTree
    head = _contract.head()
    doc = _contract.load(head) if head else None
    if doc is None:
        raise KeyError("this run's contract predates the whatif_volume artifacts and no head "
                       "contract is committed to name them")
    return RunTree(rt.base, doc, layout=rt.layout)


def run(base_dir: str, reference: str | None = None, log=log) -> list:
    from Optimization import runschema
    rt = runschema.resolver_for(base_dir)
    cells = [n for n, _d in rt.cells()]
    if len(cells) < 2:
        log.info('  whatif_volume: single-cell run — nothing to compare across schedulers')
        return []
    reference = reference or rt.layout.get('reference') or cells[0]
    rows, scans = build_rows(rt, cells, reference)
    if not rows:
        log.info('  whatif_volume: no readable arms')
        return []

    wt = _writer_tree(rt)
    csv_path = wt.path('whatif_volume_csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in _FIELDS})
    log.info(f'  wrote {csv_path}  ({len(rows)} rows)')

    with open(wt.path('whatif_volume_json'), 'w', encoding='utf-8') as fh:
        json.dump({'reference': reference, 'hours_note': _HOURS_NOTE, 'rows': rows}, fh,
                  indent=2, default=float)

    # The volume PNGs are covered by the whatif_labor_pngs glob (whatif_*.png); the star is in the
    # filename segment, so the template's directory is the contract-rendered home for them.
    png_dir = os.path.dirname(wt.path('whatif_labor_pngs'))
    _curves_png(rows, scans, cells, os.path.join(png_dir, 'whatif_volume_curves.png'))
    _gain_png(rows, os.path.join(png_dir, 'whatif_volume_uplift_bars.png'))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('base_dir')
    ap.add_argument('--reference', default=None, help='baseline cell (default: the run descriptor)')
    a = ap.parse_args(argv)
    from Optimization import runschema
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    rows = run(runschema.resolve_base_dir(a.base_dir), a.reference)
    return 0 if rows else 1


if __name__ == '__main__':
    raise SystemExit(main())
