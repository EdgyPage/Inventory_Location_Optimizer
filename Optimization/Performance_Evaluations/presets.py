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

_TABLES   = ['config.series', 'tables.per_run', 'tables.tidy']
_HEADLINE = ['headline.top_vs_baseline', 'headline.all_arms', 'headline.rollup',
             'headline.throughput_vs_labor']
_TRENDS   = ['trajectories.overtime', 'labor.delta_topn', 'labor.per_batch',
             'labor.delta_grid', 'throughput.volume']
_DETAIL   = ['task_time.duration', 'task_time.breakdown', 'layout.churn',
             'diagnostics.metric_grids', 'diagnostics.scorecards']
_AGG      = ['agg.traj', 'agg.tables', 'agg.sig']


def _keys(stats):
    """stats: 'assignment' (flat suite) | 'initial' (uni-vs-opt per fn) | None."""
    base = _TABLES + _HEADLINE + _TRENDS + _DETAIL + _AGG
    if stats == 'assignment':
        return base + ['tables.stats', 'sig.suite']
    if stats == 'initial':
        return base + ['tables.by_initial', 'sig.by_initial']
    return base


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
