"""compare.top_vs_baseline — how the best runs compare to the FIFO/random baseline.

Two stakeholder-facing artifacts under compare/:
  * top_vs_baseline.png        — grouped bars, % improvement of each top run vs the FIFO
                                 baseline across the headline metrics (higher = better).
  * top_vs_baseline_table.png  — a supporting table graphic (for the site): one row per
                                 top run with its assignment function, the % difference in
                                 TOTAL task time vs FIFO, and the statistical significance
                                 (paired Wilcoxon p over all batches).

Baseline = strategies[0] (the FIFO/uniform-random run).  Params: top_n, top_by.
"""
import os

import numpy as np
import matplotlib.pyplot as plt
import scipy.stats as st

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _TOP_DIMS, legend_right
from Performance_Evaluations.common.series import _select_top
from Performance_Evaluations.common.frames import _metric_series
from Performance_Evaluations.stats.plots import _stars

# steady-state scalars for the overview bars: (label, ss_field, lower_is_better)
_BAR_METRICS = [
    ('Total task time', 'ss_prod_hours', True),
    ('Makespan',        'ss_dur',        True),
    ('Throughput',      'ss_thr',        False),
    ('Layout total f*D', 'ss_sigma',     True),
]


def _impr(val, base, lower):
    """Signed % improvement vs baseline (always oriented so higher = better)."""
    if (base is None or val is None or not np.isfinite(base)
            or not np.isfinite(val) or base == 0):
        return float('nan')
    return ((base - val) / base * 100.0) if lower else ((val - base) / base * 100.0)


def _prod_paired(ctx, s):
    """(pct_change, wilcoxon_p) of TOTAL task time (Σ task duration / batch) vs the FIFO
    baseline, paired over all common batches.  pct = (strat − base)/base · 100
    (negative ⇒ less total task time than FIFO ⇒ better)."""
    base = ctx.base
    pb = _metric_series(ctx.batch_df(base['key']), ctx.task_df(base['key']), 'task_sum', 'duration', 0)
    ps = _metric_series(ctx.batch_df(s['key']),    ctx.task_df(s['key']),    'task_sum', 'duration', 0)
    common = sorted(set(pb.index) & set(ps.index))
    if len(common) < 3:
        return float('nan'), float('nan')
    b = pb.loc[common].values.astype(float)
    v = ps.loc[common].values.astype(float)
    mb = float(np.median(b))
    pct = (float(np.median(v)) - mb) / mb * 100.0 if mb else float('nan')
    try:
        p = float(st.wilcoxon(v, b).pvalue) if np.any(v != b) else 1.0
    except ValueError:
        p = float('nan')
    return pct, p


def _bar_chart(ctx, selected, S, baseline, path, top_n, top_by):
    bd = S.get(baseline['key'])
    nstr = len(selected)
    x = np.arange(len(_BAR_METRICS))
    width = 0.8 / max(1, nstr)
    fig, ax = plt.subplots(figsize=(max(9, len(_BAR_METRICS) * 2.4), 6))
    for i, s in enumerate(selected):
        d = S.get(s['key'])
        vals = [_impr(d.get(f), bd.get(f), low) for (_, f, low) in _BAR_METRICS]
        offs = x + (i - (nstr - 1) / 2.0) * width
        ax.bar(offs, vals, width, color=s['color'], label=_stitle(s))
        for rx, vv in zip(offs, vals):
            if np.isfinite(vv):
                ax.text(rx, vv, f'{vv:+.0f}%', ha='center',
                        va='bottom' if vv >= 0 else 'top', fontsize=6)
    ax.axhline(0, color='k', lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([m[0] for m in _BAR_METRICS])
    ax.set_ylabel('% improvement vs FIFO baseline (higher = better)')
    ax.grid(axis='y', alpha=0.3)
    legend_right(ax, fontsize=8, title='top run')
    sub = f'top {top_n} per {top_by}' if top_by in _TOP_DIMS else f'top {top_n}'
    ax.set_title(f'Top runs vs baseline ({_stitle(baseline)}) — {sub}  [{ctx.title}]',
                 fontsize=12, fontweight='bold')
    plt.tight_layout()
    _save_close(fig, path)


def _table_graphic(ctx, selected, S, baseline, path):
    bd = S.get(baseline['key'])
    base_prod = bd.get('ss_prod_hours') if bd else None
    col_labels = ['Assignment function', 'Total task time vs FIFO', 'Significance (Wilcoxon p)']
    cell_text, pcts = [], []
    for s in selected:
        pct, p = _prod_paired(ctx, s)
        asn = s.get('assignment') or _stitle(s)
        pct_txt = '-' if not np.isfinite(pct) else f'{pct:+.1f}%'
        sig_txt = '-' if not np.isfinite(p) else f'{p:.3g}  {_stars(p)}'
        cell_text.append([asn, pct_txt, sig_txt])
        pcts.append(pct)

    nrows = len(cell_text)
    fig, ax = plt.subplots(figsize=(10, 1.6 + 0.55 * (nrows + 1)))
    ax.axis('off')
    tbl = ax.table(cellText=cell_text, colLabels=col_labels, loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.7)
    cells = tbl.get_celld()
    for c in range(len(col_labels)):                  # header styling
        hc = cells[(0, c)]
        hc.set_facecolor('#34495e')
        hc.set_text_props(color='white', fontweight='bold')
    for r, pct in enumerate(pcts, start=1):           # green = less work, red = more
        if np.isfinite(pct):
            cells[(r, 1)].set_facecolor('#d4efdf' if pct < 0 else '#f9d7d4')

    base_txt = ('' if base_prod is None or not np.isfinite(base_prod)
                else f'   (FIFO total task time ~ {base_prod:,.0f} sim units)')
    ax.set_title(
        f'Top runs vs FIFO baseline — total task time & significance  [{ctx.title}]\n'
        f'negative % = less total task time than FIFO (better);  '
        f'* p<.05  ** p<.01  *** p<.001{base_txt}',
        fontsize=11, fontweight='bold', pad=16)
    _save_close(fig, path)


@evaluation(key='compare.top_vs_baseline', label='Top runs vs FIFO baseline (% diff + table)',
            scope='config', needs=('series', 'batch', 'task'), out_subdir='compare',
            defaults={'top_n': 3, 'top_by': 'global'})
def render(ctx, params):
    S = ctx.series()
    baseline = ctx.base
    if S.get(baseline['key']) is None:
        return
    top_n = int(params.get('top_n', 3) or 3)
    top_by = params.get('top_by', 'global') or 'global'
    selected, _ = _select_top(ctx.strategies, S, top_n, top_by)
    selected = [s for s in selected if s['key'] != baseline['key']]   # exclude the baseline itself
    if not selected:
        return
    out = os.path.join(ctx.run_dir, 'compare')
    os.makedirs(out, exist_ok=True)
    _bar_chart(ctx, selected, S, baseline, os.path.join(out, 'top_vs_baseline.png'), top_n, top_by)
    _table_graphic(ctx, selected, S, baseline, os.path.join(out, 'top_vs_baseline_table.png'))
