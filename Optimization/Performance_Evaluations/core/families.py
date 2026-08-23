"""The chart-family grammar — which VIEWS a figure carries, and where the answer comes from.

A family is one folder under a leaf's figures/ tree (figures/<family>/) holding every
figure that answers one class of question.  A VIEW is the measurement stance of a single
figure, named by its filename prefix:

    absolute_  raw quantities in honest units (hours, items, % of time)
    percent_   improvement vs the baseline, positive = better (chartkit.improvement_pct)
    delta_     the same comparison in the quantity's own units, not as a ratio
    effect_    effect sizes + significance (the discriminating stance at n=75, where
               p-stars alone are all ***)
    table_     a tabular summary rendered as a figure

## Views are DERIVED, not declared

Each family used to carry an editorial `views=` allow-list and each evaluation an
editorial `views=` claim, and the result was exactly the drift you would predict:
`throughput` structurally could not have a delta while `labor` had to; `layout.churn`
shipped absolute-only beside `layout.travel`'s two views; `task_time.breakdown`
absolute-only beside `task_time.duration`'s; `headline.all_arms` percent-only beside
`headline.rollup`'s two.  `agg.traj` emitted `percent` while its family REQUIRED
absolute+percent, and nothing noticed — the `required` field claimed a golden test
asserted it and no such test existed.  `RUN_SCOPE_FAMILIES` was declared and read by
nothing at all.

Worse than any single gap: `layout`'s allow-list had no `percent` in it, so the author of
`layout.travel`'s comparison figure had nowhere honest to put a percent view and saved one
under `delta_travel_vs_baseline.png`.  An editorial allow-list does not merely permit
drift; it manufactures mislabelling.

So a family now declares only what a family IS — the question it answers and the scope its
figures live at.  What views a figure carries comes from
`core.quantities.derive_views(quantity, shape)`: a property of the thing being measured and
of the mark drawing it, neither of which any single module gets a vote on.

## The transition

Evaluations still carrying a hand-written `views=` are listed in `LEGACY_VIEWS` with the
reason they have not moved, and the count is capped.  An entry there is a debt with a name,
not a permission.
"""
from __future__ import annotations

VIEWS = ('absolute', 'percent', 'delta', 'effect', 'table')

#: family -> what it is.  `scope` is where its figures land, and it is load-bearing rather
#: than documentation: `cost` renders at RUN scope, into the run root's own dossier tree
#: rather than a per-leaf one, so there are nine families and only eight per-leaf figure
#: globs in the run-tree contract.  A generator that iterated all nine would move
#: `schema_id`.  Resolve the actual directory through `runschema.resolver_for`, never by
#: spelling it here.
FAMILIES: dict = {
    'headline':     dict(scope='leaf',
                         charter='the two or three numbers a decision rests on'),
    'trajectories': dict(scope='leaf',
                         charter='how a quantity moves over the run, arm by arm'),
    'labor':        dict(scope='leaf',
                         charter='where the picking hours go, and how many are saved'),
    'throughput':   dict(scope='leaf',
                         charter='how many items got done, and how fast they got done'),
    'task_time':    dict(scope='leaf',
                         charter='the shape of one picker task'),
    'layout':       dict(scope='leaf',
                         charter='what the placement rule did to the warehouse itself'),
    'significance': dict(scope='leaf',
                         charter='effect sizes and their intervals — is a gap separable'),
    'diagnostics':  dict(scope='leaf',
                         charter='raw operational read-outs for inspection. THE deliberate '
                                 'exemption from comparison work: a grid or a scorecard is '
                                 'read to find out what happened, not to rank anything'),
    'cost':         dict(scope='run',
                         charter='what a rule costs to RUN, in real wall-clock seconds — '
                                 'orthogonal to every family above, which measure modeled '
                                 'warehouse labor'),
}

#: Families whose figures live at the run root rather than in a per-leaf tree.
RUN_SCOPE_FAMILIES = tuple(f for f, d in FAMILIES.items() if d['scope'] == 'run')

#: Families whose figures live in a per-leaf tree — the ones the run-tree contract needs a
#: `figures_<family>_pngs` glob for.
LEAF_FAMILIES = tuple(f for f, d in FAMILIES.items() if d['scope'] == 'leaf')

#: eval key -> why it still hand-writes `views=` instead of declaring a shape and the
#: quantities it draws.  A debt with a name and a ceiling, not a permission.
LEGACY_VIEWS: dict = {}

#: Raising this is a decision someone makes on purpose, in a diff, with a reason beside it.
LEGACY_VIEWS_CEILING = 12


def check_view(eval_key: str, view: str) -> None:
    """Raise unless `view` is one the evaluation actually carries.

    Called from chartkit at save time, inside a render only.  There is no family
    allow-list any more: the evaluation's own view set is derived from its quantities and
    its mark, so a view outside it is not a permissions problem — it is a figure whose
    stance nothing asked for.
    """
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    ev = EVAL_BY_KEY.get(eval_key)
    if ev is None:                      # not this layer's problem (driver sets real keys)
        return
    if view not in VIEWS:
        raise ValueError(f'{eval_key}: {view!r} is not a view (known: {VIEWS})')
    if not ev.family:
        raise ValueError(f'{eval_key} declares no family but saved a {view!r} view')
    if view not in ev.views:
        raise ValueError(
            f'{eval_key}: saved a {view!r} view, but its declared quantities drawn with '
            f'a {ev.shape!r} mark carry {tuple(sorted(ev.views))}. Either the mark is '
            f'wrong for this figure, or the view belongs in views_suppressed with the '
            f'reason it must not be drawn.')


def figures_subdir(family: str) -> str:
    """The out_subdir a family's figures land in."""
    if family not in FAMILIES:
        raise KeyError(f'unknown chart family {family!r} '
                       f'(known: {tuple(FAMILIES)})')
    return f'figures/{family}'
