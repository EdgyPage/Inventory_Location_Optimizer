"""compare.faceted — over-time trajectories faceted by (initial × reslot), colored by
assignment function.  One PNG per over-time metric under compare/faceted/."""
import os

from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.painters import _facet_metric
from Optimization.Performance_Evaluations.common.style import _assign_color_map
from Optimization.Performance_Evaluations.compare import overtime_metrics


@evaluation(key='compare.faceted', label='Over-time, faceted by initial×reslot',
            scope='config', needs=('series',), out_subdir='compare/faceted')
def render(ctx, params):
    S = ctx.series()
    out = io.out_dir(ctx)                       # compare/faceted, from the declaration
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _facet_metric(ctx.strategies, S, m, ttl, os.path.join(out, m['f'] + '.png'))
