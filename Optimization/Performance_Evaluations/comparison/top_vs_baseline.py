"""compare.top_vs_baseline — how the best runs compare to the FIFO/random baseline.

Two stakeholder-facing artifacts under compare/:
  * top_vs_baseline.png        — grouped bars, % improvement of each top run vs the FIFO
                                 baseline across the headline metrics (higher = better).
  * top_vs_baseline_table.png  — a supporting table graphic (for the site): one row per
                                 top run, with the % difference in TOTAL task time (labor)
                                 AND in throughput vs FIFO, plus the statistical
                                 significance (paired Wilcoxon p over all batches).

Both table columns come from `_paired`, so they share one window and one method — a labor
number measured over all batches next to a throughput number measured over the last 50 would
be two different questions in one table.  The bar chart is the steady-state view; the table is
the paired-over-the-whole-run view.  They answer the same question and need not match to the
decimal.

Baseline = strategies[0] (the FIFO/uniform-random run).  Params: top_n, top_by.
"""
import os

import numpy as np
import matplotlib.pyplot as plt
import scipy.stats as st

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.style import _stitle, _TOP_DIMS, legend_right
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.common.frames import _metric_series
from Optimization.Performance_Evaluations.stats.plots import _stars

# steady-state scalars for the overview bars: (label, ss_field, lower_is_better)
# All four success metrics: task makespan (a) & batch makespan (b), and the throughput off each.
_BAR_METRICS = [
    ('Task makespan',        'ss_prod_hours', True),    # Σ task time = total labor (a)
    ('Batch makespan',       'ss_dur',        True),    # parallel wall-clock (b)
    ('Thr / batch makespan', 'ss_thr',        False),   # items / batch makespan (d)
    ('Thr / task makespan',  'ss_thr_task',   False),   # items / task makespan  (c)
    ('Layout total f*D',     'ss_sigma',      True),
]

# table cell shading — better / worse than the FIFO baseline
_GREEN, _RED = '#d4efdf', '#f9d7d4'


def _impr(val, base, lower):
    """Signed % improvement vs baseline (always oriented so higher = better)."""
    if (base is None or val is None or not np.isfinite(base)
            or not np.isfinite(val) or base == 0):
        return float('nan')
    return ((base - val) / base * 100.0) if lower else ((val - base) / base * 100.0)


def _paired(ctx, s, source, col):
    """(pct_change, wilcoxon_p, n_batches) for one per-batch metric vs the FIFO baseline,
    paired over the batches the two runs share.

    pct = (median(strat) − median(base))/median(base) · 100 — the RAW sign, so the caller
    decides which direction reads as better.  Used for both table columns:
      ('task_sum', 'duration')      → total task time = labor  (negative ⇒ better)
      ('batch',    'completion_rate') → throughput / batch makespan (positive ⇒ better)
    """
    base = ctx.base
    pb = _metric_series(ctx.batch_df(base['key']), ctx.task_df(base['key']), source, col, 0)
    ps = _metric_series(ctx.batch_df(s['key']),    ctx.task_df(s['key']),    source, col, 0)
    common = sorted(set(pb.index) & set(ps.index))
    if len(common) < 3:
        return float('nan'), float('nan'), 0
    b = pb.loc[common].values.astype(float)
    v = ps.loc[common].values.astype(float)
    mb = float(np.median(b))
    pct = (float(np.median(v)) - mb) / mb * 100.0 if mb else float('nan')
    try:
        p = float(st.wilcoxon(v, b).pvalue) if np.any(v != b) else 1.0
    except ValueError:
        p = float('nan')
    return pct, p, len(common)


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
    """The site's quick-takeaway table: labor AND throughput vs FIFO, one row per run.

    Rows are labelled with _stitle (initial|assignment|reslot), not the bare assignment name —
    Opt|Rank_labor and Uni|Rank_labor are different runs and must not collapse to one label.
    """
    bd = S.get(baseline['key'])
    base_prod = bd.get('ss_prod_hours') if bd else None
    col_labels = ['Run (initial | assignment | reslot)', 'Labor — total task time vs FIFO',
                  'Throughput vs FIFO', 'Significance (task time, Wilcoxon p)']
    fmt = lambda v: '-' if not np.isfinite(v) else f'{v:+.1f}%'
    cell_text, labor_pcts, thr_pcts, nb = [], [], [], 0
    for s in selected:
        lab_pct, p, n = _paired(ctx, s, 'task_sum', 'duration')
        thr_pct, _, _ = _paired(ctx, s, 'batch', 'completion_rate')
        nb = max(nb, n)
        sig_txt = '-' if not np.isfinite(p) else f'{p:.3g}  {_stars(p)}'
        cell_text.append([_stitle(s), fmt(lab_pct), fmt(thr_pct), sig_txt])
        labor_pcts.append(lab_pct)
        thr_pcts.append(thr_pct)

    nrows = len(cell_text)
    fig, ax = plt.subplots(figsize=(13, 1.8 + 0.55 * (nrows + 1)))
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
    # Shade each metric in ITS OWN direction: less labor is better, more throughput is better.
    for r, (lab, thr) in enumerate(zip(labor_pcts, thr_pcts), start=1):
        if np.isfinite(lab):
            cells[(r, 1)].set_facecolor(_GREEN if lab < 0 else _RED)
        if np.isfinite(thr):
            cells[(r, 2)].set_facecolor(_GREEN if thr > 0 else _RED)

    base_txt = ('' if base_prod is None or not np.isfinite(base_prod)
                else f'   (FIFO total task time ~ {base_prod:,.0f} sim units)')
    win_txt = f'both columns paired over the {nb} batches each run shares with FIFO' if nb else ''
    ax.set_title(
        f'Top runs vs FIFO baseline — labor & throughput  [{ctx.title}]\n'
        f'negative labor % = less total task time than FIFO (better);  '
        f'positive throughput % = more items per hour (better)\n'
        f'{win_txt};  * p<.05  ** p<.01  *** p<.001{base_txt}',
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
