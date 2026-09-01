"""The Evaluation descriptor + @evaluation decorator + module-level registry.

Mirrors the registry style of Optimization/config/strategies.py (a dataclass per item, a flat
list, and a by-key dict), but populated by a decorator so a graph module self-registers
on import.  A graph's `render(ctx, params)` does the actual plotting; the descriptor
carries only the metadata the driver needs to schedule it.

**`views` is derived.**  A figure evaluation declares the QUANTITIES it draws and the MARK
it draws them with, and its view set follows from `core.quantities.derive_views`.  It was
a free per-evaluation declaration, and the drift it produced is catalogued in
`core/families.py`; the short version is that two figures of the same quantity through the
same mark carried different view sets because two authors made two different calls, and in
one case the family's allow-list left a percent view with nowhere honest to go, so it
shipped under a `delta_` filename.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Evaluation:
    key:        str                          # 'headline.top_vs_baseline'
    label:      str
    scope:      str                          # 'per_strategy' | 'config' | 'aggregate' | 'run'
                                             #   per_strategy/config: one channel-run leaf
                                             #   aggregate: cross-PROFILE within one cell
                                             #   run: the whole run root, across cells — the
                                             #     only scope that can see both schedulers,
                                             #     which is what a cross-cell claim needs
    needs:      tuple = ()                    # subset of {'batch','task','series','breakdown'}
    defaults:   dict = field(default_factory=dict)
    out_subdir: str | tuple = ''             # relative dir(s) under the run/agg root; a tuple
                                             # declares a multi-dir owner, '' declares the
                                             # root itself.  Figure evals DERIVE this from
                                             # `family` (figures/<family>) — see the decorator.
    family:     str | None = None            # chart family (core/families.py); None for
                                             # non-figure evals (series/tables writers)
    quantities: tuple = ()                   # keys into core.quantities.BY_KEY
    quantities_optional: tuple = ()          # ALSO drawn, but the figure survives without
                                             #   them. Same input to the view derivation;
                                             #   excluded from the era gate. See the
                                             #   decorator for when this is legitimate.
    shape:      tuple = ()                   # the mark(s): core.quantities.SHAPES by
                                             #   name.  A tuple when one evaluation draws
                                             #   more than one KIND of mark — a ranked bar
                                             #   panel and a rendered table are different
                                             #   marks with different capabilities, and
                                             #   the view set is the union.
    views_suppressed: tuple = ()             # ((view, reason), ...) — MUST NOT be drawn
    views_pending: tuple = ()                # ((view, reason), ...) — SHOULD be drawn,
                                             #   not implemented yet.  Deliberately a
                                             #   different field from `views_suppressed`:
                                             #   "never" and "not yet" are different
                                             #   claims, and collapsing them is how a
                                             #   backlog becomes a design.
    views:      tuple = ()                   # DERIVED from quantities x shape; validated
                                             #   per save by chartkit against the grammar
    by_initial: bool = False                 # stats-only structural fork (uni-vs-opt per fn)
    render:     Callable = None              # render(ctx, params) -> None


EVALUATIONS: list[Evaluation] = []
EVAL_BY_KEY: dict[str, Evaluation] = {}


def _reasons_are_real(key, entries, field):
    for view, reason in entries:
        if not (reason or '').strip():
            raise ValueError(
                f'{key} lists {view!r} in {field} with no reason. A view withheld '
                f'without a recorded reason is indistinguishable from one nobody got '
                f'round to, which is the whole failure this derivation exists to end.')


def _derive_views(key, family, quantities, shape, suppressed, pending):
    """The view set for one figure evaluation — quantities x mark, minus its suppressions.

    An INSPECTION or EFFECT mark answers for itself: a scorecard has no baseline in it and
    a distribution+effect panel IS the contrast, so neither needs a quantity declared to
    know what it can show.
    """
    from Optimization.Performance_Evaluations.core import quantities as q
    base: set = set()
    for shape_name in shape:
        if shape_name not in q.SHAPE_BY_NAME:
            raise ValueError(f'{key}: unknown mark {shape_name!r} '
                             f'(known: {tuple(q.SHAPE_BY_NAME)})')
        shp = q.SHAPE_BY_NAME[shape_name]
        if shp.self_describing:
            # a scorecard has no baseline in it; an effect panel IS the contrast; a table
            # IS its cells.  None of the three needs a quantity to know what it can show.
            base |= shp.fixed
            continue
        if not quantities:
            raise ValueError(
                f'{key}: a {shape_name!r} mark must declare the quantities it draws — '
                f'that is what its view set is derived from. Add them to '
                f'core/quantities.py if they are not there yet.')
        for name in quantities:
            if name not in q.BY_KEY:
                raise ValueError(f'{key}: no quantity declares {name!r}')
            base |= q.derive_views(q.BY_KEY[name], shp)
    _reasons_are_real(key, suppressed, 'views_suppressed')
    _reasons_are_real(key, pending, 'views_pending')
    withheld = {v for v, _r in suppressed} | {v for v, _r in pending}
    stale = withheld - set(base)
    if stale:
        raise ValueError(
            f'{key} withholds {sorted(stale)}, which the derivation does not produce '
            f'anyway. A stale exception licenses a future regression — delete it.')
    return tuple(sorted(set(base) - withheld))


def evaluation(*, key, label, scope, needs=(), defaults=None, out_subdir=None,
               family=None, quantities=(), quantities_optional=(), shape=None,
               views_suppressed=(), views_pending=(), views=(), by_initial=False):
    """Decorator: register the wrapped render fn as an Evaluation.

    A figure eval declares `family=` and its out_subdir is DERIVED (figures/<family>) —
    passing both is an error, so a family's folder can never be retyped inconsistently.
    Non-figure evals declare out_subdir explicitly ('tables', or '' for the leaf root).

    A figure eval also declares `shape=` (the mark) and `quantities=` (what it draws), and
    its `views` are derived from those.  `views=` may still be passed, but ONLY by an
    evaluation listed in `families.LEGACY_VIEWS` with the reason it has not moved — a debt
    with a name, not a permission.

    ## `quantities_optional=`

    A quantity the figure draws WHEN THE RUN CAN ANSWER IT and omits when it cannot.  It
    feeds the view derivation exactly like `quantities=` — it is drawn, so it must be
    drawable — but `requests.era_shortfall` ignores it, so a capability the run lacks costs
    the figure that quantity rather than the whole render.

    This is a narrow permission, not a softer `quantities=`.  It is only honest when the
    render genuinely degrades per quantity and SAYS SO — `headline.top_vs_baseline`, whose
    two funnel-appended groups (total production hours, yard overage) are dropped from the
    panel with a log line when no arm has a finite value, so the reader sees five groups
    rather than seven blank-panelled ones.  An evaluation whose figure would be a lie
    without the quantity declares it in `quantities=` and takes the refusal: that is what
    the whole `yard` family does, and correctly.

    Returns the plain function unchanged so it stays directly unit-testable.
    """
    def _wrap(fn):
        sub = out_subdir
        derived = tuple(views)
        marks = (shape,) if isinstance(shape, str) else tuple(shape or ())
        if family is not None:
            from Optimization.Performance_Evaluations.core.families import (
                FAMILIES, LEGACY_VIEWS, figures_subdir)
            if sub is not None:
                raise ValueError(f'{key}: declare family= OR out_subdir=, not both')
            if family not in FAMILIES:
                raise ValueError(f'{key}: unknown chart family {family!r}')
            if not marks:
                if key not in LEGACY_VIEWS:
                    raise ValueError(
                        f'{key}: a figure evaluation declares shape= (and the quantities '
                        f'it draws), from which its views are derived. Hand-written '
                        f'views= is accepted only for an evaluation listed in '
                        f'families.LEGACY_VIEWS with the reason it has not moved yet.')
                if not views:
                    raise ValueError(f'{key} is in LEGACY_VIEWS but declares no views')
            else:
                if views:
                    raise ValueError(
                        f'{key}: declares both shape= and views=. Views are DERIVED from '
                        f'the quantities and the mark; a hand-written set beside them is '
                        f'the drift this replaced.')
                overlap = set(quantities) & set(quantities_optional)
                if overlap:
                    raise ValueError(
                        f'{key}: {sorted(overlap)} is declared both required and '
                        f'optional. A quantity is one or the other — the difference is '
                        f'whether the era gate refuses the render for it.')
                derived = _derive_views(key, family,
                                        tuple(quantities) + tuple(quantities_optional),
                                        marks, tuple(views_suppressed),
                                        tuple(views_pending))
            sub = figures_subdir(family)
        elif marks or quantities or quantities_optional:
            raise ValueError(f'{key}: shape=/quantities= describe a FIGURE evaluation, '
                             f'but this one declares no family')
        ev = Evaluation(key=key, label=label, scope=scope, needs=tuple(needs),
                        defaults=dict(defaults or {}), out_subdir=sub or '',
                        family=family, quantities=tuple(quantities),
                        quantities_optional=tuple(quantities_optional), shape=marks,
                        views_suppressed=tuple(views_suppressed),
                        views_pending=tuple(views_pending), views=derived,
                        by_initial=by_initial, render=fn)
        if key in EVAL_BY_KEY:
            raise ValueError(f'duplicate evaluation key {key!r}')
        EVALUATIONS.append(ev)
        EVAL_BY_KEY[key] = ev
        return fn
    return _wrap
