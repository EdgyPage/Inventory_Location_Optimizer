"""tables.tidy — tidy long-format replacements for the retired 103-column MultiIndex
summary pivots.

The old pair of wide tables put every (strategy × statistic) in its own column, which no
downstream tool could consume without bespoke unpivoting.  These two documents are the
tidy re-statement of the same data:

  * the batch table — one row per (strategy, batch_id, metric) over every numeric
    batch-frame column, carrying the strategy's identity split (initial / assignment /
    reslot) and the upstream Tukey outlier flag so a consumer can filter honestly;
  * the task table — one row per (strategy, batch_id) with the task count, the
    mean/median/summed task duration, and the share of tasks flagged as outliers.

Values stay in raw sim units on purpose: these are DATA products for downstream
computation, not presentation surfaces — unit policy is applied where a figure is drawn.
"""
import os

import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io

#: batch-frame columns that are identity/bookkeeping, not metrics.
#: `work_day` is the day a batch was RELEASED into -- a grouping label, and a metric row
#: of "day 17" would be a number with no direction.
_NON_METRIC = ('batch_id', 'is_outlier', 'work_day')


def _batch_long(strategies, df_b):
    """One row per (strategy, batch_id, metric) over the numeric batch-frame columns."""
    frames = []
    for s in strategies:
        b = df_b.get(s['key'])
        if b is None or b.empty:
            continue
        metrics = [c for c in b.columns
                   if c not in _NON_METRIC and pd.api.types.is_numeric_dtype(b[c])]
        m = b.melt(id_vars=list(_NON_METRIC), value_vars=metrics,
                   var_name='metric', value_name='value')
        m['strategy']   = s['key']
        m['initial']    = s.get('initial', '')
        m['assignment'] = s.get('assignment', '')
        m['reslot']     = s.get('reslot', '')
        frames.append(m)
    cols = ['strategy', 'initial', 'assignment', 'reslot',
            'batch_id', 'metric', 'value', 'is_outlier']
    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)[cols]


def _task_long(strategies, df_t):
    """One row per (strategy, batch_id): task count, duration stats, outlier share."""
    rows = []
    for s in strategies:
        t = df_t.get(s['key'])
        if t is None or t.empty:
            continue
        g = t.groupby('batch_id')
        agg = g['duration'].agg(n_tasks='size', duration_mean='mean',
                                duration_median='median', duration_sum='sum')
        agg['outlier_share'] = g['is_outlier'].mean()
        agg = agg.reset_index()
        agg.insert(0, 'strategy', s['key'])
        rows.append(agg)
    cols = ['strategy', 'batch_id', 'n_tasks', 'duration_mean', 'duration_median',
            'duration_sum', 'outlier_share']
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.concat(rows, ignore_index=True)[cols]


@evaluation(key='tables.tidy', label='Tidy long-format batch/task tables',
            scope='config', needs=('batch', 'task'), out_subdir='tables')
def render(ctx, params):
    out = io.out_dir(ctx)
    bl = _batch_long(ctx.strategies, ctx.batch_frames())
    tl = _task_long(ctx.strategies, ctx.task_frames())
    bl.to_csv(os.path.join(out, 'batch_metrics.csv'), index=False)
    tl.to_csv(os.path.join(out, 'task_metrics.csv'), index=False)
    ctx.log.info(f'  tidy tables -> {out} ({len(bl)} batch-metric rows, '
                 f'{len(tl)} task rows)')
