"""sig.suite / sig.by_initial — the per-config significance FIGURES.

Both draw from the SAME computation the tables evaluations write (imported from the
tables stats module), so figure and table can never disagree.  The retired layout was
4 PNGs × 11 metrics for the all-strategies suite plus a full per-assignment-function
directory tree for the by-initial fork (~748 files); this pair replaces it with:

  * `sig.suite`      — ONE merged panel per metric (distribution + pairwise
                       rank-biserial ladder, Holm-p in the row labels);
  * `sig.by_initial` — the headline assignments × metrics improvement heatmap, plus one
                       forest plot per assignment function (metrics as rows, mean paired
                       % improvement with its 95% CI — the tables keep the median-based
                       % companion; both are improvement-oriented, positive = better).

Presentation units on distribution axes only: durations in hours, rates in items/hour
(effects and % improvements are unitless).  ss_lo=0, as the tables use.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io
from Optimization.Performance_Evaluations.common.stats_core import (
    _METRICS, _descriptives, _rank_biserial)
from Optimization.Performance_Evaluations.significance.panels import (
    effect_heatmap, forest_panel, merged_effect_panel)
from Optimization.Performance_Evaluations.tables.stats_csv import (
    compute_by_initial, compute_config_stats)

_PER_HOUR = 3.6e6            # raw rates are items per sim-millisecond

#: metric -> (conv into presentation units, axis label) for the distribution panels.
#: The analytical-labor metrics stay raw: W is the cost model's unit, not a sim-ms
#: duration, so the hours conversion would be a lie.
_PRESENT = {
    'production_time':      (chartkit.to_hours, f'Σ task time per batch ({chartkit.HOURS})'),
    'objective_task_labor': (None,              'E[task labor] (W)'),
    'objective_total_labor': (None,             'Σ task labor (W)'),
    'task_mean_duration':   (chartkit.to_hours, f'mean task duration ({chartkit.HOURS})'),
    'makespan':             (chartkit.to_hours, f'batch makespan ({chartkit.HOURS})'),
    'throughput':           (lambda v: np.asarray(v, float) * _PER_HOUR, 'items / hour'),
    'throughput_task':      (lambda v: np.asarray(v, float) * _PER_HOUR, 'items / hour'),
    'queue_depth':          (None, 'put-away queue depth (units)'),
    'sigma_fd':             (None, 'total f·D'),
    'picking_pct':          (None, '% of time picking'),
    'reorder_churn':        (None, 'reorder placements / batch'),
}


def _present(name):
    return _PRESENT.get(name, (None, name))


@evaluation(key='sig.suite', label='Merged effect panels (all strategies)',
            scope='config', needs=('batch', 'task', 'breakdown'),
            family='significance', views=('effect',))
def render_suite(ctx, params):
    _, per_metric = compute_config_stats(
        ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0)
    keys = [s['key'] for s in ctx.strategies]
    colors = [s.get('color', '#888888') for s in ctx.strategies]
    out = io.out_dir(ctx)
    n_done = 0
    for name, d in per_metric.items():
        conv, unit = _present(name)
        try:
            path = merged_effect_panel(
                keys, colors, d['box_values'], d['tests'],
                os.path.join(out, f'effect_{name}.png'),
                title=f'{name} — distribution & effects',
                lower_is_better=d['lower'], conv=conv, unit_label=unit)
            n_done += bool(path)
        except Exception as exc:                                  # noqa: BLE001
            ctx.log.error(f'  effect panel failed for {name}: {exc!r}')
    ctx.log.info(f'  significance: {n_done} merged panels -> {out}')


@evaluation(key='sig.by_initial', label='Opt-vs-uni heatmap + per-fn forests',
            scope='config', needs=('batch', 'task', 'breakdown'),
            family='significance', views=('effect',), by_initial=True)
def render_by_initial(ctx, params):
    _, per_fn = compute_by_initial(
        ctx.strategies, ctx.batch_frames(), ctx.task_frames(), 0)
    if not per_fn:
        ctx.log.warning(f'  by-initial significance: no uni/opt pairs in {ctx.name}')
        return
    out = io.out_dir(ctx)
    fns = list(per_fn)
    names = [name for name, _s, _c, _l in _METRICS]
    lower_of = {name: lower for name, _s, _c, lower in _METRICS}

    shape = (len(fns), len(names))
    pct, eff, pmat = (np.full(shape, np.nan) for _ in range(3))
    for fi, fn in enumerate(fns):
        for mi, name in enumerate(names):
            det = per_fn[fn]['metrics'].get(name)
            if det is None:
                continue
            lower = lower_of[name]
            pct[fi, mi] = chartkit.improvement_pct(
                det['opt_median'], det['uni_median'], lower_is_better=lower)
            r = _rank_biserial(det['o'], det['u'])
            eff[fi, mi] = -r if lower else r
            pmat[fi, mi] = det['p_wilcoxon']
    effect_heatmap(fns, names, pct, eff, pmat,
                   os.path.join(out, 'effect_heatmap.png'),
                   title='Optimal vs uniform start — % improvement',
                   cbar_label='% improvement (opt vs uni)')

    for fn in fns:
        rows = []
        for name in names:
            det = per_fn[fn]['metrics'].get(name)
            if det is None:
                continue
            lower = lower_of[name]
            arr = np.array([chartkit.improvement_pct(o_i, u_i, lower_is_better=lower)
                            for u_i, o_i in zip(det['u'], det['o'])], dtype=float)
            desc = _descriptives(arr)
            r = _rank_biserial(det['o'], det['u'])
            rows.append(dict(name=name, pct=desc['mean'],
                             lo=desc['ci_lo'], hi=desc['ci_hi'],
                             p=det['p_wilcoxon'], effect=(-r if lower else r)))
        forest_panel(rows, os.path.join(out, f'effect_{fn}.png'),
                     title=f'{fn} — opt vs uni initial')
    ctx.log.info(f'  by-initial significance: heatmap + {len(fns)} forests -> {out}')
