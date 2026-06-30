"""Presets — the manifest that decides which graphs run and with what params.

A preset bundles: `keys` (the enabled evaluation keys, in run order), `overrides`
(per-graph param overrides keyed by evaluation key), and `focus` (a context-level
strategy filter, 'uni' | 'opt' | 'all').  Toggle a graph by adding/removing its key;
tune a graph via its `overrides` entry — no central argparse to grow.

The old CLI combinations map to presets:
  --focus uni  (default)            → DEFAULT
  --no-stats                        → NO_STATS
  --compare initial                 → BY_INITIAL  (focus='all', uni-vs-opt per fn)
  coverage_e2e's top_n=2/top_by=initial → E2E_PARITY (used only for migration parity)
"""

_PER_STRATEGY = ['per_strategy.report_bars', 'per_strategy.metric_grids',
                 'per_strategy.scorecards', 'per_strategy.summary_bars']
_CONFIG = ['config.summary_csv', 'config.series']
_COMPARE = ['compare.faceted', 'compare.overlay', 'compare.top_metric',
            'compare.top_vs_baseline', 'compare.pick_vs_travel', 'compare.delta_bars',
            'compare.task_box', 'breakdown.travel_handling']
_AGG = ['agg.cross_profile']


def _keys(stats):
    """stats: 'assignment' (stats.suite) | 'initial' (stats.by_initial) | None."""
    base = _PER_STRATEGY + _CONFIG + _COMPARE + _AGG
    if stats == 'assignment':
        return base + ['stats.suite', 'agg.stats']
    if stats == 'initial':
        return base + ['stats.by_initial', 'agg.stats_by_initial']
    return base


_GLOBAL_TOP = {'compare.top_metric': {'top_n': 1, 'top_by': 'global'},
               'agg.cross_profile': {'top_n': 1, 'top_by': 'global'}}
_INITIAL_TOP = {'compare.top_metric': {'top_n': 3, 'top_by': 'initial'},
                'agg.cross_profile': {'top_n': 3, 'top_by': 'initial'}}
_E2E_TOP = {'compare.top_metric': {'top_n': 2, 'top_by': 'initial'},
            'agg.cross_profile': {'top_n': 2, 'top_by': 'initial'}}


PRESETS = {
    'DEFAULT':    {'keys': _keys('assignment'), 'overrides': _GLOBAL_TOP,  'focus': 'uni'},
    'NO_STATS':   {'keys': _keys(None),         'overrides': _GLOBAL_TOP,  'focus': 'uni'},
    'BY_INITIAL': {'keys': _keys('initial'),    'overrides': _INITIAL_TOP, 'focus': 'all'},
    # Reproduces coverage_e2e.py's old run_analysis(top_n=2, top_by='initial') call so the
    # registry pipeline can be diffed byte-for-byte against the monolith during migration.
    'E2E_PARITY': {'keys': _keys('assignment'), 'overrides': _E2E_TOP,     'focus': 'uni'},
}
