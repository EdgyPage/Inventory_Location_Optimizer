"""compare.pick_vs_travel — horizontal stacked picking%/traveling% bar per strategy
(steady-state aggregate picker-time split).  Writes compare/breakdown/pick_vs_travel.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _save_close
from Optimization.Performance_Evaluations.common.painters import _pick_travel_bars
from Optimization.Performance_Evaluations.common.style import _stitle, legend_right


@evaluation(key='compare.pick_vs_travel', label='Picking vs traveling split',
            scope='config', needs=('series',), out_subdir='compare/breakdown')
def render(ctx, params):
    out = io.out_dir(ctx)                       # compare/breakdown, from the declaration
    _pick_travel_bars(ctx.strategies, ctx.series(),
                      f'Pick vs travel  [{ctx.title}]',
                      os.path.join(out, 'pick_vs_travel.png'))
