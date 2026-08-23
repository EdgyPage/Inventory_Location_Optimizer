"""agg.sig — the cross-profile significance FIGURES (the aggregate mirror of the
per-config significance suite, over the steady-state scalar metrics).

Reuses the shared significance painters and the shared aggregate computation from the
aggregate tables module, so the panels show exactly the numbers the tables carry.  The
`by_initial` fork (default True) renders the headline assignments × metrics improvement
heatmap plus one forest plot per assignment function; the all-strategies fork renders
one merged distribution+effect panel per steady-state metric.  Distribution axes are in
presentation units — items-per-hour for rates, and for a DURATION the time unit resolved
at render time from that metric's own pooled values, because a fixed hours axis reads
0.0008 whenever the metric's magnitude is seconds; effects and % improvements are
unitless.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, present
from Optimization.Performance_Evaluations.common.stats_core import (
    _AGG_METRICS, _boot_ci, _rank_biserial)
from Optimization.Performance_Evaluations.significance.panels import (
    effect_heatmap, forest_panel, merged_effect_panel)
from Optimization.Performance_Evaluations.aggregate.tables import (
    compute_aggregate_by_initial, compute_aggregate_stats, paired_profile_diagnosis)


@evaluation(key='agg.sig', label='Cross-profile significance figures',
            scope='aggregate', needs=('series',),
            family='significance', shape='effect',
            defaults={'by_initial': True})
def render(ctx, params):
    out = io.out_dir(ctx)
    if params.get('by_initial'):
        _render_by_initial(ctx, out)
    else:
        _render_all(ctx, out)


def _render_all(ctx, out):
    keys, colors, _rows, per_metric = compute_aggregate_stats(ctx.profile_series_list)
    if len(keys) < 2:
        ctx.log.warning(f'  aggregate significance {ctx.pickcfg}: <2 common strategies')
        return
    n_done = 0
    for name, d in per_metric.items():
        conv, unit = present.for_metric(name, present.pooled(d['box_values']))
        try:
            path = merged_effect_panel(
                keys, colors, d['box_values'], d['tests'],
                os.path.join(out, f'effect_{name}.png'),
                title=f'{name} — cross-profile effects',
                lower_is_better=d['lower'], conv=conv, unit_label=unit)
            n_done += bool(path)
        except Exception as exc:                                  # noqa: BLE001
            ctx.log.error(f'  aggregate effect panel failed for {name}: {exc!r}')
    ctx.log.info(f'  aggregate significance: {n_done} merged panels -> {out}')


def _render_by_initial(ctx, out):
    _, per_fn = compute_aggregate_by_initial(ctx.profile_series_list)
    if not per_fn:
        # Say WHICH reason.  The retired message asserted "no uni/opt pairs" for every
        # empty result; on a two-inventory sweep there are 17 pairs and the real reason is
        # that a paired test over 2 profiles is not a test.  An absence the reader cannot
        # diagnose reads as a bug — and this one spent its whole life behind a NameError,
        # so nobody ever saw either message.
        ctx.log.warning(f'  aggregate by-initial significance {ctx.pickcfg}: nothing to '
                        f'render — {paired_profile_diagnosis(ctx.profile_series_list)}')
        return
    fns = list(per_fn)
    names = [name for name, _f, _l in _AGG_METRICS]
    lower_of = {name: lower for name, _f, lower in _AGG_METRICS}

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
                   title='Opt vs uni start — cross-profile % improvement',
                   cbar_label='% improvement (opt vs uni)')

    for fn in fns:
        rows = []
        for name in names:
            det = per_fn[fn]['metrics'].get(name)
            if det is None or not len(det['u']):
                continue
            lower = lower_of[name]
            # Median dot with the bootstrap of that same median, matching the heatmap
            # cell above (a ratio of medians) — see the per-config twin.
            arr = chartkit.improvement_pct_series(det['o'], det['u'],
                                                  lower_is_better=lower)
            pct_v = float(np.median(arr)) if arr.size else float('nan')
            lo, hi = _boot_ci(arr)
            r = _rank_biserial(det['o'], det['u'])
            rows.append(dict(name=name, pct=pct_v, lo=lo, hi=hi,
                             p=det['p_wilcoxon'], effect=(-r if lower else r)))
        forest_panel(rows, os.path.join(out, f'effect_{fn}.png'),
                     title=f'{fn} — opt vs uni (profiles)')
    ctx.log.info(f'  aggregate by-initial significance: heatmap + {len(fns)} '
                 f'forests -> {out}')
