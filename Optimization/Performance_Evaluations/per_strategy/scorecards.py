"""per_strategy.scorecards — one 3-panel image per strategy (batch duration · Sigma f*D ·
churn).  Writes strat_<key>.png under per_strategy/."""
import os

import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.per_strategy.panels import panel_duration, panel_eff, panel_churn


@evaluation(key='per_strategy.scorecards', label='Per-strategy scorecards',
            scope='per_strategy', needs=('batch',), out_subdir='per_strategy')
def render(ctx, params):
    ps_dir = os.path.join(ctx.run_dir, 'per_strategy')
    optimal = ctx.optimal
    for s in ctx.strategies:
        fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 3.4))
        fig.suptitle(ctx.full_title(s), fontsize=12, fontweight='bold')
        panel_duration(ctx, a1, s); a1.set_title('Batch duration', fontsize=10)
        a1.set_xlabel('batch'); a1.grid(alpha=0.3)
        panel_eff(ctx, a2, s)
        a2.set_title('total f*D ' + ('% of optimal' if optimal > 0 else '(raw)'), fontsize=10)
        a2.set_xlabel('batch'); a2.grid(alpha=0.3)
        panel_churn(ctx, a3, s); a3.set_title('Churn (% bins/batch)', fontsize=10)
        a3.set_xlabel('batch'); a3.grid(alpha=0.3)
        plt.tight_layout(rect=(0, 0, 1, 0.92))
        _save_close(fig, os.path.join(ps_dir, f"strat_{s['key']}.png"))
