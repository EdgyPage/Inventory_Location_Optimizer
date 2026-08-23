"""task_time.duration — per-arm task-duration distributions, absolute and vs FIFO.

Two figures in the task_time family:

  * the absolute view — one box per arm of steady-state task durations, in the time
    unit `chartkit.to_time` picks from every arm's values pooled together (a per-task
    duration is seconds, so a fixed hours axis read 0.0008 and nothing else),
    arms sorted by median (best on the left), the y-range trimmed to the pooled
    2–98 percentile band (stated in a corner note) so a handful of outlier tasks
    cannot flatten the boxes.  The FIFO baseline's box is edge-marked in the
    baseline style and is the ONLY legend entry — the x labels already name every
    arm, so a color legend would restate them.
  * the delta view — per-arm % improvement vs FIFO as a dot with a 95% CI whisker:
    per-batch MEAN task durations are paired against FIFO's on the batches the two
    runs share, each pair becomes a % improvement, and the t-interval of those
    paired diffs is the whisker.  Sorted, with the zero line as the FIFO reference.

Data logic ported from the retired steady-state task-duration box chart (the `win`
steady-state tail and the per-arm duration pulls).  Baseline = strategies[0].
Params: win (steady-state tail width in batches).
"""
import os

import numpy as np
from matplotlib.lines import Line2D

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.style import _stitle, _WIN
from Optimization.Performance_Evaluations.common.stats_core import _descriptives


def _room_below(ch, labels):
    """Deepen the reserved bottom margin so rotated category labels are not
    clipped — chartkit sizes the grid from panel geometry and cannot see
    tick-label extents.  The provenance footer band stays clear below them."""
    need = 0.62 + 0.05 * max((len(str(t)) for t in labels), default=0)
    ch.axes[0].get_subplotspec().get_gridspec().update(
        bottom=min(0.55, min(2.8, need) / ch.fig.get_figheight()))


def _ss_durations(ctx, s, win):
    """Steady-state task durations for one arm, RAW sim milliseconds (ported tail
    selection).  Unit conversion waits for the caller, which pools every arm on the
    axis before choosing one."""
    df = ctx.task_df(s['key'])
    if df.empty:
        return np.array([])
    d = df[df['batch_id'] >= df['batch_id'].max() - win]['duration'].values
    return np.asarray(d, dtype=float)


def _absolute_figure(ctx, out, win):
    avail, data = [], []
    for s in ctx.strategies:
        d = _ss_durations(ctx, s, win)
        if len(d):
            avail.append(s)
            data.append(d)
    if not avail:
        return
    # every box shares one y-axis, so every arm's durations go into ONE unit decision
    div, unit = chartkit.time_units(np.concatenate(data))
    data = [np.asarray(d, dtype=float) / div for d in data]
    order = np.argsort([float(np.median(d)) for d in data])
    avail = [avail[i] for i in order]
    data = [data[i] for i in order]
    base_key = ctx.base['key']

    n = len(avail)
    ch = chartkit.make(panels=1, panel_w=chartkit.width_for_categories(n),
                       panel_h=4.6, legend='gutter',
                       legend_labels=['FIFO baseline (marked box)'])
    ax = ch.ax
    xs = np.arange(1, n + 1)
    bp = ax.boxplot(data, showfliers=False, patch_artist=True, widths=0.6,
                    medianprops=dict(color='black'))
    for patch, s in zip(bp['boxes'], avail):
        patch.set_facecolor(chartkit.strategy_color(s, ctx.strategies))
        patch.set_alpha(0.8)
        if s['key'] == base_key:
            patch.set_edgecolor(chartkit.BASELINE_STYLE['color'])
            patch.set_linewidth(2.0)
    ax.plot(xs, [float(np.mean(d)) for d in data], 'D', color='black', ms=3)
    xlabels = [_stitle(s) + (' (FIFO)' if s['key'] == base_key else '')
               for s in avail]
    ax.set_xticks(xs)
    ax.set_xticklabels(xlabels, rotation=90, fontsize=6)
    ax.set_ylabel(f'task duration ({unit}, steady state)', fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    _room_below(ch, xlabels)

    pooled = np.concatenate(data)
    lo, hi = np.percentile(pooled, [2, 98])
    pad = 0.05 * ((hi - lo) or 1.0)
    ax.set_ylim(lo - pad, hi + pad)
    ax.text(0.005, 0.008, 'y-range trimmed to the pooled 2–98th percentile band',
            transform=ax.transAxes, fontsize=6, color='#777777', va='bottom')

    handles = [Line2D([], [], color='none', marker='s', ms=9,
                      markerfacecolor='#dddddd',
                      markeredgecolor=chartkit.BASELINE_STYLE['color'],
                      markeredgewidth=2.0, label='FIFO baseline (marked box)')]
    ch.legend(handles=handles)
    ch.title('Task duration by arm, ranked',
             f'last {win} batches, sorted by median · ◆ = mean')
    ch.save(os.path.join(out, 'absolute_task_duration_ranked.png'), view='absolute')


def _delta_figure(ctx, out):
    """Pair per-batch MEAN task durations vs FIFO's over shared batches."""
    base = ctx.base
    tb = ctx.task_df(base['key'])
    if tb.empty:
        return
    base_means = tb.groupby('batch_id')['duration'].mean()

    rows = []
    for s in ctx.strategies:
        if s['key'] == base['key']:
            continue
        t = ctx.task_df(s['key'])
        if t.empty:
            continue
        means = t.groupby('batch_id')['duration'].mean()
        common = sorted(set(base_means.index) & set(means.index))
        if len(common) < 3:
            continue
        diffs = np.array([chartkit.improvement_pct(
                              float(means.loc[b]), float(base_means.loc[b]),
                              lower_is_better=True)
                          for b in common if base_means.loc[b]], dtype=float)
        desc = _descriptives(diffs)
        if not np.isfinite(desc['mean']):
            continue
        rows.append((s, desc['mean'], desc['ci_lo'], desc['ci_hi']))
    if not rows:
        return
    rows.sort(key=lambda r: r[1], reverse=True)     # best on the left

    n = len(rows)
    ch = chartkit.make(panels=1, panel_w=chartkit.width_for_categories(n),
                       panel_h=4.2, legend='gutter',
                       legend_labels=['FIFO baseline (zero line)'])
    ax = ch.ax
    xs = np.arange(n)
    mid = np.array([r[1] for r in rows], float)
    lo = np.array([r[2] for r in rows], float)
    hi = np.array([r[3] for r in rows], float)
    chartkit.draw_ci(ax, xs, lo, hi, band=False, color='#555555')
    for x, (s, m, _lo, _hi) in zip(xs, rows):
        ax.plot([x], [m], 'o', ms=6, color=chartkit.strategy_color(s, ctx.strategies),
                markeredgecolor='white', markeredgewidth=0.5, zorder=3)
    chartkit.reference_line(ax, 0.0, orient='y')
    xlabels = [_stitle(s) for s, *_r in rows]
    ax.set_xticks(xs)
    ax.set_xticklabels(xlabels, rotation=90, fontsize=6)
    ax.margins(x=max(0.03, 0.5 / max(1, n)))
    _room_below(ch, xlabels)
    tag = chartkit.pct_axis(ax, better='up')
    ax.set_ylabel(f'mean task duration improvement vs FIFO {tag}', fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    chartkit.data_ylim(ax, np.concatenate([lo, hi, mid]), include=(0.0,))

    ch.legend(handles=[chartkit.baseline_handle('FIFO baseline (zero line)')])
    ch.title('Task duration vs FIFO, paired',
             'dot = mean of paired per-batch % diffs · whisker = 95% t-interval')
    ch.save(os.path.join(out, 'delta_task_duration_vs_fifo.png'), view='delta')


@evaluation(key='task_time.duration', label='Task duration: ranked boxes + paired deltas',
            scope='config', needs=('task',), defaults={'win': 50},
            family='task_time', shape=('ranked', 'serial'),
            quantities=('task_mean_duration',),
            views_pending=(
                ('percent', 'the ranked improvement in mean task duration, which is '
                            'the view a reader compares arms with when the absolute '
                            'durations differ by a few percent; not written yet'),))
def render(ctx, params):
    win = int(params.get('win', _WIN) or _WIN)
    out = io.out_dir(ctx)
    _absolute_figure(ctx, out, win)
    _delta_figure(ctx, out)
