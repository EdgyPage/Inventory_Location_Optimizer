"""compare.delta_bars — throughput Δ% and duration-improvement% vs the baseline strategy.
Writes compare/breakdown/delta_vs_baseline.png."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _stitle, _pct_delta


def _delta_bars(strategies, S, baseline, title, path):
    avail = [s for s in strategies if S.get(s['key'])]
    base  = S.get(baseline['key'])
    if base is None:
        return
    bt, bd = base['ss_thr'], base['ss_dur']
    ypos = np.arange(len(avail))
    dthr = [_pct_delta(S[s['key']]['ss_thr'], bt) for s in avail]            # ↑ better
    ddur = [_pct_delta(bd, S[s['key']]['ss_dur']) for s in avail]            # ↑ better (improvement)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, max(6.0, len(avail) * 0.3)), sharey=True)
    a1.barh(ypos, dthr, color=['#55a868' if v >= 0 else '#c44e52' for v in dthr])
    a1.set_title('Throughput Δ% vs baseline (↑ better)', fontsize=10)
    a1.set_yticks(ypos)
    a1.set_yticklabels([_stitle(s) for s in avail], fontsize=6)
    a1.invert_yaxis()
    a1.axvline(0, color='k', lw=0.8)
    a1.grid(axis='x', alpha=0.3)
    a2.barh(ypos, ddur, color=['#55a868' if v >= 0 else '#c44e52' for v in ddur])
    a2.set_title('Duration improvement % vs baseline (↑ better)', fontsize=10)
    a2.axvline(0, color='k', lw=0.8)
    a2.grid(axis='x', alpha=0.3)
    fig.suptitle(title, fontsize=12, fontweight='bold')
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    _save_close(fig, path)


@evaluation(key='compare.delta_bars', label='Δ vs baseline (throughput/duration)',
            scope='config', needs=('series',), out_subdir='compare/breakdown')
def render(ctx, params):
    out = os.path.join(ctx.run_dir, 'compare', 'breakdown')
    os.makedirs(out, exist_ok=True)
    _delta_bars(ctx.strategies, ctx.series(), ctx.base,
                f'Δ vs baseline  [{ctx.title}]',
                os.path.join(out, 'delta_vs_baseline.png'))
