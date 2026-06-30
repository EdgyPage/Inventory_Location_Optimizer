"""config.series — write series.json (per-strategy trajectories + steady-state scalars +
optimal floors).  The cross-profile aggregate stage consumes these files.

Ordered FIRST in the preset so it lands before downstream readers, but every config graph
that needs the curves calls ctx.series() directly (so there is no hard ordering)."""
import os

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.series import _dump_series


@evaluation(key='config.series', label='series.json (trajectories + ss scalars)',
            scope='config', needs=('batch', 'task'), out_subdir='')
def render(ctx, params):
    _dump_series(
        ctx.strategies, ctx.series(),
        os.path.join(ctx.run_dir, 'series.json'),
        extra={'optimal_work': float(ctx.sim_result.get('optimal_work') or 0.0),
               'optimal_sigma_fd': float(ctx.sim_result.get('optimal_sigma_fd') or 0.0)})
