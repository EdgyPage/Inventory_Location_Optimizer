"""config.series — write series.json (per-strategy trajectories + steady-state scalars +
optimal floors).  The cross-profile aggregate stage consumes these files.

Ordered FIRST in the preset so it lands before downstream readers, but every config graph
that needs the curves calls ctx.series() directly (so there is no hard ordering)."""
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common.series import _dump_series


@evaluation(key='config.series', label='series.json (trajectories + ss scalars)',
            scope='config', needs=('series',), out_subdir='')
def render(ctx, params):
    # The optimal floors come off the CONTEXT (ctx.optimal_work / ctx.optimal), not the raw
    # sim_result job dict — the context already derives them once for every consumer, and
    # renders never reach past the facade.
    _dump_series(
        ctx.strategies, ctx.series(),
        os.path.join(ctx.run_dir, 'series.json'),
        extra={'optimal_work': ctx.optimal_work,
               'optimal_sigma_fd': ctx.optimal})
