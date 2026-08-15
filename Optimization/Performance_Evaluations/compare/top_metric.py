"""compare.top_metric — overlay only the top-N strategies (global, or top-N within each
value of a dimension) against the baseline, per over-time metric.  Under compare/top/.

Params: top_n (int), top_by ('global' | 'initial' | 'assignment' | 'reslot')."""
import os

import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.painters import _top_metric
from Optimization.Performance_Evaluations.common.style import _stitle, _LINESTYLES, _TOP_DIMS, legend_right
from Optimization.Performance_Evaluations.common.series import _select_top
from Optimization.Performance_Evaluations.compare import overtime_metrics, top_tag


@evaluation(key='compare.top_metric', label='Top-N strategies vs baseline, over time',
            scope='config', needs=('series',), out_subdir='compare/top',
            defaults={'top_n': 1, 'top_by': 'global'})
def render(ctx, params):
    S = ctx.series()
    top_n  = int(params.get('top_n', 1) or 1)
    top_by = params.get('top_by', 'global') or 'global'
    out = io.out_dir(ctx)                       # compare/top, from the declaration
    tag = top_tag(top_n, top_by)
    for m in overtime_metrics(agg=False):
        ttl = f"{m['t']}  [{ctx.title}]"
        _top_metric(ctx.strategies, S, top_n, m, ttl, ctx.base,
                    os.path.join(out, f"{tag}_{m['f']}.png"), top_by=top_by)
