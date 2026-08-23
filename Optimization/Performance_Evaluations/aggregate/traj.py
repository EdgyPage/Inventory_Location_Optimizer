"""agg.traj — cross-profile over-time trajectories, percent view only.

The aggregate series (AggregateContext.agg_series) are already ×-baseline normalized
per profile and averaged across profiles (common/series.py), so the baseline strategy's
own curve sits at ≈1.0 — exactly the reference the shared over-time painter's percent
view divides against, which is why this stage renders the SAME figures as the
per-config trajectories family with zero aggregate-specific drawing code.  An absolute
view would just restate the normalization (every axis "× baseline"), so only the
percent view is declared.
"""
from Optimization.Performance_Evaluations.core.registry import (
    evaluation, EVAL_BY_KEY)
from Optimization.Performance_Evaluations.common import io, painters
from Optimization.Performance_Evaluations.core.quantities import SERIES_ORDER

#: what the shared over-time painter draws, in its declared render order — the
#: SAME tuple `painters.overtime_metrics()` walks, so this evaluation cannot come
#: to declare a different set from the one it renders.
SERIES_QUANTITIES = SERIES_ORDER


@evaluation(key='agg.traj', label='Cross-profile trajectories (% vs baseline)',
            scope='aggregate', needs=('series',),
            family='trajectories', shape='serial',
            quantities=SERIES_QUANTITIES,
            views_suppressed=(
                ('absolute', 'the aggregate series are already x-baseline normalized '
                             'per profile and averaged across profiles, so an '
                             'absolute view would restate the normalization: every '
                             'axis would read "x baseline" and every baseline curve '
                             'would sit at 1.0'),
                ('delta', 'a difference of two already-normalized ratios is not in '
                          'the quantity\'s units and is not in any other units '
                          'either; the percent view is the honest one at this '
                          'scope'),))
def render(ctx, params):
    strategies, S = ctx.agg_series()
    if not strategies:
        ctx.log.warning(f'  aggregate trajectories {ctx.pickcfg}: no usable series')
        return
    out = io.out_dir(ctx)
    base = strategies[0]                 # per-profile order is baseline-first
    n_done = 0
    for m in painters.overtime_metrics():
        for view in EVAL_BY_KEY['agg.traj'].views:
            n_done += bool(painters.paint_overtime(strategies, S, m, base, out,
                                                   view=view, agg=True))
    ctx.log.info(f'  aggregate trajectories: {n_done} figures -> {out} '
                 f'({ctx.n_profiles} profiles)')
