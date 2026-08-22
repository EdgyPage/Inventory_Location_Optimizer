"""The chart-family grammar — which VIEWS each family of figures must and may carry.

A family is one folder under a leaf's figures/ tree (figures/<family>/) holding every
figure that answers one class of question.  A VIEW is the measurement stance of a single
figure, named by its filename prefix:

    absolute_  raw quantities in honest units (hours, items, % of time)
    percent_   improvement vs the baseline, positive = better (chartkit.improvement_pct)
    delta_     paired per-batch / cumulative differences over time
    effect_    effect sizes + significance (the discriminating stance at n=75, where
               p-stars alone are all ***)
    table_     a tabular summary rendered as a figure

The grammar exists so "absolute counts, percent views, deltas and significance baked in
as appropriate" is a CHECKED property, not an editorial habit: every evaluation declares
its family and views, `chartkit.Chart.save(view=...)` validates each file it writes, and
the writer-paths golden test asserts each family's `required` views actually exist on the
fixture render.  `diagnostics` is the deliberate exemption — raw operational read-outs
(grids, scorecards) whose job is inspection, not comparison.
"""
from __future__ import annotations

VIEWS = ('absolute', 'percent', 'delta', 'effect', 'table')

#: family -> allowed views, required views (asserted present on a full default render)
FAMILIES: dict = {
    'headline':     dict(views=('percent', 'table', 'absolute'), required=('percent',)),
    'trajectories': dict(views=('absolute', 'percent'),          required=('absolute', 'percent')),
    'labor':        dict(views=('absolute', 'percent', 'delta'), required=('delta',)),
    'throughput':   dict(views=('absolute', 'percent'),          required=('percent',)),
    'task_time':    dict(views=('absolute', 'percent', 'delta'), required=('absolute',)),
    'layout':       dict(views=('absolute', 'delta'),            required=('absolute',)),
    'significance': dict(views=('effect',),                      required=('effect',)),
    'diagnostics':  dict(views=('absolute',),                    required=()),
}


def check_view(eval_key: str, view: str) -> None:
    """Raise unless `view` is legal for the evaluation's declared family AND declared by
    the evaluation itself.  Called from chartkit at save time, inside a render only."""
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    ev = EVAL_BY_KEY.get(eval_key)
    if ev is None:                      # not this layer's problem (driver sets real keys)
        return
    if not ev.family:
        raise ValueError(f'{eval_key} declares no family but saved a {view!r} view')
    allowed = FAMILIES[ev.family]['views']
    if view not in allowed:
        raise ValueError(f'{eval_key}: view {view!r} is not in family '
                         f'{ev.family!r} (allowed: {allowed})')
    if ev.views and view not in ev.views:
        raise ValueError(f'{eval_key}: view {view!r} not among its declared '
                         f'views {ev.views}')


def figures_subdir(family: str) -> str:
    """The out_subdir a family's figures land in."""
    if family not in FAMILIES:
        raise KeyError(f'unknown chart family {family!r} '
                       f'(known: {tuple(FAMILIES)})')
    return f'figures/{family}'
