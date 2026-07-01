"""config.summary_csv — per-strategy mean/median/std summary tables for headline batch
and task metrics.  Writes per_strategy/summary_batch.csv and per_strategy/summary_task.csv."""
import os

import pandas as pd

from Performance_Evaluations.core.registry import evaluation


@evaluation(key='config.summary_csv', label='Summary batch/task CSVs',
            scope='config', needs=('batch', 'task'), out_subdir='per_strategy')
def render(ctx, params):
    strategies = ctx.strategies
    labels = [s['label'] for s in strategies]
    bcols = ['duration', 'completion_rate', 'avg_concurrent_pickers', 'picking_pct', 'traveling_pct']
    tcols = ['duration', 'W', 'lift_sum', 'num_bins']
    summ_b = pd.concat([ctx.batch_df(s['key'])[bcols].agg(['mean', 'median', 'std']).T
                        for s in strategies], axis=1, keys=labels).round(3)
    summ_t = pd.concat([ctx.task_df(s['key'])[tcols].agg(['mean', 'median', 'std']).T
                        for s in strategies], axis=1, keys=labels).round(3)
    ps_dir = os.path.join(ctx.run_dir, 'per_strategy')
    summ_b.to_csv(os.path.join(ps_dir, 'summary_batch.csv'))
    summ_t.to_csv(os.path.join(ps_dir, 'summary_task.csv'))
    ctx.log.info(f'\n{summ_b.to_string()}\n')
    ctx.log.info(f'\n{summ_t.to_string()}\n')
