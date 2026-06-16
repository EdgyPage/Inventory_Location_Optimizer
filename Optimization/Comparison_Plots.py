"""
Comparison_Plots.py — graphing and analysis for the strategy comparison.

Called via `run_config_analysis(sim_result, shared, log)`.  `sim_result` carries a
self-describing `strategies` list (each {key, label, color, db_path, run_id}) read
from sim_meta.json, so the plots are N-wide and driven entirely by that list —
no hardcoded A/B/C.  strategies[0] is the baseline for delta comparisons.
"""

import matplotlib
matplotlib.use('Agg')  # must come before pyplot import

import json
import logging
import math
import os
import shutil
from collections import defaultdict

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from scipy.stats import gaussian_kde

from Picking_Data        import load_batch_stats, load_task_stats
from Simulation_Analytics import flag_batch_outliers, flag_task_outliers

_TRAVEL_COL = '#a9a9a9'

# ── rolling-average window ─────────────────────────────────────────────────────
_WIN = 50   # batches


# ── DataFrame helpers ──────────────────────────────────────────────────────────

def _bdf(stats):
    return pd.DataFrame([{
        'batch_id'              : s.batch_id,
        'duration'              : s.duration,
        'num_tasks'             : s.num_tasks,
        'total_items'           : s.total_items,
        'completion_rate'       : s.total_items / s.duration if s.duration > 0 else 0.0,
        'avg_concurrent_pickers': s.avg_concurrent_pickers,
        'picking_pct'           : s.picking_pct   * 100,
        'traveling_pct'         : s.traveling_pct * 100,
        'sigma_fd'              : s.sigma_fd,
        'reload_moves'          : s.reload_moves,
        'reorder_placements'    : s.reorder_placements,
    } for s in stats])


def _tdf(stats, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W'        : s.W,
        'lift_sum'   : s.lift_sum,
        'num_bins'   : s.num_bins_visited,
        'total_items': s.total_items,
        'unit_type'  : aisle_unittype_map.get(s.aisle_id),
        'handling'   : aisle_handling_map.get(s.aisle_id),
    } for s in stats])


def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


# ── plot helpers ───────────────────────────────────────────────────────────────

def _kde_plot(ax, data, color, bins):
    ax.hist(data, bins=bins, color=color, alpha=0.65, edgecolor='white')
    if len(data) > 1 and data.max() > data.min():
        kde = gaussian_kde(data, bw_method='silverman')
        xs  = np.linspace(data.min(), data.max(), 400)
        ax.plot(xs, kde(xs) * len(data) * (data.max() - data.min()) / bins, color=color, lw=2)
    ax.axvline(data.mean(),     color='red',    lw=1.5, linestyle='--', label=f'Mean   {data.mean():.1f}')
    ax.axvline(np.median(data), color='orange', lw=1.5, linestyle=':',  label=f'Median {np.median(data):.1f}')


def _save_close(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _fresh_dir(path):
    """Remove a stale output directory and recreate it empty, so a re-run can never
    leave mismatched plots (e.g. an old top5_* beside a new top3_by_initial_*) behind."""
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


def _row(n, figw=4.8, h=4.5):
    """A row of n subplots; always returns a flat list of axes."""
    fig, axes = plt.subplots(1, n, figsize=(figw * n, h), squeeze=False)
    return fig, list(axes[0])


def _pct_delta(val, base):
    return (val - base) / abs(base) * 100 if base else 0.0


def _grid(n, panel_w=3.0, panel_h=2.3, max_cols=8):
    """A roughly-square subplot grid for n strategies; returns (fig, n flat axes)."""
    cols = min(max_cols, max(1, math.ceil(math.sqrt(n))))
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(panel_w * cols, panel_h * rows), squeeze=False)
    flat = [ax for row in axes for ax in row]
    for ax in flat[n:]:
        ax.axis('off')
    return fig, flat[:n]


# ── comparison suite: strategy decomposition + per-strategy series ─────────────

_LINESTYLES = ['-', '--', '-.', ':']


def _stitle(s):
    """Compact strategy label: initial|assignment|reslot (falls back to label/key)."""
    parts = [p for p in (s.get('initial', ''), s.get('assignment', ''),
                         s.get('reslot', '')) if p]
    return '|'.join(parts) if parts else s.get('label', s.get('key', ''))


def _assign_color_map(strategies):
    asn = sorted({s['assignment'] for s in strategies})
    cmap = plt.cm.tab10
    return {a: cmap(i % 10) for i, a in enumerate(asn)}


def _ir_style_map(strategies):
    irs = sorted({(s['initial'], s['reslot']) for s in strategies})
    return {ir: _LINESTYLES[i % len(_LINESTYLES)] for i, ir in enumerate(irs)}


def _build_series(strategies, df_b, df_t):
    """Per-strategy time series + steady-state scalars for the comparison suite.

    Returns {key: dict | None}.  Task metrics carry their own x ('task_batch')
    because some batches may have no surviving (non-outlier) tasks.
    """
    S = {}
    for s in strategies:
        b = df_b[s['key']]
        t = df_t[s['key']]
        if b.empty:
            S[s['key']] = None
            continue
        bd    = b.sort_values('batch_id')
        batch = bd['batch_id'].values
        thr   = bd['completion_rate'].rolling(_WIN, min_periods=1).mean().values

        if not t.empty:
            g   = t.groupby('batch_id')['duration']
            agg = g.agg(['mean', 'median']).sort_index()
            q25 = g.quantile(0.25).reindex(agg.index)
            q75 = g.quantile(0.75).reindex(agg.index)
            tsum = g.sum().reindex(agg.index)        # productivity hours = Σ task time / batch
            tb   = agg.index.values
            roll = lambda ser: ser.rolling(_WIN, min_periods=1).mean().values
            tmean, tmed = roll(agg['mean']), roll(agg['median'])
            tp25,  tp75 = roll(q25),         roll(q75)
            tprod = roll(tsum)
        else:
            tb = batch
            tmean = tmed = tp25 = tp75 = tprod = np.full(len(batch), np.nan)

        maxb = int(batch.max())
        lo   = maxb - _WIN + 1
        ssb  = bd[bd['batch_id'] >= lo]
        sst  = t[t['batch_id'] >= lo] if not t.empty else t
        S[s['key']] = dict(
            batch=batch, thr=thr,
            task_batch=tb, task_mean=tmean, task_median=tmed,
            task_p25=tp25, task_p75=tp75, prod_hours=tprod,
            ss_thr=float(ssb['completion_rate'].mean()),
            ss_dur=float(ssb['duration'].mean()),
            ss_task_mean=float(sst['duration'].mean()) if len(sst) else float('nan'),
            ss_prod_hours=(float(sst.groupby('batch_id')['duration'].sum().mean())
                           if len(sst) else float('nan')),
            picking_pct=float(ssb['picking_pct'].mean()),
            traveling_pct=float(ssb['traveling_pct'].mean()),
            initial=s.get('initial', ''), assignment=s.get('assignment', ''),
            reslot=s.get('reslot', ''), color=s['color'],
            label=s.get('label', s['key']), key=s['key'],
        )
    return S


def _dump_series(strategies, S, path):
    out = []
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        out.append({k: (v.tolist() if isinstance(v, np.ndarray) else v)
                    for k, v in d.items()})
    with open(path, 'w') as f:
        json.dump({'strategies': out}, f)


# ── individual plot builders (shared by per-config and aggregate) ──────────────

def _facet_metric(strategies, S, m, title, path):
    inits = sorted({s['initial'] for s in strategies})
    resl  = sorted({s['reslot'] for s in strategies})
    acmap = _assign_color_map(strategies)
    nrow, ncol = max(1, len(inits)), max(1, len(resl))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.0 * ncol, 3.4 * nrow),
                             squeeze=False, sharex=True)
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        ax = axes[inits.index(s['initial'])][resl.index(s['reslot'])]
        ax.plot(d[m['x']], d[m['y']], color=acmap[s['assignment']], lw=1.3)
        if m['blo'] and d.get(m['blo']) is not None:
            ax.fill_between(d[m['x']], d[m['blo']], d[m['bhi']],
                            color=acmap[s['assignment']], alpha=0.12)
    for r, ini in enumerate(inits):
        for c, rs in enumerate(resl):
            ax = axes[r][c]
            ax.set_title(f'{ini} | {rs}', fontsize=9)
            ax.grid(alpha=0.3)
            if r == nrow - 1:
                ax.set_xlabel('batch')
            if c == 0:
                ax.set_ylabel(m['yl'], fontsize=8)
    handles = [Line2D([], [], color=acmap[a], lw=2, label=a) for a in sorted(acmap)]
    axes[0][ncol - 1].legend(handles=handles, fontsize=7, title='assignment')
    fig.suptitle(title, fontsize=13, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.97))
    _save_close(fig, path)


def _overlay_metric(strategies, S, m, title, path):
    acmap = _assign_color_map(strategies)
    smap  = _ir_style_map(strategies)
    fig, ax = plt.subplots(figsize=(11, 6))
    for s in strategies:
        d = S.get(s['key'])
        if d is None:
            continue
        ax.plot(d[m['x']], d[m['y']], color=acmap[s['assignment']],
                ls=smap[(s['initial'], s['reslot'])], lw=1.1, alpha=0.9)
    ax.set_xlabel('batch')
    ax.set_ylabel(m['yl'])
    ax.grid(alpha=0.3)
    ax.set_title(title, fontsize=13, fontweight='bold')
    ch = [Line2D([], [], color=acmap[a], lw=2, label=a) for a in sorted(acmap)]
    sh = [Line2D([], [], color='k', ls=smap[ir], lw=1.5, label=f'{ir[0]}|{ir[1]}')
          for ir in sorted(smap)]
    leg1 = ax.legend(handles=ch, fontsize=8, title='assignment', loc='upper right')
    ax.add_artist(leg1)
    ax.legend(handles=sh, fontsize=8, title='initial|reslot', loc='lower right')
    plt.tight_layout()
    _save_close(fig, path)


_TOP_DIMS = ('initial', 'assignment', 'reslot')


def _select_top(strategies, S, top_n, top_by):
    """Choose which strategies the top/ plot shows.

    'global'  → the top_n strategies overall (by steady-state throughput).
    a dim     → the top_n WITHIN each value of that dim (initial/assignment/reslot),
                so the comparison isn't dominated by one family (e.g. optimal-start).
    Returns (selected_strategies, group_of_key) where group_of_key maps key→group
    label ('' for global)."""
    avail = [s for s in strategies if S.get(s['key'])]
    rank  = lambda s: S[s['key']]['ss_thr']
    if top_by in _TOP_DIMS:
        groups = defaultdict(list)
        for s in avail:
            groups[s[top_by]].append(s)
        out, gof = [], {}
        for g in sorted(groups):
            for s in sorted(groups[g], key=rank, reverse=True)[:top_n]:
                out.append(s)
                gof[s['key']] = g
        return out, gof
    return sorted(avail, key=rank, reverse=True)[:top_n], {}


def _top_metric(strategies, S, top_n, m, title, baseline, path, top_by='global'):
    selected, gof = _select_top(strategies, S, top_n, top_by)
    # in grouped mode, linestyle encodes the group so the families are distinguishable
    gstyle = {g: _LINESTYLES[i % len(_LINESTYLES)] for i, g in enumerate(sorted(set(gof.values())))}
    fig, ax = plt.subplots(figsize=(11, 6))
    db = S.get(baseline['key'])
    if db is not None:
        ax.plot(db[m['x']], db[m['y']], color='grey', lw=1.3, ls='--',
                label=f"baseline · {_stitle(baseline)}")
    solo = len(selected) <= 3
    for s in selected:
        d = S.get(s['key'])
        if d is None:
            continue
        ls = gstyle.get(gof.get(s['key']), '-')
        ax.plot(d[m['x']], d[m['y']], color=s['color'], lw=1.8, ls=ls, label=_stitle(s))
        if m['blo'] and solo and d.get(m['blo']) is not None:
            ax.fill_between(d[m['x']], d[m['blo']], d[m['bhi']], color=s['color'], alpha=0.12)
    ax.set_xlabel('batch')
    ax.set_ylabel(m['yl'])
    ax.grid(alpha=0.3)
    sub = f'  (top {top_n} per {top_by})' if top_by in _TOP_DIMS else f'  (top {top_n})'
    ax.set_title(title + sub, fontsize=12, fontweight='bold')
    ax.legend(fontsize=8)
    plt.tight_layout()
    _save_close(fig, path)


def _task_box(strategies, df_t, title, path, win=_WIN):
    """Box plot of steady-state task durations, one box per strategy (color =
    assignment), with mean markers — task time per strategy at a glance."""
    avail, data = [], []
    for s in strategies:
        df = df_t[s['key']]
        if df.empty:
            continue
        d = df[df['batch_id'] >= df['batch_id'].max() - win]['duration'].values
        if len(d):
            avail.append(s)
            data.append(d)
    if not avail:
        return
    acmap = _assign_color_map(avail)
    xs = np.arange(1, len(avail) + 1)
    fig, ax = plt.subplots(figsize=(max(10.0, len(avail) * 0.55), 6))
    bp = ax.boxplot(data, showfliers=False, patch_artist=True, widths=0.6,
                    medianprops=dict(color='black'))
    for patch, s in zip(bp['boxes'], avail):
        patch.set_facecolor(acmap[s['assignment']])
        patch.set_alpha(0.8)
    ax.plot(xs, [float(np.mean(d)) for d in data], 'D', color='black', ms=4, label='mean')
    ax.set_xticks(xs)
    ax.set_xticklabels([_stitle(s) for s in avail], rotation=90, fontsize=6)
    ax.set_ylabel('task duration (steady state)')
    ax.grid(axis='y', alpha=0.3)
    ax.set_title(title, fontsize=12, fontweight='bold')
    handles = [Line2D([], [], color=acmap[a], lw=6, label=a) for a in sorted(acmap)]
    handles.append(Line2D([], [], marker='D', color='black', ls='', label='mean'))
    ax.legend(handles=handles, fontsize=7, ncol=2)
    plt.tight_layout()
    _save_close(fig, path)


def _pick_travel_bars(strategies, S, title, path):
    avail = [s for s in strategies if S.get(s['key'])]
    ypos  = np.arange(len(avail))
    pk = [S[s['key']]['picking_pct']   for s in avail]
    tv = [S[s['key']]['traveling_pct'] for s in avail]
    fig, ax = plt.subplots(figsize=(10, max(6.0, len(avail) * 0.3)))
    ax.barh(ypos, pk, color='#4c72b0', label='picking %')
    ax.barh(ypos, tv, left=pk, color='#dd8452', label='traveling %')
    ax.set_yticks(ypos)
    ax.set_yticklabels([_stitle(s) for s in avail], fontsize=6)
    ax.invert_yaxis()
    ax.set_xlabel('% of aggregate picker-time')
    ax.grid(axis='x', alpha=0.3)
    ax.legend(loc='lower right')
    ax.set_title(title, fontsize=12, fontweight='bold')
    plt.tight_layout()
    _save_close(fig, path)


def _delta_bars(strategies, S, baseline, title, path):
    avail = [s for s in strategies if S.get(s['key'])]
    base  = S.get(baseline['key'])
    if base is None:
        return
    bt, bd = base['ss_thr'], base['ss_dur']
    ypos = np.arange(len(avail))
    dthr = [_pct_delta(S[s['key']]['ss_thr'], bt) for s in avail]            # ↑ better
    ddur = [_pct_delta(bd, S[s['key']]['ss_dur']) for s in avail]            # ↑ better (improvement)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, max(6.0, len(avail) * 0.3)), sharey=True)
    a1.barh(ypos, dthr, color=['#55a868' if v >= 0 else '#c44e52' for v in dthr])
    a1.set_title('Throughput Δ% vs baseline (↑ better)', fontsize=10)
    a1.set_yticks(ypos)
    a1.set_yticklabels([_stitle(s) for s in avail], fontsize=6)
    a1.invert_yaxis()
    a1.axvline(0, color='k', lw=0.8)
    a1.grid(axis='x', alpha=0.3)
    a2.barh(ypos, ddur, color=['#55a868' if v >= 0 else '#c44e52' for v in ddur])
    a2.set_title('Duration improvement % vs baseline (↑ better)', fontsize=10)
    a2.axvline(0, color='k', lw=0.8)
    a2.grid(axis='x', alpha=0.3)
    fig.suptitle(title, fontsize=12, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    _save_close(fig, path)


def _emit_comparison_suite(strategies, S, out_dir, top_n, title_prefix, agg=False,
                           top_by='global'):
    """Write faceted/, overlay/, top/, breakdown/ for the core over-time metrics."""
    fac = os.path.join(out_dir, 'faceted')
    ovl = os.path.join(out_dir, 'overlay')
    top = os.path.join(out_dir, 'top')
    brk = os.path.join(out_dir, 'breakdown')
    for d in (fac, ovl, top, brk):
        os.makedirs(d, exist_ok=True)
    unit = ' (× baseline)' if agg else ''
    metrics = [
        dict(x='task_batch', y='task_median', blo='task_p25', bhi='task_p75',
             f='task_duration_over_time', t='Task duration over time (median + IQR)',
             yl='task duration' + unit),
        dict(x='task_batch', y='task_mean', blo=None, bhi=None,
             f='avg_task_duration_over_time', t='Average task duration over time',
             yl='mean task duration' + unit),
        dict(x='batch', y='thr', blo=None, bhi=None,
             f='throughput_over_time', t='Throughput over time (items / sim-time)',
             yl='throughput' + unit),
        dict(x='task_batch', y='prod_hours', blo=None, bhi=None,
             f='productivity_hours_over_time',
             t='Productivity hours (Sigma task time per batch)',
             yl='productivity hours' + unit),
    ]
    base = strategies[0]
    top_tag = f"top{top_n}" + (f"_by_{top_by}" if top_by in _TOP_DIMS else "")
    for m in metrics:
        ttl = f"{m['t']}  [{title_prefix}]"
        _facet_metric(strategies, S, m, ttl, os.path.join(fac, m['f'] + '.png'))
        _overlay_metric(strategies, S, m, ttl, os.path.join(ovl, m['f'] + '.png'))
        _top_metric(strategies, S, top_n, m, ttl, base,
                    os.path.join(top, f"{top_tag}_{m['f']}.png"), top_by=top_by)
    _pick_travel_bars(strategies, S, f'Pick vs travel  [{title_prefix}]',
                      os.path.join(brk, 'pick_vs_travel.png'))
    _delta_bars(strategies, S, base, f'Δ vs baseline  [{title_prefix}]',
                os.path.join(brk, 'delta_vs_baseline.png'))


# ── cross-profile aggregate ────────────────────────────────────────────────────

def _aggregate_series(profile_series_list):
    """Average per-strategy curves across profiles, normalized per-profile to that
    profile's baseline-strategy steady-state mean (scale-free).  Returns
    (agg_strategies, agg_S) ready for the same builders."""
    per_key = defaultdict(list)               # key -> [(strat_dict, baseline_dict)]
    order   = {}
    for ps in profile_series_list:
        strs = ps.get('strategies', [])
        if not strs:
            continue
        base = strs[0]
        for i, d in enumerate(strs):
            per_key[d['key']].append((d, base))
            order.setdefault(d['key'], i)

    def _avg_norm(items, field, norm_field):
        arrs = []
        for d, base in items:
            a  = np.asarray(d.get(field, []), dtype=float)
            nb = base.get(norm_field)
            if a.size and nb and not math.isnan(nb):
                arrs.append(a / nb)
        if not arrs:
            return np.array([])
        mlen = min(len(a) for a in arrs)
        return np.mean([a[:mlen] for a in arrs], axis=0)

    agg_S, agg_strats = {}, []
    for key, items in per_key.items():
        rep   = items[0][0]
        thr   = _avg_norm(items, 'thr',         'ss_thr')
        tmean = _avg_norm(items, 'task_mean',   'ss_task_mean')
        tmed  = _avg_norm(items, 'task_median', 'ss_task_mean')
        tp25  = _avg_norm(items, 'task_p25',    'ss_task_mean')
        tp75  = _avg_norm(items, 'task_p75',    'ss_task_mean')
        tprod = _avg_norm(items, 'prod_hours',  'ss_prod_hours')
        thr_ratio = [d['ss_thr'] / b['ss_thr'] for d, b in items if b.get('ss_thr')]
        dur_ratio = [d['ss_dur'] / b['ss_dur'] for d, b in items if b.get('ss_dur')]
        agg_S[key] = dict(
            batch=np.arange(len(thr)), thr=thr,
            task_batch=np.arange(len(tmed)), task_mean=tmean, task_median=tmed,
            task_p25=tp25, task_p75=tp75, prod_hours=tprod,
            ss_thr=float(np.mean(thr_ratio)) if thr_ratio else float('nan'),
            ss_dur=float(np.mean(dur_ratio)) if dur_ratio else float('nan'),
            picking_pct=float(np.mean([d['picking_pct']   for d, _ in items])),
            traveling_pct=float(np.mean([d['traveling_pct'] for d, _ in items])),
            initial=rep.get('initial', ''), assignment=rep.get('assignment', ''),
            reslot=rep.get('reslot', ''), color=rep['color'],
            label=rep.get('label', key), key=key,
        )
        agg_strats.append({k: rep.get(k, '') for k in ('key', 'initial', 'assignment',
                                                       'reslot', 'label')} | {'color': rep['color']})
    agg_strats.sort(key=lambda s: order.get(s['key'], 1 << 30))
    return agg_strats, agg_S


def run_aggregate_analysis(profile_series_list, out_dir, top_n, pickcfg, log,
                           top_by='global', no_stats=False):
    """Cross-profile roll-up for one pick-config: same plot suite, baseline-normalized."""
    _fresh_dir(out_dir)   # never mix this run's aggregate with a prior one
    strategies, S = _aggregate_series(profile_series_list)
    if not strategies:
        log.warning(f'  aggregate {pickcfg}: no usable series')
        return
    _emit_comparison_suite(
        strategies, S, out_dir, top_n,
        f'AGG {pickcfg} · {len(profile_series_list)} profiles', agg=True, top_by=top_by)
    log.info(f'  aggregate suite -> {out_dir} ({len(profile_series_list)} profiles)')

    # ── cross-profile significance suite (strategies paired by profile) ──
    if not no_stats:
        try:
            from Stats_Analysis import run_aggregate_stats
            run_aggregate_stats(profile_series_list, os.path.join(out_dir, 'stats'),
                                log, pickcfg)
        except Exception as exc:                                   # noqa: BLE001
            log.error(f'  aggregate stats failed for {pickcfg}: {exc!r}')


# ── main analysis entry point ──────────────────────────────────────────────────

def run_config_analysis(
    sim_result : dict,
    shared     : dict,
    log        : logging.Logger,
) -> None:
    """Run the analysis + plotting phase for one config across all strategies.

    Must be called sequentially (matplotlib pyplot is not thread-safe).
    """
    name               = sim_result['name']
    run_dir            = sim_result['run_dir']
    strategies         = sim_result['strategies']        # [{key,label,color,db_path,run_id}]
    aisle_unittype_map = shared['aisle_unittype_map']
    aisle_handling_map = shared['aisle_handling_map']
    k_pickers          = shared.get('k_pickers', 25)
    top_n              = int(shared.get('top_n', 1) or 1)
    # wipe prior analysis outputs so a re-run can't leave stale/mismatched plots behind
    # (the sim DBs, sim_meta.json, checkpoints at run_dir root are untouched).
    ps_dir             = os.path.join(run_dir, 'per_strategy')   # de-clutter
    _fresh_dir(ps_dir)
    _fresh_dir(os.path.join(run_dir, 'compare'))

    base       = strategies[0]                            # delta baseline
    base_key   = base['key']
    base_label = base['label']

    # ── load per-strategy DataFrames (outliers flagged out) ───────────────────
    df_b: dict[str, pd.DataFrame] = {}
    df_t: dict[str, pd.DataFrame] = {}
    for s in strategies:
        bs = flag_batch_outliers(load_batch_stats(s['db_path'], s['run_id']))
        ts = flag_task_outliers(load_task_stats(s['db_path'], s['run_id']))
        df_b[s['key']] = _bdf([x for x in bs if not x.is_outlier])
        df_t[s['key']] = _tdf([x for x in ts if not x.is_outlier],
                              aisle_unittype_map, aisle_handling_map)

    labels = [s['label'] for s in strategies]

    # ── summary CSVs ──────────────────────────────────────────────────────────
    bcols = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols = ['duration', 'W', 'lift_sum', 'num_bins']
    summ_b = pd.concat([df_b[s['key']][bcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_t = pd.concat([df_t[s['key']][tcols].agg(['mean', 'median', 'std']).T for s in strategies],
                       axis=1, keys=labels).round(3)
    summ_b.to_csv(os.path.join(ps_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(ps_dir, 'summary_task.csv'))
    log.info(f'\n{summ_b.to_string()}\n')
    log.info(f'\n{summ_t.to_string()}\n')

    n = len(strategies)
    inv = sim_result.get('inventory', '') or name
    optimal = float(sim_result.get('optimal_sigma_fd') or 0.0)
    total_bins = float(shared.get('total_bins') or 0)
    top_by = shared.get('top_by', 'global') or 'global'

    # per-strategy time series + steady-state scalars (also feeds the compare/ suite)
    S = _build_series(strategies, df_b, df_t)

    def _full_title(s):
        bits = [inv, s.get('initial', ''), s.get('assignment', ''), s.get('reslot', '')]
        return '_'.join(b for b in bits if b)

    def _eff_series(df):
        if df.empty:
            return None
        d = df.sort_values('batch_id')
        if optimal > 0:
            y = (optimal / d['sigma_fd'].clip(lower=1e-9) * 100.0).rolling(_WIN, min_periods=1).mean()
        else:
            y = d['sigma_fd'].rolling(_WIN, min_periods=1).mean()
        return d['batch_id'].values, y.values

    def _churn_series(df):
        if df.empty:
            return None
        d = df.sort_values('batch_id')
        moved = (d['reload_moves'] + d['reorder_placements']).astype(float)
        if total_bins > 0:
            moved = moved / total_bins * 100.0
        return d['batch_id'].values, moved.rolling(_WIN, min_periods=1).mean().values

    def _panel_duration(ax, s):
        df = df_b[s['key']]
        if not df.empty:
            d = df.sort_values('batch_id')
            ax.plot(d['batch_id'].values, _roll(df, 'duration', _WIN), color=s['color'], lw=1.2)

    def _panel_eff(ax, s):
        ser = _eff_series(df_b[s['key']])
        if ser is not None:
            ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)
            if optimal > 0:
                ax.axhline(100.0, color='grey', lw=0.7, ls='--')

    def _panel_churn(ax, s):
        ser = _churn_series(df_b[s['key']])
        if ser is not None:
            ax.plot(ser[0], ser[1], color=s['color'], lw=1.2)

    def _panel_taskdur(ax, s):
        data = df_t[s['key']]['duration'].values
        if not len(data):
            return
        ax.hist(data, bins=30, color=s['color'], alpha=0.7, edgecolor='white')
        mean, med = float(np.mean(data)), float(np.median(data))
        ax.axvline(mean, color='red',   lw=1.4, ls='--', label=f'mean {mean:.0f}')
        ax.axvline(med,  color='black', lw=1.2, ls=':',  label=f'med {med:.0f}')
        ax.legend(fontsize=5, loc='upper right')

    def _panel_task_overtime(ax, s):
        d = S.get(s['key'])
        if d is None:
            return
        x = d['task_batch']
        ax.fill_between(x, d['task_p25'], d['task_p75'], color=s['color'], alpha=0.18, label='IQR')
        ax.plot(x, d['task_median'], color=s['color'], lw=1.3, label='median')
        ax.plot(x, d['task_mean'],   color='black',    lw=1.0, ls='--', label='mean')

    # ── per-metric GRID images: one rectangular grid, one panel per strategy ───
    def _metric_grid(fname, title, panel, ylabel, legend=False):
        fig, axes = _grid(n)
        fig.suptitle(f'{title}  [{inv} / {name}]', fontsize=13, fontweight='bold')
        for ax, s in zip(axes, strategies):
            panel(ax, s)
            ax.set_title(_stitle(s), fontsize=7)
            ax.tick_params(labelsize=6)
            ax.grid(alpha=0.3)
        if axes:
            axes[0].set_ylabel(ylabel, fontsize=8)
            if legend:
                axes[0].legend(fontsize=6, loc='upper right')
        plt.tight_layout(rect=(0, 0, 1, 0.98))
        _save_close(fig, os.path.join(ps_dir, fname))

    _metric_grid('grid_batch_duration.png', 'Batch duration (rolling mean)', _panel_duration, 'sim time')
    _metric_grid('grid_sigma_fd.png',
                 'Layout efficiency: optimal / realised Sigma f*D (%)' if optimal > 0 else 'Sigma f*D (lower better)',
                 _panel_eff, '% of optimal' if optimal > 0 else 'Sigma f*D')
    _metric_grid('grid_churn.png', 'Inventory churn (% of bins moved / batch)', _panel_churn, '% / batch')
    _metric_grid('grid_task_duration.png', 'Task (aisle) duration distribution (mean + median)',
                 _panel_taskdur, 'count')
    _metric_grid('grid_task_over_time.png', 'Task duration over time (median, mean, IQR)',
                 _panel_task_overtime, 'task duration', legend=True)

    # ── per-strategy SCORECARDS: one image per strategy ────────────────────────
    for s in strategies:
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 3.4))
        fig.suptitle(_full_title(s), fontsize=12, fontweight='bold')
        _panel_duration(a1, s); a1.set_title('Batch duration', fontsize=10)
        a1.set_xlabel('batch'); a1.grid(alpha=0.3)
        _panel_eff(a2, s)
        a2.set_title('Sigma f*D ' + ('% of optimal' if optimal > 0 else '(raw)'), fontsize=10)
        a2.set_xlabel('batch'); a2.grid(alpha=0.3)
        _panel_churn(a3, s); a3.set_title('Churn (% bins/batch)', fontsize=10)
        a3.set_xlabel('batch'); a3.grid(alpha=0.3)
        plt.tight_layout(rect=(0, 0, 1, 0.92))
        _save_close(fig, os.path.join(ps_dir, f"strat_{s['key']}.png"))

    # ── rectangular SUMMARY: headline metrics across strategies, side by side ──
    ylabels = [_stitle(s) for s in strategies]
    yc      = [s['color'] for s in strategies]
    ypos    = np.arange(n)
    mean_dur = [df_b[s['key']]['duration'].mean()        if not df_b[s['key']].empty else 0.0 for s in strategies]
    mean_thr = [df_b[s['key']]['completion_rate'].mean() if not df_b[s['key']].empty else 0.0 for s in strategies]

    def _mean_eff(s):
        df = df_b[s['key']]
        if df.empty or optimal <= 0:
            return 0.0
        return float((optimal / df['sigma_fd'].clip(lower=1e-9) * 100.0).mean())
    mean_eff = [_mean_eff(s) for s in strategies]

    fig, (b1, b2, b3) = plt.subplots(1, 3, figsize=(16, max(6.0, n * 0.24)), sharey=True)
    fig.suptitle(f'Strategy summary  [{inv} / {name}]  (n={n}, baseline {_stitle(strategies[0])})',
                 fontsize=13, fontweight='bold')
    b1.barh(ypos, mean_dur, color=yc); b1.set_title('Mean batch duration (lower better)', fontsize=10)
    b1.set_yticks(ypos); b1.set_yticklabels(ylabels, fontsize=6); b1.invert_yaxis(); b1.grid(axis='x', alpha=0.3)
    b2.barh(ypos, mean_thr, color=yc); b2.set_title('Mean throughput (higher better)', fontsize=10)
    b2.grid(axis='x', alpha=0.3)
    b3.barh(ypos, mean_eff, color=yc); b3.set_title('Mean Sigma f*D efficiency % (higher better)', fontsize=10)
    b3.grid(axis='x', alpha=0.3)
    plt.tight_layout(rect=(0, 0, 1, 0.98))
    _save_close(fig, os.path.join(ps_dir, 'summary.png'))

    # ── comparison suite: faceted / overlay / top + breakdown, plus series.json ─
    compare_dir = os.path.join(run_dir, 'compare')
    _emit_comparison_suite(strategies, S, compare_dir, top_n, f'{inv} / {name}',
                           top_by=top_by)
    # task-time-per-strategy comparison (raw steady-state distributions; per-config only)
    _task_box(strategies, df_t, f'Steady-state task duration by strategy  [{inv} / {name}]',
              os.path.join(compare_dir, 'breakdown', 'task_duration_by_strategy.png'))
    _dump_series(strategies, S, os.path.join(run_dir, 'series.json'))

    # ── statistical significance suite (paired by batch_id over steady state) ──
    if not shared.get('no_stats', False):
        try:
            from Stats_Analysis import run_config_stats
            maxb = max((int(df_b[s['key']]['batch_id'].max())
                        for s in strategies if not df_b[s['key']].empty), default=0)
            ss_lo = maxb - _WIN + 1
            run_config_stats(strategies, df_b, df_t, ss_lo,
                             os.path.join(run_dir, 'stats'), log)
        except Exception as exc:                                   # noqa: BLE001
            log.error(f'  stats suite failed for {name}: {exc!r}')

    log.info(f'  Saved {n} strategies: per_strategy/ + compare/ + series.json -> {run_dir}')
