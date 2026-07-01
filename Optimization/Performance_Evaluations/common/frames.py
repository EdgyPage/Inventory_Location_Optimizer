"""DataFrame builders + steady-state series alignment (verbatim from the monoliths).

`_bdf`/`_tdf` turn the namedtuple stat lists from Picking_Data into the wide per-batch /
per-task frames every graph consumes; `_roll` is the rolling-mean helper; `_metric_series`
and `_aligned` build the paired matrices the stats suite tests.
"""
import numpy as np
import pandas as pd


def _bdf(stats):
    return pd.DataFrame([{
        'batch_id'              : s.batch_id,
        'duration'              : s.duration,
        'num_tasks'             : s.num_tasks,
        'total_items'           : s.total_items,
        'completion_rate'       : s.total_items / s.duration if s.duration > 0 else 0.0,
        'avg_concurrent_pickers': s.avg_concurrent_pickers,
        'picking_pct'           : s.picking_pct   * 100,
        'traveling_pct'         : s.traveling_pct * 100,
        'sigma_fd'              : s.sigma_fd,
        'reload_moves'          : s.reload_moves,
        'reorder_placements'    : s.reorder_placements,
        'queue_depth'           : getattr(s, 'queue_depth', 0),
        'lead_queue_depth'      : getattr(s, 'lead_queue_depth', 0),
        'in_transit_qty'        : getattr(s, 'in_transit_qty', 0),
    } for s in stats])


def _tdf(stats, aisle_unittype_map, aisle_handling_map):
    return pd.DataFrame([{
        'batch_id'   : s.batch_id,
        'aisle_id'   : s.aisle_id,
        'duration'   : s.duration,
        'W'        : s.W,
        'lift_sum'   : s.lift_sum,
        'num_bins'   : s.num_bins_visited,
        'total_items': s.total_items,
        'unit_type'  : aisle_unittype_map.get(s.aisle_id),
        'handling'   : aisle_handling_map.get(s.aisle_id),
    } for s in stats])


def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


def _metric_series(df_b_k, df_t_k, source, col, ss_lo):
    """Per-batch steady-state Series (indexed by batch_id) for one strategy/metric."""
    if source == 'batch':
        d = df_b_k[df_b_k['batch_id'] >= ss_lo]
        if d.empty or col not in d:
            return pd.Series(dtype=float)
        return d.set_index('batch_id')[col]
    d = df_t_k[df_t_k['batch_id'] >= ss_lo]
    if d.empty or col not in d:
        return pd.Series(dtype=float)
    g = d.groupby('batch_id')[col]
    return g.mean() if source == 'task_mean' else g.sum()


def _aligned(series_by_key, keys):
    """Intersect batch indices common to all strategies → paired matrix (n × k)."""
    idxs = [set(series_by_key[k].index) for k in keys if len(series_by_key[k])]
    if len(idxs) != len(keys) or not idxs:
        return None
    common = sorted(set.intersection(*idxs))
    if len(common) < 3:
        return None
    return np.column_stack([series_by_key[k].loc[common].values for k in keys])
