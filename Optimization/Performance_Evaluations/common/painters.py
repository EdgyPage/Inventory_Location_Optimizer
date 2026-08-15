"""Shared figure painters — the builders more than one evaluation draws with.

Promoted here from the modules that first grew them so that aggregate/cross_profile (which
re-renders the whole compare suite on cross-profile averages) imports a PUBLISHED shared
surface instead of five private helpers across four compare modules — and so top_vs_baseline
no longer reaches into stats/ for `_stars`.  Each painter keeps its original module as its
primary consumer; the docstrings travel with the code.

`overtime_metrics` / `top_tag` are the shared filename-stem vocabulary: every over-time
figure basename and every `top{n}[_by_{dim}]_` prefix in the published site composes from
these two functions, so THIS module is where those names are minted.
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.style import (
    _TOP_DIMS, _LINESTYLES, _assign_color_map, _ir_style_map, _pct_delta, _stitle,
    legend_right)
from Optimization.Performance_Evaluations.common.series import _select_top


def overtime_metrics(agg=False):
    unit = ' (× baseline)' if agg else ''
    return [
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
             f='production_time_over_time',
             t='Production time (total task time per batch, sim units)',
             yl='production time (sim units)' + unit),
        dict(x='batch', y='sigma_fd', blo=None, bhi=None,
             f='layout_travel_over_time',
             t='Layout travel cost over time (total f*D, lower=better)',
             yl='total f*D' + unit),
    ]


def top_tag(top_n, top_by):
    return f"top{top_n}" + (f"_by_{top_by}" if top_by in _TOP_DIMS else "")


def _stars(p: float) -> str:
    if p is None or not np.isfinite(p):
        return ''
    return '***' if p < 1e-3 else '**' if p < 1e-2 else '*' if p < 5e-2 else 'ns'


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
    fig.legend(handles=handles, loc='center left', bbox_to_anchor=(1.0, 0.5),
               fontsize=7, title='assignment')
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
    leg1 = legend_right(ax, ch, anchor=(1.02, 1.0), fontsize=8, title='assignment')
    ax.add_artist(leg1)
    legend_right(ax, sh, anchor=(1.02, 0.45), fontsize=8, title='initial|reslot')
    plt.tight_layout()
    _save_close(fig, path)


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
    legend_right(ax, fontsize=8)
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
    legend_right(ax)
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
