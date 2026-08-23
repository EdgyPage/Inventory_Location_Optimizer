"""Shared figure painters — the builders more than one evaluation draws with.

Post-redesign this module holds exactly three things:

  * `overtime_metrics` / `top_tag` — the shared filename-stem vocabulary.  Every
    over-time figure basename and every `top{n}[_by_{dim}]` suffix in the published
    site composes from these two functions, so THIS module is where those names are
    minted (and where the figure-registry test looks for the stems).
  * `paint_overtime` — the one over-time painter, drawn through chartkit for both its
    views: 'absolute' (honest units — hours, items/hour, f·D) and 'percent'
    (improvement vs the baseline strategy, positive = better).  The config-stage
    trajectories family and the aggregate stage both render with it, which is what
    keeps their charts visually identical.
  * unit helpers binding the metric vocabulary to chartkit's unit policy.

Everything else the old painters carried (facet grids, top-N line picks, breakdown and
delta bars) either died in the redesign or moved into the single family module that owns
it now.
"""
import numpy as np

from Optimization.Performance_Evaluations.common import chartkit
from Optimization.Performance_Evaluations.common.style import _TOP_DIMS


def overtime_metrics():
    """The five over-time metric specs — stem (`f`), short title (`t`), units, and
    direction.  `conv` maps raw series values into presentation units; `yl` is the
    absolute-view axis label; `lower_is_better` orients the percent view."""
    per_h = 3.6e6            # raw rates are items per sim-millisecond
    return [
        dict(x='task_batch', y='task_median', blo='task_p25', bhi='task_p75',
             f='task_duration', t='Task duration (median + IQR)',
             conv=chartkit.to_hours, yl=f'task duration ({chartkit.HOURS})',
             lower_is_better=True),
        dict(x='task_batch', y='task_mean', blo=None, bhi=None,
             f='avg_task_duration', t='Average task duration',
             conv=chartkit.to_hours, yl=f'mean task duration ({chartkit.HOURS})',
             lower_is_better=True),
        dict(x='batch', y='thr', blo=None, bhi=None,
             f='throughput', t='Throughput',
             conv=lambda v: np.asarray(v, dtype=float) * per_h,
             yl='throughput (items / hour)', lower_is_better=False),
        dict(x='task_batch', y='prod_hours', blo=None, bhi=None,
             f='production_time', t='Production time per batch',
             conv=chartkit.to_hours,
             yl=f'total task time per batch ({chartkit.HOURS})', lower_is_better=True),
        dict(x='batch', y='sigma_fd', blo=None, bhi=None,
             f='layout_travel', t='Layout travel cost',
             conv=None, yl='total f·D (lower = better)', lower_is_better=True),
    ]


def top_tag(top_n, top_by):
    return f"top{top_n}" + (f"_by_{top_by}" if top_by in _TOP_DIMS else "")


def _conv(m, vals):
    return m['conv'](vals) if m.get('conv') else np.asarray(vals, dtype=float)


def paint_overtime(strategies, S, m, baseline, out_dir_path, *, view, agg=False):
    """Render one over-time metric in one view and return the saved path (or None
    when nothing could be drawn).

    view='absolute': every strategy in presentation units, baseline in BASELINE_STYLE.
    view='percent' : per-batch % improvement vs the baseline strategy, aligned on the
                     metric's own x — the baseline IS the zero line, so it is drawn as
                     a reference line, not a series.
    agg=True labels the aggregate stage (values are already ×-baseline normalized
    upstream; the absolute view is skipped there by the caller).
    """
    import os
    labels = [_label(s) for s in strategies] + ['FIFO baseline']
    ch = chartkit.make(panels=1, panel_w=6.4, panel_h=4.2,
                       legend='gutter', legend_labels=labels)
    ax = ch.ax
    db = S.get(baseline['key']) if baseline else None
    drawn = 0
    if view == 'absolute':
        if db is not None:
            chartkit.mark_baseline(ax, db[m['x']], _conv(m, db[m['y']]))
        for s in strategies:
            d = S.get(s['key'])
            if d is None or (baseline and s['key'] == baseline['key']):
                continue
            ax.plot(d[m['x']], _conv(m, d[m['y']]),
                    color=chartkit.strategy_color(s, strategies),
                    ls=chartkit.strategy_dash(s), lw=1.4, label=_label(s))
            if m['blo'] and len(strategies) <= 3 and d.get(m['blo']) is not None:
                chartkit.draw_ci(ax, d[m['x']], _conv(m, d[m['blo']]),
                                 _conv(m, d[m['bhi']]),
                                 color=chartkit.strategy_color(s, strategies))
            drawn += 1
        ax.set_ylabel(('× baseline' if agg else m['yl']))
        vals = [_conv(m, S[s['key']][m['y']]) for s in strategies if S.get(s['key'])]
        if vals:
            chartkit.shared_ylim([ax], vals)
        ch.title(m['t'])
    else:                                              # percent view
        if db is None:
            import matplotlib.pyplot as plt
            plt.close(ch.fig)
            return None
        bx = {int(b): v for b, v in zip(db[m['x']], np.asarray(db[m['y']], float))
              if v == v and v != 0}
        allv = []
        for s in strategies:
            d = S.get(s['key'])
            if d is None or s['key'] == baseline['key']:
                continue
            xs, ys = [], []
            for b, v in zip(d[m['x']], np.asarray(d[m['y']], float)):
                bv = bx.get(int(b))
                if bv is None or v != v:
                    continue
                xs.append(int(b))
                ys.append(chartkit.improvement_pct(
                    v, bv, lower_is_better=m['lower_is_better']))
            if not xs:
                continue
            ax.plot(xs, ys, color=chartkit.strategy_color(s, strategies),
                    ls=chartkit.strategy_dash(s), lw=1.4, label=_label(s))
            allv.append(ys)
            drawn += 1
        ax.axhline(0, **{**chartkit.BASELINE_STYLE, 'lw': 1.2})
        tag = chartkit.pct_axis(ax, better='up')
        ax.set_ylabel(f'improvement vs FIFO {tag}')
        if allv:
            chartkit.shared_ylim([ax], allv, include=(0.0,))
        ch.title(f"{m['t']} — % vs baseline")
    if not drawn:
        import matplotlib.pyplot as plt
        plt.close(ch.fig)
        return None
    ax.set_xlabel('batch')
    ch.legend(title='strategy')
    path = os.path.join(out_dir_path, f"{view}_{m['f']}.png")
    return ch.save(path, view=view)


def _label(s):
    parts = [p for p in (s.get('initial', ''), s.get('assignment', ''),
                         s.get('reslot', '')) if p]
    return '|'.join(parts) if parts else s.get('label', s.get('key', ''))
