"""tables.per_run — the two per-run CSV data products, ported verbatim from the retired
per-strategy report module (schemas unchanged; the bar charts died in the redesign — the
significance and headline families own those comparisons now).

Two documents:

  * the raw long-format per-batch table — every batch × strategy with all headline
    metrics, written to the LEAF ROOT so external analysis (and the docs ingest) finds
    it beside the series document, exactly where it has always lived;
  * the per-run rollup — one row per strategy (production-time-first, with the
    gaming-resistant per-item variant, the put-away queue honesty columns, and the
    strategy's published color hex so charts and tables agree), written into the flat
    tables dir the registration declares.
"""
import os

import pandas as pd

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io


# ── the data build (verbatim schemas from the retired report module) ────────────────
def _per_run_rows(strategies, df_b, df_t, title):
    """(long_rows, summary_rows): the per-(strategy, batch) long records and the
    per-strategy rollup records.  Column names and dtypes are the OLD schemas —
    downstream consumers (docs ingest, notebooks) key on them."""
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
    return rows, summ


@evaluation(key='tables.per_run', label='Per-run rollup + long per-batch CSV',
            scope='per_strategy', needs=('batch', 'task'), out_subdir='tables')
def render(ctx, params):
    rows, summ = _per_run_rows(ctx.strategies, ctx.batch_frames(), ctx.task_frames(),
                               ctx.title)
    # The long table lands at the LEAF ROOT (beside the series document), as always —
    # io.out_dir is only for the declared tables dir.
    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(ctx.run_dir, 'batches_long.csv'),
                                  index=False)
    if summ:
        pd.DataFrame(summ).to_csv(os.path.join(io.out_dir(ctx), 'per_run_summary.csv'),
                                  index=False)
    ctx.log.info(f'  per-run tables -> {ctx.run_dir} '
                 f'({len(rows)} batch rows, {len(summ)} strategies)')
