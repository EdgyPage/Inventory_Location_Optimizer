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
from Optimization.Performance_Evaluations.core.registry import (
    evaluation, EVAL_BY_KEY)
from Optimization.Performance_Evaluations.common import io, painters
from Optimization.Performance_Evaluations.core import quantities as _quantities
from Optimization.Performance_Evaluations.core.quantities import (
    SERIES_GATED, SERIES_UNGATED)

#: what the shared over-time painter draws, in its declared render order — the
#: UNION is `painters.overtime_metrics()`'s own `SERIES_ORDER`, so this evaluation cannot
#: come to declare a different set from the one it renders.  The split is the era gate's:
#: the gated pair is DRAWN exactly like the rest and omitted per quantity when the run
#: predates it, rather than costing the whole family its render (`registry.evaluation`).
SERIES_QUANTITIES = SERIES_UNGATED
SERIES_OPTIONAL = SERIES_GATED


def _dropped(ctx):
    """Gated quantities THIS run cannot answer, named so the absence is a statement.

    `quantities_optional=` buys the family its render on an archived run; it does not
    buy silence.  A panel that is simply not there is indistinguishable from one nobody
    got round to, which is the drift the era gate exists to end -- so the omission is
    logged with the capability that would have served it.
    """
    have = ctx.capabilities() if hasattr(ctx, 'capabilities') else None
    if have is None:
        return ()
    return tuple(k for k in SERIES_OPTIONAL
                 if _quantities.BY_KEY[k].capability not in have)


@evaluation(key='trajectories.overtime',
            label='Over-time trajectories, absolute + % vs baseline',
            scope='config', needs=('series',),
            family='trajectories', shape='serial',
            quantities=SERIES_QUANTITIES,
            quantities_optional=SERIES_OPTIONAL)
def render(ctx, params):
    S = ctx.series()
    out = io.out_dir(ctx)                 # figures/trajectories, from the family
    views = EVAL_BY_KEY['trajectories.overtime'].views
    skip = _dropped(ctx)
    if skip:
        ctx.log.info(f'  trajectories: this run predates {", ".join(skip)}; those '
                     f'panels are omitted, not empty')
    n = 0
    for m in painters.overtime_metrics():
        if m['q'].key in skip:
            continue
        for view in views:
            n += bool(painters.paint_overtime(ctx.strategies, S, m, ctx.base, out,
                                              view=view))
    ctx.log.info(f'  trajectories: {n} figures over {len(views)} views -> {out}')
