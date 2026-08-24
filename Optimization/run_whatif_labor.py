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

Reuses run_whatif_delta's engine (resolver-driven scan + _metrics steady-state means); adds full-run
sums for the true total labor.  Outputs to the run root: the whatif_labor CSV + JSON
(whatif_matrix()-compatible), and four PNGs.  All output paths render through the run-tree
contract (`rt.path(...)` / the whatif PNG glob's directory) — this module is the declared writer
of those artifacts, and Tests/integration/test_writer_paths_golden.py pins the rendered strings
to the literals the old hand-joins produced.

  python -m Optimization.run_whatif_labor <comparison_whatif_...> [--baseline fifo]
         [--baseline-initial match|uni|opt] [--reference k1_off_rr] [--pairs sum|median]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_whatif_labor.py <dir>); `-m` form needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from Schema import compat as _compat
from Schema import connect
from Optimization.Performance_Evaluations.common import chartkit as _ck
from Optimization.Performance_Evaluations.common import io as _io
from Optimization.Performance_Evaluations.common.stats_core import census
from Optimization.run_whatif_delta import _metrics, _channel_of, WIN   # steady-state (last WIN) means

# The suite's one divisor, imported rather than restated -- this module used to carry
# its own `MS_PER_HOUR = 3.6e6`, a second copy of the same wrong number.
from Optimization.Performance_Evaluations.common.units import PER_HOUR
_SCHED = {'rr': 'round_robin', 'lpt': 'lpt'}

#: Every table/column THIS module's own SQL reads out of a sim DB (`_hours`); the steady-state
#: means it merges in come through `run_whatif_delta._metrics`, which is covered by THAT module's
#: declaration.  Validated against the sim_db guaranteed surface in CI
#: (Tests/architecture/test_schema_compatibility.py).
REQUIRES = _compat.Requires(
    family='sim_db',
    label='run_whatif_labor hours reader',
    tables={
        'batch_stats': ('duration', 'task_makespan', 'total_items'),
    })


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
    """Full-run SUMS over ALL batches (the true totals): labor/batch hours + items + n_batches.

    Read-only + immutable for the same reason as `run_whatif_delta._metrics`: a WAL-mode DB opened
    any other way leaves `-wal`/`-shm` sidecars beside an archived ~1 GB file.
    """
    con = connect.read_only(db, row_factory=False, immutable=True)
    try:
        row = con.execute('SELECT SUM(task_makespan), SUM(duration), SUM(total_items), COUNT(*) '
                          'FROM batch_stats').fetchone()
        if not row or row[0] is None:
            return None
        return {'labor_hours': row[0] / PER_HOUR, 'batch_hours': row[1] / PER_HOUR,
                'items': int(row[2] or 0), 'n_batches': int(row[3] or 0)}
    finally:
        con.close()


def _scan_labor(rt, cell: str) -> dict:
    """{(pair, pickcfg, channel, arm): {**steady means (whatif_delta), **full-run hours}}.

    Walks via the versioned run-tree resolver.  The previous relpath scan required a `<channel>`
    segment (`len(rel) < 4: continue`) and so returned NOTHING for a store-only run.
    """
    out = {}
    for _cell, cr, db in rt.sim_dbs(cell):
        m, h = _metrics(db), _hours(db)
        if m and h and m.get('task_ms'):
            out[(cr.pair, cr.config, _channel_of(cr), rt.strategy_of(db))] = {**m, **h}
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
# Every figure below draws through the chartkit contract (Performance_Evaluations/common):
# gutter legends that cannot touch the data region, a reserved provenance footer band
# (stamped via io.set_footer around the render), short titles with the context demoted to a
# subtitle, and data-hugging axes that say so out loud whenever they exclude zero or a
# sibling panel uses a different scale.
_SCHED_COLOR = {'round_robin': '#4c78a8', 'lpt': '#f58518'}
_ARM_HUE = '#5b7fa6'   # the one neutral hue for signed per-arm bars — sign is the zero line's job
_HOURS_NOTE = ('modeled sim pick-time hours (batch_stats seconds / 3600) — not wall-clock')


def canonical_arm_order(rows, reference, baseline='fifo'):
    """THE one arm ordering every what-if figure shares (this writer and the volume writer
    alike): assignment functions sorted by mean labor hours saved vs `baseline` in the
    `reference` cell, largest saving first; arms with no reference sample sort last,
    alphabetically.  Computed once per run and passed to every chart, so a reader can track
    a strategy across figures by position alone.

    Works off the long-row fields both writers emit (cell / pair / pickcfg / channel /
    initial / assignment / labor_hours); the saving is matched-initial (uni vs uni,
    opt vs opt), isolating the assignment function.
    """
    base = {(r['pair'], r['pickcfg'], r['channel'], r['initial']): r['labor_hours']
            for r in rows if r['cell'] == reference and r['assignment'] == baseline}
    groups: dict = {}
    for r in rows:
        if r['cell'] != reference:
            continue
        b = base.get((r['pair'], r['pickcfg'], r['channel'], r['initial']))
        v = r.get('labor_hours')
        if b is None or v is None or v != v:
            continue
        groups.setdefault(r['assignment'], []).append(b - v)
    means = {a: statistics.mean(vs) for a, vs in groups.items()}
    names = sorted({r['assignment'] for r in rows})
    return sorted(names, key=lambda a: (-means.get(a, float('-inf')), a))


def _ordered(names, order):
    """`names` arranged in the canonical arm order; anything the order does not know
    (defensive — should not happen on real rows) goes last, alphabetically."""
    known = [a for a in order if a in names]
    return known + sorted(n for n in names if n not in known)


def _data_xlim(ax, values, *, pad=0.08, pad_hi=None, include=()):
    """chartkit.data_ylim's x-axis counterpart for the horizontal bar/dumbbell panels:
    hug the data (+pad fraction), fold in the `include` anchors, and declare the
    truncation whenever the window excludes zero.  `pad_hi` widens only the right side
    (room for row-end labels)."""
    vals = [float(v) for v in values if v is not None and v == v]
    if not vals:
        return
    lo, hi = min(vals), max(vals)
    for inc in include:
        lo, hi = min(lo, inc), max(hi, inc)
    span = (hi - lo) or (abs(hi) or 1.0)
    lo2 = lo - span * pad
    hi2 = hi + span * (pad if pad_hi is None else pad_hi)
    ax.set_xlim(lo2, hi2)
    if lo2 > 0 or hi2 < 0:
        ax.text(0.005, 0.008, 'x-axis truncated — 0 not shown', transform=ax.transAxes,
                fontsize=6, color='#777777', va='bottom', ha='left')


def _headroom(ax, frac):
    """Raise the y ceiling by `frac` of the current span — reserved space for the panel
    tag, so it can never sit on data.  Applied equally per panel, it preserves a shared
    scale's identical limits."""
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + (hi - lo) * frac)


def _panel_tag(ax, text):
    """Panel caption INSIDE the axes (top-left, in the reserved headroom) — the figure's
    title band is sized for the suptitle + subtitle only, so per-panel `ax.set_title`
    would collide with it."""
    ax.text(0.02, 0.985, text, transform=ax.transAxes, ha='left', va='top',
            fontsize=9, fontweight='bold', color='#333333')


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
    """One point per (scheduler, assignment fn): total labor vs mean throughput, per
    channel.  Channels are different workloads, so each panel keeps its own scale and
    says so."""
    chans = _channels(rows)
    scheds = ('round_robin', 'lpt')
    chart = _ck.make(panels=len(chans), panel_w=5.4, panel_h=4.4,
                     legend_labels=scheds, legend_title='scheduler')
    for ax, ch in zip(chart.axes, chans):
        sub = [r for r in rows if r['channel'] == ch]
        # one point per (scheduler, assignment): labor summed over pairs, throughput averaged
        labor = _agg(sub, lambda r: (r['scheduler'], r['assignment']), 'labor_hours', 'sum')
        thr = _agg(sub, lambda r: (r['scheduler'], r['assignment']), 'thr_items_hr', 'mean')
        for sched in scheds:
            keys = sorted(k for k in labor if k[0] == sched and k in thr)
            ax.scatter([labor[k] for k in keys], [thr[k] for k in keys], s=60,
                       color=_SCHED_COLOR[sched], edgecolors='white', linewidths=0.5,
                       label=sched, zorder=3)
        ax.set_xlabel('total labor hours (Σ task-makespan)')
        ax.set_ylabel('throughput (items / hour)')
        _headroom(ax, 0.16)
        _panel_tag(ax, ch)
        if len(chans) > 1:
            _ck.annotate_unshared(ax, axis='x & y')
    chart.legend()
    chart.title('Throughput vs labor hours')
    chart.save(out_path)


def _labor_saved_bars(rows, baseline, out_path, order=()):
    """ONE panel: the channel where the assignment function moves real hours.  The other
    channels' spreads are minutes-scale noise, and ranking noise with confident bars was
    the audit finding this figure fixes — they are omitted (and named in the subtitle)
    rather than drawn at 100× zoom.  Neutral single hue: sign is the zero line's job."""
    groups_by_chan: dict = {}
    for r in rows:
        v = r.get('labor_saved')
        if r['assignment'] == baseline or v is None or v != v:
            continue
        groups_by_chan.setdefault(r['channel'], {}).setdefault(r['assignment'], []).append(v)
    if not groups_by_chan:
        return
    ch = max(sorted(groups_by_chan),
             key=lambda c: max(abs(statistics.mean(vs)) for vs in groups_by_chan[c].values()))
    groups = groups_by_chan[ch]
    saved = {a: statistics.mean(vs) for a, vs in groups.items()}
    names = _ordered(sorted(saved), order)
    vals = [saved[a] for a in names]
    ns = sorted({len(groups[a]) for a in names})
    n_txt = str(ns[0]) if len(ns) == 1 else f'{ns[0]}–{ns[-1]}'
    others = [c for c in sorted(groups_by_chan) if c != ch]
    omitted = (f" · omitted: {', '.join(others)} (minutes-scale)" if others else '')
    chart = _ck.make(panels=1, panel_w=6.4,
                     panel_h=_ck.height_for_categories(len(names)), legend='none')
    ax = chart.ax
    ypos = [len(names) - 1 - i for i in range(len(names))]   # canonical order, top-down
    ax.barh(ypos, vals, color=_ARM_HUE, height=0.62)
    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=8)
    ax.axvline(0, color='#222222', lw=0.9)
    _data_xlim(ax, vals, include=(0.0,))
    ax.set_xlabel(f'labor hours saved vs {baseline} (→ better)')
    ax.grid(False, axis='y')
    chart.title('Labor hours saved by assignment fn',
                subtitle=f'{ch} · n={n_txt} pairs averaged per arm{omitted}')
    chart.save(out_path)


def _batch_hours_bars(rows, out_path, order=()):
    """Per-arm RR-vs-LPT batch-makespan as a DUMBBELL — dot per scheduler, connecting
    line, and the LPT improvement labelled at the row end.  Zero-anchored paired bars
    made the few-percent deltas this figure exists to show invisible; the x-window hugs
    the data and declares its truncation instead."""
    chans = _channels(rows)
    scheds = ('round_robin', 'lpt')
    n_max = max((len({r['assignment'] for r in rows if r['channel'] == ch})
                 for ch in chans), default=1)
    chart = _ck.make(panels=len(chans), panel_w=5.6,
                     panel_h=_ck.height_for_categories(n_max),
                     legend_labels=scheds, legend_title='scheduler')
    for ax, ch in zip(chart.axes, chans):
        sub = [r for r in rows if r['channel'] == ch]
        rr = _agg([r for r in sub if r['scheduler'] == 'round_robin'],
                  lambda r: r['assignment'], 'batch_hours', 'sum')
        lpt = _agg([r for r in sub if r['scheduler'] == 'lpt'],
                   lambda r: r['assignment'], 'batch_hours', 'sum')
        names = _ordered(sorted(set(rr) | set(lpt)), order)
        allv = []
        for i, a in enumerate(names):
            y = len(names) - 1 - i                           # canonical order, top-down
            hr, hl = rr.get(a), lpt.get(a)
            if hr is not None and hl is not None:
                ax.plot([hr, hl], [y, y], color='#b8c0cc', lw=1.8, zorder=2)
                pct = _ck.improvement_pct(hl, hr, lower_is_better=True)
                ax.annotate(f'{pct:+.1f}%', (max(hr, hl), y), xytext=(6, 0),
                            textcoords='offset points', va='center', fontsize=7,
                            color='#555555')
            if hr is not None:
                ax.plot([hr], [y], 'o', ms=6, color=_SCHED_COLOR['round_robin'], zorder=3)
                allv.append(hr)
            if hl is not None:
                ax.plot([hl], [y], 'o', ms=6, color=_SCHED_COLOR['lpt'], zorder=3)
                allv.append(hl)
        ax.set_yticks([len(names) - 1 - i for i in range(len(names))])
        ax.set_yticklabels(names, fontsize=8)
        ax.set_ylim(-0.6, len(names) - 0.4)
        _data_xlim(ax, allv, pad_hi=0.30)
        _headroom(ax, 1.4 / (len(names) + 1))
        _panel_tag(ax, ch)
        ax.set_xlabel('batch-makespan hours (Σ duration) — '
                      'row label: LPT improvement, + = fewer hours', fontsize=9)
        ax.grid(False, axis='y')
        if len(chans) > 1:
            _ck.annotate_unshared(ax, axis='x')
    chart.legend([Line2D([], [], marker='o', ls='none', color=_SCHED_COLOR[s], label=s)
                  for s in scheds])
    chart.title('Batch-makespan hours: RR vs LPT')
    chart.save(out_path)


def _uplift_bars(samples, out_path, order=()):
    """Median LPT-vs-reference throughput uplift per arm — vertical bars on ONE shared
    value scale across panels, with a 95% t-interval whisker wherever an arm has two or
    more per-(pair × config) samples.  `samples` is {(channel, assignment): [uplift %]}."""
    by_chan: dict = {}
    for (ch, a), vs in samples.items():
        vs = [v for v in vs if v == v]
        if vs:
            by_chan.setdefault(ch, {})[a] = vs
    if not by_chan:
        return
    chans = sorted(by_chan)
    n_max = max(len(d) for d in by_chan.values())
    chart = _ck.make(panels=len(chans), panel_w=_ck.width_for_categories(n_max),
                     panel_h=3.8, legend='none')
    try:
        from scipy.stats import t as _t
    except ImportError:                                      # pragma: no cover
        _t = None
    allvals = []
    for ax, ch in zip(chart.axes, chans):
        d = by_chan[ch]
        names = _ordered(sorted(d), order)
        meds = [_med(d[a]) for a in names]
        xs = list(range(len(names)))
        ax.bar(xs, meds, color=_SCHED_COLOR['lpt'], width=0.62)
        allvals += meds
        cx, clo, chi = [], [], []
        for x, a in zip(xs, names):
            vs = d[a]
            if _t is None or len(vs) < 2:
                continue
            m = statistics.mean(vs)
            half = float(_t.ppf(0.975, len(vs) - 1)) * statistics.stdev(vs) / math.sqrt(len(vs))
            cx.append(x)
            clo.append(m - half)
            chi.append(m + half)
        if cx:
            _ck.draw_ci(ax, cx, clo, chi, band=False)
            allvals += clo + chi
        ax.set_xticks(xs)
        ax.set_xticklabels(names, rotation=35, ha='right', fontsize=8)
        ax.axhline(0, color='#222222', lw=0.9)
        cue = _ck.pct_axis(ax, better='up', axis='y')
        ax.tick_params(axis='y', labelsize=8)
        if ax is chart.axes[0]:                # edge-only labeling — the scale is shared
            ax.set_ylabel(f'LPT throughput uplift {cue}', fontsize=8, labelpad=2)
        ax.grid(False, axis='x')
    _ck.shared_ylim(chart.axes, [allvals], include=(0.0,))
    for ax, ch in zip(chart.axes, chans):
        _headroom(ax, 0.16)                    # equal per panel — the scale stays shared
        _panel_tag(ax, ch)
    chart.title('Scheduler throughput uplift by arm',
                subtitle='bar = median % change in steady-state items/hour vs the reference '
                         'scheduler · whisker = 95% t-interval of the mean (n ≥ 2 pairs) · '
                         'same scale on every panel')
    chart.save(out_path)


def run(base_dir, baseline='fifo', baseline_initial='match', reference=None, pairs='sum', log=None):
    """Engine: cross-cell throughput / labor-hours over base_dir; write the whatif_labor CSV/JSON
    + the four PNGs, and return the CSV path.  Importable so the analysis hub calls it in-process
    (no argv).  `pairs` in {'sum','median'} blends the catalogs; a single-cell run still emits
    the CSV/JSON (its cross-cell deltas are empty)."""
    import types
    args = types.SimpleNamespace(base_dir=base_dir, baseline=baseline,
                                 baseline_initial=baseline_initial, reference=reference, pairs=pairs)
    from Optimization.runschema import resolver_for
    rt = resolver_for(args.base_dir)
    if args.reference is None:
        args.reference = rt.layout.get('reference')
    if args.reference is None:
        try:
            from Optimization.config.whatif_config import WHATIF
            args.reference = WHATIF.get('reference', 'k1_off_rr')
        except Exception:
            args.reference = 'k1_off_rr'

    cells = [name for name, _dir in rt.cells()]
    if not cells:
        raise SystemExit(f'no scenario cells in {args.base_dir}')
    scans = {c: _scan_labor(rt, c) for c in cells}

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
                'thr_items_hr': (m['thr_batch'] or 0) * PER_HOUR,
                'thr_task_items_hr': (m['thr_task'] or 0) * PER_HOUR,
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

    csv_path = rt.path('whatif_labor_csv')
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
            # The count of positive throughput comparisons used to be hand-rolled right
            # here (`'n': len(dth), 'pos_thr': sum(x > 0 ...)`) and then RESTATED in prose
            # on the site — "positive in all 68 comparisons" — with no interval and no
            # test.  `census` is that count generalised; the page renders it instead of
            # repeating it.  NaN filtering moves inside, so `dth` no longer needs it.
            chan_out[ch] = {
                'dtask_ms': {'med': _med(dt), 'min': min(dt) if dt else None, 'max': max(dt) if dt else None},
                'dthr_batch': {'med': _med(dth), 'min': min(dth) if dth else None, 'max': max(dth) if dth else None},
                'census': census(d['dthr_batch'], value=lambda x: x)[0],
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
    json_path = rt.path('whatif_labor_json')
    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f'wrote {json_path}')

    # ── charts ─────────────────────────────────────────────────────────────────────────────
    # The PNGs live under the whatif_labor_pngs artifact, a glob whose star sits in the FILENAME
    # segment — so the template's directory is the contract-rendered home and only the concrete
    # basenames are spelled here.
    png_dir = os.path.dirname(rt.path('whatif_labor_pngs'))
    pngs = {name: os.path.join(png_dir, name) for name in (
        'whatif_labor_throughput_scatter.png', 'whatif_labor_saved_bars.png',
        'whatif_batch_hours_rr_vs_lpt.png', 'whatif_scheduler_uplift.png')}
    # ONE canonical arm ordering, shared by every what-if figure this run emits.
    arm_order = canonical_arm_order(rows, args.reference, args.baseline)
    # Provenance rides the chartkit footer band, not per-figure ad-hoc text.
    prev_footer = getattr(_io, '_FOOTER', None)
    _io.set_footer(f'whatif labor re-analysis · {os.path.basename(os.path.normpath(args.base_dir))}'
                   f' · baseline={args.baseline} · reference={args.reference}'
                   f' · modeled sim pick-time hours, not wall-clock')
    try:
        _throughput_scatter(rows, pngs['whatif_labor_throughput_scatter.png'])
        _labor_saved_bars(rows, args.baseline, pngs['whatif_labor_saved_bars.png'], arm_order)
        _batch_hours_bars(rows, pngs['whatif_batch_hours_rr_vs_lpt.png'], arm_order)
        _uplift_bars(uplift_samples, pngs['whatif_scheduler_uplift.png'], arm_order)
    finally:
        _io.set_footer(prev_footer)
    for p in pngs.values():
        if os.path.exists(p):
            print(f'wrote {p}')

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
                    help="scheduler reference cell (default: the run's own descriptor "
                         "reference, else WHATIF['reference'])")
    ap.add_argument('--pairs', default='sum', choices=('sum', 'median'),
                    help='how to blend the two catalogs for hours (sum = additive workload; default sum)')
    args = ap.parse_args()
    from Optimization.runschema import resolve_base_dir
    run(resolve_base_dir(args.base_dir), baseline=args.baseline,
        baseline_initial=args.baseline_initial, reference=args.reference, pairs=args.pairs)


if __name__ == '__main__':
    main()
