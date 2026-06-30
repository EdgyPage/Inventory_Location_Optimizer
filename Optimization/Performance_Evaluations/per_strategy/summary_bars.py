"""per_strategy.summary_bars — single rectangular summary: mean batch duration, mean
throughput, mean Sigma f*D efficiency, side by side across strategies.  Writes
per_strategy/summary.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle


@evaluation(key='per_strategy.summary_bars', label='Strategy summary bars',
            scope='per_strategy', needs=('batch',), out_subdir='per_strategy')
def render(ctx, params):
    strategies = ctx.strategies
    n = len(strategies)
    optimal = ctx.optimal
    ps_dir = os.path.join(ctx.run_dir, 'per_strategy')

    ylabels = [_stitle(s) for s in strategies]
    yc      = [s['color'] for s in strategies]
    ypos    = np.arange(n)
    mean_dur = [ctx.batch_df(s['key'])['duration'].mean()
                if not ctx.batch_df(s['key']).empty else 0.0 for s in strategies]
    mean_thr = [ctx.batch_df(s['key'])['completion_rate'].mean()
                if not ctx.batch_df(s['key']).empty else 0.0 for s in strategies]

    def _mean_eff(s):
        df = ctx.batch_df(s['key'])
        if df.empty or optimal <= 0:
            return 0.0
        return float((optimal / df['sigma_fd'].clip(lower=1e-9) * 100.0).mean())
    mean_eff = [_mean_eff(s) for s in strategies]

    fig, (b1, b2, b3) = plt.subplots(1, 3, figsize=(16, max(6.0, n * 0.24)), sharey=True)
    fig.suptitle(f'Strategy summary  [{ctx.title}]  (n={n}, baseline {_stitle(strategies[0])})',
                 fontsize=13, fontweight='bold')
    b1.barh(ypos, mean_dur, color=yc); b1.set_title('Mean batch duration (lower better)', fontsize=10)
    b1.set_yticks(ypos); b1.set_yticklabels(ylabels, fontsize=6); b1.invert_yaxis(); b1.grid(axis='x', alpha=0.3)
    b2.barh(ypos, mean_thr, color=yc); b2.set_title('Mean throughput (higher better)', fontsize=10)
    b2.grid(axis='x', alpha=0.3)
    b3.barh(ypos, mean_eff, color=yc); b3.set_title('Mean Sigma f*D efficiency % (higher better)', fontsize=10)
    b3.grid(axis='x', alpha=0.3)
    plt.tight_layout(rect=(0, 0, 1, 0.98))
    _save_close(fig, os.path.join(ps_dir, 'summary.png'))
