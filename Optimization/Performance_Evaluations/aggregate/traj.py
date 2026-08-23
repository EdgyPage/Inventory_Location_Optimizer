"""agg.traj — cross-profile over-time trajectories, percent view only.

The aggregate series (AggregateContext.agg_series) are already ×-baseline normalized
per profile and averaged across profiles (common/series.py), so the baseline strategy's
own curve sits at ≈1.0 — exactly the reference the shared over-time painter's percent
view divides against, which is why this stage renders the SAME figures as the
per-config trajectories family with zero aggregate-specific drawing code.  An absolute
view would just restate the normalization (every axis "× baseline"), so only the
percent view is declared.
"""
from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io, painters


@evaluation(key='agg.traj', label='Cross-profile trajectories (% vs baseline)',
            scope='aggregate', needs=('series',),
            family='trajectories', views=('percent',))
def render(ctx, params):
    strategies, S = ctx.agg_series()
    if not strategies:
        ctx.log.warning(f'  aggregate trajectories {ctx.pickcfg}: no usable series')
        return
    out = io.out_dir(ctx)
    base = strategies[0]                 # per-profile order is baseline-first
    n_done = 0
    for m in painters.overtime_metrics():
        path = painters.paint_overtime(strategies, S, m, base, out,
                                       view='percent', agg=True)
        n_done += bool(path)
    ctx.log.info(f'  aggregate trajectories: {n_done} figures -> {out} '
                 f'({ctx.n_profiles} profiles)')
