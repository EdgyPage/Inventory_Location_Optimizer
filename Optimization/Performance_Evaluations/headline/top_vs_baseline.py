"""headline.top_vs_baseline — how the best arms compare to the FIFO baseline.

Two figures in the headline family:

  * the percent view — grouped bars of % improvement of each top arm vs the FIFO
    baseline across the headline metric groups (positive = better, one-decimal data
    labels).  Metric groups whose swings are an order of magnitude larger than the
    rest (layout f·D, typically) move to a SECOND panel that says out loud its scale
    is independent, so they stop crushing the 2–3% labor effects — the flaw of the
    retired single-axis chart.  Each metric group is annotated with the best arm's
    paired effect vs FIFO: matched-pairs rank-biserial r plus a Holm-corrected
    Wilcoxon p.
  * the table view — one row per top arm: labor and throughput % vs FIFO, paired
    Hedges g, a 95% interval for the labor figure, and the Wilcoxon p LAST — demoted,
    because with n≈75 paired batches essentially every p is three-star; the subtitle
    says exactly that and the effect columns carry the discrimination.  The labor
    figure and its interval are the same estimator (the median of the per-batch
    improvements, and the bootstrap of that median), so the interval always contains
    the number it sits beside.

Both figures pair each arm against FIFO over the batches the two runs share
(`_paired`, ported verbatim from the retired compare module), so labor and
throughput answer one question with one method.  Baseline = strategies[0] (the
FIFO/uniform-random arm).  Params: top_n, top_by.
"""
import os

import numpy as np
import scipy.stats as st
from matplotlib import transforms as mtransforms
from matplotlib.patches import Patch

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle, _TOP_DIMS
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.common.frames import _metric_series
from Optimization.Performance_Evaluations.common.stats_core import (
    _rank_biserial, _hedges_g_paired, _boot_ci, _holm)

# ── the headline metric groups ──────────────────────────────────────────────────
# (label, ss_field, lower_is_better, per-batch source, per-batch column)
# ss_* scalars feed the bars; source/column feed the paired per-batch effect stats.
# All four success metrics: task makespan (a) & batch makespan (b), the throughput
# off each (c, d), plus the layout travel objective.
_METRIC_GROUPS = [
    ('Task makespan',        'ss_prod_hours', True,  'task_sum', 'duration'),
    ('Batch makespan',       'ss_dur',        True,  'batch',    'duration'),
    ('Thr / batch makespan', 'ss_thr',        False, 'batch',    'completion_rate'),
    ('Thr / task makespan',  'ss_thr_task',   False, 'batch',    'thr_task'),
    ('Layout total f·D',     'ss_sigma',      True,  'batch',    'sigma_fd'),
]

#: a metric group whose largest |%| swing exceeds this multiple of the median group
#: swing gets its own panel — the "order of magnitude larger" rule made operational.
_UNSHARED_RATIO = 8.0


def _impr(val, base, lower):
    """Signed % improvement vs baseline (always oriented so higher = better)."""
    if (base is None or val is None or not np.isfinite(base)
            or not np.isfinite(val) or base == 0):
        return float('nan')
    return ((base - val) / base * 100.0) if lower else ((val - base) / base * 100.0)


def _paired(ctx, s, source, col):
    """(pct_change, wilcoxon_p, n_batches, base_vals, strat_vals) for one per-batch
    metric vs the FIFO baseline, paired over the batches the two runs share.

    pct = (median(strat) − median(base))/median(base) · 100 — the RAW sign; callers
    reorient it through the one improvement convention before display.
    """
    base = ctx.base
    pb = _metric_series(ctx.batch_df(base['key']), ctx.task_df(base['key']), source, col, 0)
    ps = _metric_series(ctx.batch_df(s['key']),    ctx.task_df(s['key']),    source, col, 0)
    common = sorted(set(pb.index) & set(ps.index))
    if len(common) < 3:
        return float('nan'), float('nan'), 0, np.array([]), np.array([])
    b = pb.loc[common].values.astype(float)
    v = ps.loc[common].values.astype(float)
    mb = float(np.median(b))
    pct = (float(np.median(v)) - mb) / mb * 100.0 if mb else float('nan')
    try:
        p = float(st.wilcoxon(v, b).pvalue) if np.any(v != b) else 1.0
    except ValueError:
        p = float('nan')
    return pct, p, len(common), b, v


def _pair_diffs(b, v, lower):
    """Per-batch paired % improvement (positive = better) for an aligned pair, with the
    pairs where the comparison is undefined dropped by the shared rule."""
    return chartkit.improvement_pct_series(v, b, lower_is_better=lower)


# ── the percent view ────────────────────────────────────────────────────────────

def _percent_figure(ctx, selected, S, baseline, out, top_n, top_by):
    bd = S[baseline['key']]
    # improvement value per (group, arm), the improvement-oriented sign throughout
    vals = {}
    for (label, field, lower, _src, _col) in _METRIC_GROUPS:
        vals[label] = [_impr(S[s['key']].get(field), bd.get(field), lower)
                       for s in selected]

    # split off groups whose swings dwarf the rest (they crushed the old chart)
    gmax = {lbl: (np.nanmax(np.abs(v)) if np.any(np.isfinite(v)) else 0.0)
            for lbl, v in vals.items()}
    med = float(np.median([m for m in gmax.values() if np.isfinite(m)]) or 0.0)
    big = [lbl for lbl in vals
           if med > 0 and gmax[lbl] > _UNSHARED_RATIO * med]
    if len(big) == len(vals):
        big = []                                    # everything huge = nothing special
    panels = ([lbl for lbl in vals if lbl not in big], big)
    panels = tuple(p for p in panels if p)

    # per-group effect annotation: best arm vs FIFO, Holm-corrected across groups
    ann = {}
    pvals = []
    for (label, _field, lower, src, col) in _METRIC_GROUPS:
        v = np.asarray(vals[label], float)
        if not np.any(np.isfinite(v)):
            pvals.append(np.nan)
            continue
        s_best = selected[int(np.nanargmax(v))]
        _pct, p, _n, b_arr, v_arr = _paired(ctx, s_best, src, col)
        rb = (_rank_biserial(b_arr, v_arr) if lower
              else _rank_biserial(v_arr, b_arr)) if len(b_arr) else np.nan
        ann[label] = dict(rb=rb, p=p)
        pvals.append(p)
    adj = _holm(np.asarray(pvals, float))
    for (label, *_rest), pa in zip(_METRIC_GROUPS, adj):
        if label in ann:
            ann[label]['p'] = pa

    labels = [_stitle(s) for s in selected] + ['FIFO baseline (zero line)']
    ch = chartkit.make(panels=len(panels), ncols=len(panels),
                       panel_w=max(3.6, 1.9 * max(len(p) for p in panels)),
                       panel_h=4.0, legend='gutter', legend_labels=labels,
                       legend_title='top arm')
    nstr = len(selected)
    width = 0.8 / max(1, nstr)
    for ax, group in zip(ch.axes, panels):
        x = np.arange(len(group))
        panel_vals = []
        for i, s in enumerate(selected):
            v = [vals[lbl][i] for lbl in group]
            offs = x + (i - (nstr - 1) / 2.0) * width
            ax.bar(offs, v, width, color=chartkit.strategy_color(s, ctx.strategies))
            for rx, vv in zip(offs, v):
                if np.isfinite(vv):
                    ax.text(rx, vv, f'{vv:+.1f}%', ha='center',
                            va='bottom' if vv >= 0 else 'top', fontsize=6)
            panel_vals.extend(v)
        ax.axhline(0, color=chartkit.BASELINE_STYLE['color'], lw=1.2)
        ax.set_xticks(x)
        ax.set_xticklabels(group, fontsize=7)
        tag = chartkit.pct_axis(ax, better='up')
        ax.set_ylabel(f'improvement vs FIFO {tag}', fontsize=8)
        ax.tick_params(axis='y', labelsize=8)
        chartkit.data_ylim(ax, panel_vals, pad=0.10, include=(0.0,))
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + 0.14 * (hi - lo))      # headroom row for effect labels
        tr = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
        for gx, lbl in zip(x, group):
            a = ann.get(lbl)
            if a and np.isfinite(a.get('rb', np.nan)):
                ax.text(gx, 0.99, chartkit.effect_label(a['p'], a['rb'], kind='r'),
                        transform=tr, ha='center', va='top', fontsize=6,
                        color='#555555')
    if len(panels) == 2:
        chartkit.annotate_unshared(ch.axes[1], axis='y')
    # widen the reserved left margin: '%' tick labels + the y label need ~1 inch
    ch.axes[0].get_subplotspec().get_gridspec().update(
        left=min(0.98, 1.05 / ch.fig.get_figwidth()))

    handles = [Patch(color=chartkit.strategy_color(s, ctx.strategies),
                     label=_stitle(s)) for s in selected]
    handles.append(chartkit.baseline_handle('FIFO baseline (zero line)'))
    ch.legend(handles=handles, title='top arm')
    sub = (f'top {top_n} per {top_by}' if top_by in _TOP_DIMS else f'top {top_n}')
    ch.title('Top arms vs FIFO baseline',
             f'{sub} — note per group: best arm r, Holm p')
    ch.save(os.path.join(out, 'percent_top_vs_baseline.png'), view='percent')


# ── the table view ──────────────────────────────────────────────────────────────

def _table_figure(ctx, selected, S, baseline, out):
    """Rows are labelled with _stitle (initial|assignment|reslot), not the bare
    assignment name — Opt|Rank_labor and Uni|Rank_labor are different arms and must
    not collapse to one label."""
    bd = S.get(baseline['key'])
    base_prod = bd.get('ss_prod_hours') if bd else None

    rows, lab_ps, nb = [], [], 0
    for s in selected:
        lab_pct, lab_p, n, b_l, v_l = _paired(ctx, s, 'task_sum', 'duration')
        thr_pct, _p, _n, _b, _v = _paired(ctx, s, 'batch', 'completion_rate')
        nb = max(nb, n)
        # The point estimate and the interval beside it must estimate the SAME thing.
        # The labor column is a median-based improvement (a per-batch ratio has a long
        # right tail; one near-empty batch makes the mean of it unstable), so the
        # interval is the bootstrap of the median of the per-batch improvements — not a
        # t-interval on their mean, which can and does exclude the median it sits next to.
        diffs = _pair_diffs(b_l, v_l, lower=True)
        lab = float(np.median(diffs)) if diffs.size else float('nan')
        g = _hedges_g_paired(b_l, v_l) if len(b_l) else float('nan')
        rows.append(dict(s=s, lab=lab, thr=thr_pct, g=g, ci=_boot_ci(diffs)))
        lab_ps.append(lab_p)
    adj = _holm(np.asarray(lab_ps, float))

    fmt = lambda v: '-' if not np.isfinite(v) else f'{v:+.1f}%'
    col_labels = ['arm (initial | assignment | reslot)',
                  'labor Δ% vs FIFO (+ = better)',
                  'throughput Δ% vs FIFO (+ = better)',
                  'Hedges g (paired, labor)',
                  '95% CI of paired Δ% (labor)',
                  'Wilcoxon p (Holm)']
    cell_text = []
    for r, p in zip(rows, adj):
        lo, hi = r['ci']
        ci_txt = ('-' if not (np.isfinite(lo) and np.isfinite(hi))
                  else f'[{lo:+.1f}, {hi:+.1f}]')
        p_txt = '-' if not np.isfinite(p) else f'{p:.1g} {chartkit.stars(p)}'
        g_txt = '-' if not np.isfinite(r['g']) else f'{r["g"]:.2f}'
        cell_text.append([_stitle(r['s']), fmt(r['lab']), fmt(r['thr']),
                          g_txt, ci_txt, p_txt])

    ch = chartkit.make(panels=1, panel_w=12.5,
                       panel_h=chartkit.height_for_categories(
                           len(cell_text), per=0.42, base=1.2),
                       legend='none')
    ax = ch.ax
    ax.axis('off')
    ax.grid(False)
    tbl = ax.table(cellText=cell_text, colLabels=col_labels,
                   loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1, 1.6)
    cells = tbl.get_celld()
    for c in range(len(col_labels)):
        hc = cells[(0, c)]
        hc.set_facecolor('#34495e')
        hc.set_text_props(color='white', fontweight='bold', fontsize=7)

    # HOURS IS FIXED HERE, deliberately: this is the labor-hours-per-batch quantity the
    # labor family plots on an explicit hours axis, quoted as a reader-facing figure in
    # a subtitle.  "Hours" is the unit the sentence is about, not a scale chosen from
    # the data, so it must not float with the run's magnitude — the two would disagree.
    base_txt = ('' if base_prod is None or not np.isfinite(base_prod)
                else f' · FIFO Σ task time / batch ≈ '
                     f'{float(chartkit.to_hours(base_prod)):,.1f} {chartkit.HOURS}')
    ch.legend()
    ch.title('Top arms vs FIFO — labor & throughput',
             f'n={nb} paired batches: nearly every p is *** at this n — '
             f'read g and the CI{base_txt}')
    ch.save(os.path.join(out, 'table_top_vs_baseline.png'), view='table')


@evaluation(key='headline.top_vs_baseline',
            label='Top arms vs FIFO baseline (% bars + effect table)',
            scope='config', needs=('series', 'batch', 'task'),
            defaults={'top_n': 3, 'top_by': 'initial'},
            family='headline', views=('percent', 'table'))
def render(ctx, params):
    S = ctx.series()
    baseline = ctx.base
    if S.get(baseline['key']) is None:
        return
    top_n = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'initial') or 'initial'
    selected, _ = _select_top(ctx.strategies, S, top_n, top_by)
    selected = [s for s in selected if s['key'] != baseline['key']]
    if not selected:
        return
    out = io.out_dir(ctx)
    _percent_figure(ctx, selected, S, baseline, out, top_n, top_by)
    _table_figure(ctx, selected, S, baseline, out)
