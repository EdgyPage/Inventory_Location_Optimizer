"""per_strategy.report_bars — production-time-first per-run rollups + the raw long-format
per-batch CSV.  Writes batches_long.csv (run_dir root), per_run_summary.csv (per_strategy/),
and bar charts ranking strategies by production time, production-time-per-item, completion
rate, and put-away queue depth."""
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from Performance_Evaluations.core.registry import evaluation
from Performance_Evaluations.common.io import _save_close


def _per_run_report(strategies, df_b, df_t, run_dir, ps_dir, title, log):
    """Production-time-first per-run rollups + a raw long-format per-batch CSV.

    Writes batches_long.csv (every batch × strategy, all metrics) for external analysis,
    a per_run_summary.csv, and bar charts ranking strategies by total production time,
    production-time-per-item (gaming-resistant), completion rate, and put-away queue depth
    (the honesty metric — a strategy that defers placement carries a standing queue)."""
    rows, summ = [], []
    for s in strategies:
        k = s['key']
        b = df_b.get(k)
        t = df_t.get(k)
        if b is None or b.empty:
            continue
        bb = b.set_index('batch_id').sort_index()
        prod = (t.groupby('batch_id')['duration'].sum().reindex(bb.index).fillna(0.0)
                if t is not None and not t.empty
                else pd.Series(0.0, index=bb.index))
        for bid in bb.index:
            r = bb.loc[bid]
            rows.append(dict(
                profile=title, strategy=k, initial=s.get('initial', ''),
                restock=s.get('assignment', ''), batch_id=int(bid),
                production_time=float(prod.loc[bid]),
                makespan=float(r['duration']),
                completion_rate=float(r['completion_rate']),
                queue_depth=int(r.get('queue_depth', 0)),
                lead_queue_depth=int(r.get('lead_queue_depth', 0)),
                in_transit_qty=int(r.get('in_transit_qty', 0)),
                sigma_fd=float(r['sigma_fd']),
                reorder_placements=int(r['reorder_placements']),
                reload_moves=int(r['reload_moves']),
                picking_pct=float(r['picking_pct']),
                avg_concurrent_pickers=float(r['avg_concurrent_pickers']),
                num_tasks=int(r['num_tasks']),
                total_items=int(r['total_items']),
            ))
        tot_prod = float(prod.sum())
        tot_items = float(bb['total_items'].sum())
        qd = bb['queue_depth'] if 'queue_depth' in bb else pd.Series(0.0, index=bb.index)
        it = bb['in_transit_qty'] if 'in_transit_qty' in bb else pd.Series(0.0, index=bb.index)
        summ.append(dict(
            strategy=k, label=s['label'], color=s.get('color', '#888888'),
            total_production_time=tot_prod,
            production_time_per_item=(tot_prod / tot_items if tot_items else float('nan')),
            mean_completion_rate=float(bb['completion_rate'].mean()),
            mean_queue_depth=float(qd.mean()), max_queue_depth=float(qd.max()),
            mean_in_transit=float(it.mean()),
        ))

    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(run_dir, 'batches_long.csv'), index=False)
    sdf = pd.DataFrame(summ)
    if sdf.empty:
        return sdf
    sdf.to_csv(os.path.join(ps_dir, 'per_run_summary.csv'), index=False)

    def _bar(col, ylabel, fname, ascending=True):
        d = sdf.dropna(subset=[col]).sort_values(col, ascending=ascending)
        if d.empty:
            return
        fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(d)), 4.5))
        ax.bar(range(len(d)), d[col].values, color=list(d['color']), alpha=0.85)
        ax.set_xticks(range(len(d)))
        ax.set_xticklabels(list(d['label']), rotation=40, ha='right', fontsize=8)
        ax.set_ylabel(ylabel)
        ax.grid(axis='y', alpha=0.3)
        ax.set_title(f'{ylabel} per run  [{title}]', fontsize=11, fontweight='bold')
        _save_close(fig, os.path.join(ps_dir, fname))

    _bar('total_production_time', 'total production time (sim units)',
         'production_time_per_run.png', ascending=True)
    _bar('production_time_per_item', 'production time per item (sim units)',
         'production_time_per_item.png', ascending=True)
    _bar('mean_completion_rate', 'mean completion rate (items / sim-time)',
         'completion_rate_per_run.png', ascending=False)
    if float(sdf['max_queue_depth'].max() or 0) > 0:
        _bar('mean_queue_depth', 'mean put-away queue depth (units)',
             'queue_depth_per_run.png', ascending=True)
    log.info(f'  per-run report → {ps_dir} (+ batches_long.csv)')
    return sdf


@evaluation(key='per_strategy.report_bars', label='Per-run rollup bars + batches_long.csv',
            scope='per_strategy', needs=('batch', 'task'), out_subdir='per_strategy')
def render(ctx, params):
    ps_dir = os.path.join(ctx.run_dir, 'per_strategy')
    _per_run_report(ctx.strategies, ctx.batch_frames(), ctx.task_frames(),
                    ctx.run_dir, ps_dir, ctx.title, ctx.log)
