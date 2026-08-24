"""Presets — the manifest that decides which graphs run and with what params.

A preset bundles: `keys` (the enabled evaluation keys, in run order), `overrides`
(per-graph param overrides keyed by evaluation key), and `focus` (a context-level
strategy filter, 'uni' | 'opt' | 'all').  Toggle a graph by adding/removing its key;
tune a graph via its `overrides` entry — no central argparse to grow.

Keys are grouped by chart family (core/families.py): the tables group writes the tidy
CSV surface, the figure groups each own one folder of the leaf's figures tree.  The two
stats flavours stay mutually exclusive per preset, exactly as before the redesign:
'assignment' runs the flat suite, 'initial' the uni-vs-opt per-assignment fork.
"""

# ── the groups ───────────────────────────────────────────────────────────────────
# ORDER is declared; MEMBERSHIP is checked against the registry below.  The order is
# editorial — it is what the run log reads in, and for the run scope it is the order the
# dossier stages in — and a derived order would be alphabetical, which means nothing.
#
# What the check buys: six hand-written lists that name every evaluation a second time
# used to drift in one direction only and in silence.  A new evaluation registers, no
# preset names it, it renders nothing, and the sole symptom is a figure that does not
# appear.  Nothing raises, because a preset naming a SUBSET of the registry is exactly
# what a preset is — which is why the check has to be for completeness, not for validity.
_TABLES   = ['config.series', 'tables.per_run', 'tables.tidy',
             'tables.vs_baseline']
_HEADLINE = ['headline.top_vs_baseline', 'headline.all_arms', 'headline.rollup',
             'headline.throughput_vs_labor']
_TRENDS   = ['trajectories.overtime', 'labor.delta_topn', 'labor.per_batch',
             'labor.delta_grid', 'throughput.volume']
_DETAIL   = ['task_time.duration', 'task_time.breakdown',
             'layout.travel', 'layout.churn',
             'diagnostics.metric_grids', 'diagnostics.scorecards']
_AGG      = ['agg.traj', 'agg.tables', 'agg.sig']
# Run scope: the whole run root, across cells.  These answer the questions the pages keep
# ASSERTING rather than showing — how broadly a comparison holds, what a rule costs to
# run, what the rules and the inventory model actually were, and what was held fixed.
_RUN      = ['cost.compute', 'cost.rollup', 'dossier.index', 'tables.census',
             'catalog.rules', 'catalog.inventory', 'catalog.fixed']

#: The two stats flavours, mutually exclusive per preset.  Not in the groups above because
#: no preset runs both: `_keys` picks one.
_STATS = {'assignment': ['tables.stats', 'sig.suite'],
          'initial': ['tables.by_initial', 'sig.by_initial']}

_GROUPS = (_TABLES, _HEADLINE, _TRENDS, _DETAIL, _AGG, _RUN)


def check_groups() -> None:
    """Every registered evaluation is named by exactly one group or one stats flavour.

    Called from the PACKAGE `__init__`, immediately after `import_all()` — not at this
    module's own import, because `presets` is itself one of the modules that walk
    discovers, so at that moment the registry holds whatever happens to have been imported
    alphabetically before it.  Calling it there and not from a test is deliberate: a
    newly registered evaluation must not be able to reach a run without someone deciding
    where it belongs, and the cost of finding out otherwise lands 21 minutes later as a
    figure nobody notices is absent.

    The message names the key and lists the groups, because "add it somewhere" is not
    actionable and "which of these six" is.
    """
    from Optimization.Performance_Evaluations.core.registry import EVALUATIONS
    named: list = [k for g in _GROUPS for k in g]
    named += [k for v in _STATS.values() for k in v]
    dupes = sorted({k for k in named if named.count(k) > 1})
    if dupes:
        raise AssertionError(f'preset groups name {dupes} more than once; a key belongs '
                             f'to exactly one group')
    registered = {ev.key for ev in EVALUATIONS}
    unknown = sorted(set(named) - registered)
    if unknown:
        raise AssertionError(f'preset groups name {unknown}, which no module registers — '
                             f'a renamed or deleted evaluation left this list behind')
    missing = sorted(registered - set(named))
    if missing:
        raise AssertionError(
            f'{missing} are registered and named by no preset group, so they would render '
            f'nothing and say nothing about it. Add each to whichever group fits: _TABLES '
            f'(the tidy CSV surface), _HEADLINE, _TRENDS, _DETAIL, _AGG (cross-profile), '
            f'or _RUN (the whole run root).')


def _keys(stats):
    """stats: 'assignment' (flat suite) | 'initial' (uni-vs-opt per fn) | None."""
    base = [k for g in _GROUPS for k in g]
    return base + list(_STATS.get(stats, ()))


# Every top-N graph must select the SAME arms — the site pairs the headline table with
# the labor/throughput figures, so a divergent selection shows a subset and reads as a
# contradiction.  One helper, applied to every selecting key, keeps the invariant.
_TOP_KEYS = ('labor.delta_topn', 'labor.per_batch', 'throughput.volume')


def _top(n, by, *extra):
    return {k: {'top_n': n, 'top_by': by} for k in (*_TOP_KEYS, *extra)}


# The aggregate stats pair defaults to the by-initial fork (the CLI-default preset);
# DEFAULT flips them to the flat suite so both stages agree on the stats flavour.
_AGG_FLAT = {'agg.tables': {'by_initial': False}, 'agg.sig': {'by_initial': False}}


PRESETS = {
    # headline.top_vs_baseline keeps its own default selection here: a top_n=1 override
    # would cut the summary table to a single row.
    'DEFAULT':    {'keys': _keys('assignment'),
                   'overrides': _top(1, 'global') | _AGG_FLAT, 'focus': 'uni'},
    'NO_STATS':   {'keys': _keys(None),
                   'overrides': _top(1, 'global') | _AGG_FLAT, 'focus': 'uni'},
    'BY_INITIAL': {'keys': _keys('initial'),
                   'overrides': _top(3, 'initial', 'headline.top_vs_baseline'),
                   'focus': 'all'},
}
