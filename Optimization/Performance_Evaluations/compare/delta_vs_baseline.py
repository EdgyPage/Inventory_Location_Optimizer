"""compare.delta_vs_baseline — throughput Δ% and duration-improvement% vs the baseline strategy.
Writes compare/breakdown/delta_vs_baseline.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.painters import _delta_bars
from Optimization.Performance_Evaluations.common.style import _stitle, _pct_delta


@evaluation(key='compare.delta_vs_baseline', label='Δ vs baseline (throughput/duration)',
            scope='config', needs=('series',), out_subdir='compare/breakdown')
def render(ctx, params):
    out = io.out_dir(ctx)                       # compare/breakdown, from the declaration
    _delta_bars(ctx.strategies, ctx.series(), ctx.base,
                f'Δ vs baseline  [{ctx.title}]',
                os.path.join(out, 'delta_vs_baseline.png'))
