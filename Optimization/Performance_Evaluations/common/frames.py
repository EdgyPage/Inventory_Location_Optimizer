"""DataFrame builders + steady-state series alignment (verbatim from the monoliths).

`_bdf`/`_tdf` turn the namedtuple stat lists from Picking_Data into the wide per-batch /
per-task frames every graph consumes; `_roll` is the rolling-mean helper; `_metric_series`
and `_aligned` build the paired matrices the stats suite tests.
"""
import numpy as np
import pandas as pd


def _bdf(stats):
    df = pd.DataFrame([{
        'batch_id'              : s.batch_id,
        'duration'              : s.duration,        # batch makespan (parallel wall-clock)
        'num_tasks'             : s.num_tasks,
        'total_items'           : s.total_items,
        # throughput / batch makespan (metric d) — kept as `completion_rate` for the many existing
        # consumers, and mirrored as `thr_batch` for the four-metric vocabulary.
        # NaN, not 0.0, when there is no makespan to divide by — the same rule `thr_task`
        # below already applies, and for the same reason: a batch that did no work is
        # UNMEASURED, and counting it as zero throughput drags every mean toward zero. This
        # was 0.0 while no batch could have a zero duration; a skipped batch now writes a
        # row, so the distinction became reachable.
        'completion_rate'       : s.total_items / s.duration if s.duration > 0 else np.nan,
        'thr_batch'             : s.total_items / s.duration if s.duration > 0 else np.nan,
        # task makespan (metric a) = Σ task time = total labor; throughput / task makespan (metric c).
        # Both NaN (not 0) when task_makespan is unavailable (legacy DBs) so they are excluded from
        # summaries/stats, not counted as zero labor / zero throughput.
        'task_makespan'         : (getattr(s, 'task_makespan', 0.0)
                                   if getattr(s, 'task_makespan', 0.0) > 0 else np.nan),
        'thr_task'              : (s.total_items / getattr(s, 'task_makespan', 0.0)
                                   if getattr(s, 'task_makespan', 0.0) > 0 else np.nan),
        'avg_concurrent_pickers': s.avg_concurrent_pickers,
        'picking_pct'           : s.picking_pct   * 100,
        'traveling_pct'         : s.traveling_pct * 100,
        'sigma_fd'              : s.sigma_fd,
        'reload_moves'          : s.reload_moves,
        'reorder_placements'    : s.reorder_placements,
        'queue_depth'           : getattr(s, 'queue_depth', 0),
        # The receiving dock, guarded exactly like `queue_depth` above so a pre-dock vintage
        # reads 0 rather than raising.  `recv_depth` is the DISJOINT other half of the
        # unbinned backlog -- a unit is on the dock or past it, never both -- so a reader
        # after the whole backlog sums the two.
        'recv_depth'            : getattr(s, 'recv_depth', 0),
        'recv_unloaded'         : getattr(s, 'recv_unloaded', 0),
        'recv_cut'              : getattr(s, 'recv_cut', 0),
        'recv_seconds'          : getattr(s, 'recv_seconds', 0.0),
        'lead_queue_depth'      : getattr(s, 'lead_queue_depth', 0),
        'in_transit_qty'        : getattr(s, 'in_transit_qty', 0),
        # upstream Tukey outlier flag, carried through so downstream tables can filter
        # or report it (0 for legacy rows that predate the flag).
        'is_outlier'            : getattr(s, 'is_outlier', 0),
        # The batch's own epoch on the arm's absolute axis.  Needed here (and nowhere else
        # in this frame) to measure the gap BETWEEN batches -- see `_elapsed` below.
        'batch_start_time'      : getattr(s, 'batch_start_time', 0.0),
    } for s in stats])
    return _elapsed(df)


def _elapsed(df):
    """Add `elapsed` and `thr_elapsed`: throughput against the DAY, not the makespan.

    `duration` is the batch MAKESPAN -- first picker starting to last finishing.  Under the
    continuous default that is also the whole elapsed time, because the next batch is
    released the instant this one ends, so "how fast did the crew work" and "how much did
    the day produce" have one answer and nothing had to tell them apart.

    A PACED schedule separates them.  `ReleaseSchedule` releases batch i at its slot, and a
    crew that finishes early waits.  That gap is real elapsed time in which nothing was
    picked, and `duration` does not contain it -- so a paced run's `thr_batch` is the rate
    the crew worked AT while being read as the rate the day DELIVERED.  `thr_elapsed` is the
    second number, named so the two cannot be confused.

    Both are legitimate and a scheduling change moves them in OPPOSITE directions: fewer,
    fuller waves raise what the day produces while leaving the working rate alone.  So this
    is an addition, never a replacement.

    Byte-identical under the default: the runner sets `arm_clock = batch_start_time +
    duration` and then `release_at`, which returns the clock unchanged with no cadence
    configured -- so consecutive epochs differ by exactly `duration` and `thr_elapsed ==
    thr_batch` to the last bit.

    The LAST batch has no successor to measure against and falls back to its own makespan:
    there is no gap after the final wave, because nothing was waiting for it.
    """
    if df.empty:
        df['elapsed'] = df['thr_elapsed'] = []
        return df
    order = df['batch_id'].argsort().values          # DB order is not guaranteed sorted
    starts = df['batch_start_time'].values[order]
    dur = df['duration'].values[order]
    gaps = np.empty(len(df), dtype=float)
    gaps[:-1] = starts[1:] - starts[:-1]
    gaps[-1] = dur[-1]
    # A NEGATIVE gap means the epochs are not monotonic, which a legacy DB (every row
    # stamped 0.0 before the absolute clock landed) produces for every batch.  Fall back to
    # the makespan there rather than emitting a negative rate: the run predates the axis
    # this metric measures, so its two throughputs genuinely are one number.
    gaps = np.where(gaps > 0.0, gaps, dur)
    out = np.empty(len(df), dtype=float)
    out[order] = gaps
    df['elapsed'] = out
    # NaN, not 0.0, when there is nothing to divide by -- the same rule `thr_batch` and
    # `thr_task` apply, and for the same reason: an unmeasured batch counted as zero
    # throughput drags every mean toward zero.
    df['thr_elapsed'] = np.where(df['elapsed'] > 0.0,
                                 df['total_items'] / df['elapsed'], np.nan)
    return df


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
        # upstream Tukey outlier flag (see _bdf) — 0 when the source row predates it.
        'is_outlier' : getattr(s, 'is_outlier', 0),
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
