"""sig.suite / sig.by_initial — the per-config significance FIGURES.

Both draw from the SAME computation the tables evaluations write (imported from the
tables stats module), so figure and table can never disagree.  The retired layout was
4 PNGs × 11 metrics for the all-strategies suite plus a full per-assignment-function
directory tree for the by-initial fork (~748 files); this pair replaces it with:

  * `sig.suite`      — ONE merged panel per metric (distribution + pairwise
                       rank-biserial ladder, Holm-p in the row labels);
  * `sig.by_initial` — the headline assignments × metrics improvement heatmap, plus one
                       forest plot per assignment function (metrics as rows, the MEDIAN
                       paired % improvement with the bootstrap interval of that same
                       median, so the dot and its whiskers estimate one quantity and the
                       forest agrees with the heatmap cell above it; improvement-oriented,
                       positive = better).

Presentation units on distribution axes only: rates in items/hour, and every DURATION in
the time unit resolved at render time from that metric's own pooled distribution — a
metric whose median is seconds gets a seconds axis, not a column of 0.0008 hours
(effects and % improvements are unitless).  ss_lo=0, as the tables use.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, present
from Optimization.Performance_Evaluations.common.stats_core import (
    _METRICS, _boot_ci, _rank_biserial)
from Optimization.Performance_Evaluations.significance.panels import (
    effect_heatmap, forest_panel, merged_effect_panel)
from Optimization.Performance_Evaluations.tables.stats_csv import (
    compute_by_initial, compute_config_stats)


@evaluation(key='sig.suite', label='Merged effect panels (all strategies)',
            scope='config', needs=('batch', 'task', 'breakdown'),
            family='significance', shape='effect')
def render_suite(ctx, params):
    _, per_metric = compute_config_stats(ctx.strategies, ctx.metric_frames(), 0)
    keys = [s['key'] for s in ctx.strategies]
    colors = [s.get('color', '#888888') for s in ctx.strategies]
    out = io.out_dir(ctx)
    n_done = 0
    for name, d in per_metric.items():
        conv, unit = present.for_metric(name, present.pooled(d['box_values']))
        try:
            path = merged_effect_panel(
                keys, colors, d['box_values'], d['tests'],
                os.path.join(out, f'effect_{name}.png'),
                title=f'{name} — distribution & effects',
                lower_is_better=d['lower'], conv=conv, unit_label=unit,
                paired=d.get('paired'))
            n_done += bool(path)
        except Exception as exc:                                  # noqa: BLE001
            ctx.log.error(f'  effect panel failed for {name}: {exc!r}')
    ctx.log.info(f'  significance: {n_done} merged panels -> {out}')


@evaluation(key='sig.by_initial', label='Opt-vs-uni heatmap + per-fn forests',
            scope='config', needs=('batch', 'task', 'breakdown'),
            family='significance', shape='effect', by_initial=True)
def render_by_initial(ctx, params):
    _, per_fn = compute_by_initial(ctx.strategies, ctx.metric_frames(), 0)
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
            # The heatmap cell for this same (fn, metric) is a ratio of medians, so the
            # forest dot must be a median too — a mean point beside a median cell under
            # one shared label is two different quantities wearing one name.  The
            # interval is the bootstrap of THAT median, so it cannot exclude its own dot.
            arr = chartkit.improvement_pct_series(det['o'], det['u'],
                                                  lower_is_better=lower)
            pct_v = float(np.median(arr)) if arr.size else float('nan')
            lo, hi = _boot_ci(arr)
            r = _rank_biserial(det['o'], det['u'])
            rows.append(dict(name=name, pct=pct_v, lo=lo, hi=hi,
                             p=det['p_wilcoxon'], effect=(-r if lower else r)))
        forest_panel(rows, os.path.join(out, f'effect_{fn}.png'),
                     title=f'{fn} — opt vs uni initial')
    ctx.log.info(f'  by-initial significance: heatmap + {len(fns)} forests -> {out}')
