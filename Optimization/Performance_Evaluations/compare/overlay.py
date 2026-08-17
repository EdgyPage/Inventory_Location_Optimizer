"""compare.overlay — all strategies overlaid on one axis per over-time metric: color =
assignment function, linestyle = (initial, reslot).  One PNG per metric under compare/overlay/."""
import os

from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.painters import _overlay_metric
from Optimization.Performance_Evaluations.common.style import _assign_color_map, _ir_style_map, legend_right
from Optimization.Performance_Evaluations.compare import overtime_metrics


@evaluation(key='compare.overlay', label='Over-time, all strategies overlaid',
            scope='config', needs=('series',), out_subdir='compare/overlay')
def render(ctx, params):
    S = ctx.series()
    out = io.out_dir(ctx)                       # compare/overlay, from the declaration
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _overlay_metric(ctx.strategies, S, m, ttl, os.path.join(out, m['f'] + '.png'))
