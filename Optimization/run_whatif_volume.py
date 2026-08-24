"""run_whatif_volume.py — cross-cell CUMULATIVE-VOLUME what-if: how much work each scheduler
finishes per unit of elapsed time (re-analyze, no re-sim).

The per-config `throughput.volume` graph can only see ONE channel-run leaf, so it compares
assignment functions inside a single cell.  The scheduler axis lives only in the CELL directory name
(k1_off_rr / k1_off_lpt), so the round-robin-vs-LPT comparison has to happen here, at the run root,
alongside run_whatif_delta and run_whatif_labor.

  per (channel, scheduler, assignment-fn, initial):
    elapsed_hours      = Σ duration      / 3600     running wall-clock the work took
    labor_hours        = Σ task_makespan / 3600     running serial labor (what the work COST)
    mean_thr_items_hr  = items / elapsed_hours      the chord slope of the cumulative curve
    auc_gain_vs_rr_pct area between this cell's cumulative curve and the reference cell's,
                       over the reference's own area, on a shared hour grid

WHY THIS SEPARATES THE TWO LEVERS.  The assignment function changes labor_hours (less work).  The
scheduler does not — it changes elapsed_hours (the same work, less picker idle time).  Plotting
cumulative volume against elapsed hours makes that visible as a steeper line reaching the same
volume sooner, which is the honest picture of "more throughput at flat labor".

*** CAVEAT: hours are SIM-MODELED pick-time (batch_stats SECONDS / 3600), NOT wall-clock or
    staffing hours.  A modeled-effort figure for comparing arms, not a schedule. ***

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

from Warehouse.kernel.timeline import epochs as batch_epochs

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from Schema import compat as _compat
from Schema import connect
from Optimization.Performance_Evaluations.common import chartkit as _ck
from Optimization.Performance_Evaluations.common import io as _io
from Optimization.run_whatif_delta import _channel_of
from Optimization.run_whatif_labor import (
    PER_HOUR, _HOURS_NOTE, _SCHED_COLOR, _channels, _data_xlim, _headroom, _ordered,
    _panel_tag, _parse_arm, _scheduler_of, canonical_arm_order)

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
    # `epochs` gives each batch's START; cumsum gives each batch's END, which is what a
    # cumulative curve plots — so the series here is `epoch + duration`, i.e. the same
    # numbers, from the one named definition of a cross-batch timeline rather than an
    # anonymous cumsum in an analysis module.  See Warehouse/kernel/timeline.py, which also
    # records why MS_PER_HOUR is the divisor it is.
    ends = np.array(batch_epochs(dur), dtype=float) + dur
    return {'hours': ends / PER_HOUR,
            'items': np.cumsum(items),
            'labor_hours': float(task.sum()) / PER_HOUR}


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
    """The headline curves: cumulative items vs elapsed hours for ONE arm under both
    schedulers, per channel.  Elapsed hours and item counts are commensurate across the
    panels, so both axes share ONE honest scale; the per-scheduler rate/finish figures
    live in the gutter legend, off the axes; the wall-clock caveat is the subtitle."""
    chans = _channels(rows)
    panels = []
    for ch in chans:
        arm = _pick_best(rows, ch)
        series = []
        for cell in cells:
            for key, s in scans[cell].items():
                _pair, _cfg, channel, a = key
                if channel != ch or a != arm:
                    continue
                m = _metrics_of(s)
                if not m:
                    continue
                series.append((_scheduler_of(cell), s, m))
                break
        panels.append((ch, arm, series))
    labels = [f"{sched}: {m['mean_thr_items_hr']:,.0f} items/h · {m['elapsed_hours']:.2f} h"
              for _ch, _arm, series in panels for sched, _s, m in series]
    chart = _ck.make(panels=len(chans), panel_w=5.6, panel_h=4.2,
                     legend_labels=labels or ('round_robin',))
    for ax, (ch, arm, series) in zip(chart.axes, panels):
        lines = []
        for sched, s, m in series:
            ln, = ax.plot(s['hours'], s['items'], color=_SCHED_COLOR.get(sched, '#888888'),
                          lw=2.0,
                          label=f"{sched}: {m['mean_thr_items_hr']:,.0f} items/h · "
                                f"{m['elapsed_hours']:.2f} h")
            ax.plot([s['hours'][-1]], [s['items'][-1]], 'o', ms=6,
                    color=_SCHED_COLOR.get(sched, '#888888'))
            lines.append(ln)
        ax.set_xlabel('elapsed hours')
        ax.set_ylabel('cumulative items picked')
        if lines:
            chart.legend(lines, title=ch)
    xmax = max((float(s['hours'][-1]) for _ch, _arm, series in panels
                for _sched, s, _m in series), default=1.0)
    for ax in chart.axes:
        ax.set_xlim(0.0, xmax * 1.05)
    _ck.shared_ylim(chart.axes,
                    [np.concatenate([s['items'] for _sched, s, _m in series])
                     if series else np.array([0.0])
                     for _ch, _arm, series in panels], include=(0.0,))
    for ax, (ch, arm, _series) in zip(chart.axes, panels):
        _headroom(ax, 0.14)                    # equal per panel — the scale stays shared
        # "Same work" is only true because this is ONE arm under two schedulers — across
        # DIFFERENT arms the volumes differ (measured ~6.8%), so the panel tag names the
        # arm to keep the claim from being read more widely than the data supports.
        _panel_tag(ax, f'{ch}: {arm or "?"}')
    chart.title('Cumulative volume vs elapsed time',
                subtitle='elapsed hours = modeled sim pick-time, not wall-clock · '
                         'each panel: one arm under both schedulers')
    chart.save(out_path)


def _gain_png(rows, out_path, order=()):
    """Median LPT-over-RR gain in whole-run mean throughput (the chord slope), one
    horizontal bar per arm in the canonical order, ONE shared x scale across panels."""
    by_chan: dict = {}
    for r in rows:
        v = r['thr_gain_vs_ref_pct']
        if r['scheduler'] == 'round_robin' or v != v:
            continue
        by_chan.setdefault(r['channel'], {}).setdefault(r['assignment'], []).append(v)
    if not by_chan:
        return
    chans = sorted(by_chan)
    n_max = max(len(d) for d in by_chan.values())
    chart = _ck.make(panels=len(chans), panel_w=5.2,
                     panel_h=_ck.height_for_categories(n_max), legend='none')
    allmeds, drawn = [], []
    for ax, ch in zip(chart.axes, chans):
        d = by_chan[ch]
        names = _ordered(sorted(d), order)
        meds = [float(np.median(d[a])) for a in names]
        ypos = [len(names) - 1 - i for i in range(len(names))]   # canonical order, top-down
        ax.barh(ypos, meds, color=_SCHED_COLOR['lpt'], height=0.62, alpha=0.85)
        ax.set_yticks(ypos)
        ax.set_yticklabels(names, fontsize=8)
        ax.set_ylim(-0.6, len(names) - 0.4)
        ax.axvline(0, color='#222222', lw=0.9)
        ax.set_xlabel('median throughput uplift, LPT vs round-robin % (→ better)',
                      fontsize=9)
        ax.grid(False, axis='y')
        allmeds += meds
        _headroom(ax, 1.4 / (len(names) + 1))
        _panel_tag(ax, ch)
        drawn.append(ax)
    for ax in drawn:
        _data_xlim(ax, allmeds, include=(0.0,))                  # ONE shared x scale
    chart.title('LPT throughput uplift by assignment fn',
                subtitle='median % gain in whole-run mean items/hour (chord slope) · '
                         'same scale on every panel')
    chart.save(out_path)


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
    # The canonical arm ordering shared with the labor writer's figures, and the
    # provenance footer stamped through the chartkit footer band.
    arm_order = canonical_arm_order(rows, reference)
    prev_footer = getattr(_io, '_FOOTER', None)
    _io.set_footer(f'whatif volume re-analysis · {os.path.basename(os.path.normpath(base_dir))}'
                   f' · reference={reference} · modeled sim pick-time hours, not wall-clock')
    try:
        _curves_png(rows, scans, cells, os.path.join(png_dir, 'whatif_volume_curves.png'))
        _gain_png(rows, os.path.join(png_dir, 'whatif_volume_uplift_bars.png'), arm_order)
    finally:
        _io.set_footer(prev_footer)
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
