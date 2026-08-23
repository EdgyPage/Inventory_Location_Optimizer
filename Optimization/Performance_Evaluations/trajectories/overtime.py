"""trajectories.overtime — every over-time metric in both measurement stances.

Deliberately a THIN driver: the drawing lives in `common/painters.paint_overtime`, which
is also what the aggregate stage renders with — that shared body is what keeps the
per-config and cross-profile trajectory charts visually identical, so nothing here may
style, size, or name anything.  This module's whole job is to walk the shared metric
vocabulary (`painters.overtime_metrics`) and request each metric in each declared view;
the painter owns units, the baseline treatment, filenames, and saving.

The ctx access idiom is the retired overlay module's, verbatim: the strategy list and the
baseline come off the context facade (`ctx.strategies` / `ctx.base` — focus-filtered once
at context build, baseline = first strategy), and the series dict via `ctx.series()`.
"""
from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io, painters


@evaluation(key='trajectories.overtime',
            label='Over-time trajectories, absolute + % vs baseline',
            scope='config', needs=('series',),
            family='trajectories', views=('absolute', 'percent'))
def render(ctx, params):
    S = ctx.series()
    out = io.out_dir(ctx)                 # figures/trajectories, from the family
    for m in painters.overtime_metrics():
        for view in ('absolute', 'percent'):
            painters.paint_overtime(ctx.strategies, S, m, ctx.base, out, view=view)
