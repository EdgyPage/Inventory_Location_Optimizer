"""DataFrame builders + steady-state series alignment (verbatim from the monoliths).

`_bdf`/`_tdf` turn the namedtuple stat lists from Picking_Data into the wide per-batch /
per-task frames every graph consumes; `_roll` is the rolling-mean helper; `_metric_series`
and `_aligned` build the paired matrices the stats suite tests.
"""
import numpy as np
import pandas as pd

from Optimization.Performance_Evaluations.common import units


#: Every declared-shape column this tool touches, and HOW (phase-2 of the staged
#: semantics gate).  A PURE LITERAL: Tests/architecture/test_column_semantics.py
#: AST-reads it and validates against Schema/semantics.py without importing this
#: module, so declaring costs no dependency.
SEMANTIC_USES = {'sim_db': {
    'batch_stats.duration': 'ratio', 'batch_stats.task_makespan': 'ratio',
    'batch_stats.total_items': 'ratio', 'batch_stats.avg_concurrent_pickers': 'read',
    'batch_stats.picking_pct': 'read', 'batch_stats.traveling_pct': 'read',
    'batch_stats.sigma_fd': 'read', 'batch_stats.reload_moves': 'read',
    'batch_stats.reorder_placements': 'read', 'batch_stats.queue_depth': 'read',
    'batch_stats.recv_depth': 'read', 'batch_stats.recv_unloaded': 'read',
    'batch_stats.recv_cut': 'read', 'batch_stats.recv_seconds': 'read',
    'batch_stats.lead_queue_depth': 'read', 'batch_stats.in_transit_qty': 'read',
    'batch_stats.is_outlier': 'read', 'batch_stats.batch_start_time': 'read',
    # Demand service.  `carryover.qty` is declared READ, not SUM, and that is the honest
    # verb: its kind depends on `reason` in the same row, so a row-free total over the
    # table does not exist.  `_cdf` FILTERS to the two supply reasons first — both FLOWS,
    # both PIECES — and totals what is left, which is a different operation from the one
    # the tag refuses.
    'carryover.reason': 'read', 'carryover.qty': 'read',
    'carryover.batch_id': 'read', 'batch_stats.items_demanded': 'ratio',
    # The yard.  Every stamp is READ and then DIFFERENCED against another stamp on the
    # same clock; no stamp is ever summed, which is the misread the tags exist to stop.
    # The four drain levels are read and never summed across drains — `binding_cut` counts
    # the drains where one was non-zero.
    'yard_trailers.seq': 'read', 'yard_trailers.status': 'read',
    'yard_trailers.arrived_s': 'read', 'yard_trailers.staged_s': 'read',
    'yard_trailers.emptied_s': 'read',
    'yard_drains.batch': 'read', 'yard_drains.yard_start': 'read',
    'yard_drains.free_doors_start': 'read', 'yard_drains.yard_end': 'read',
    'yard_drains.staged_remainder_end': 'read',
}}


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
        # The STATED whole demand service is a share of.  Guarded like its neighbours so a
        # pre-column vintage reads 0 -- and `_cdf` turns a zero denominator into NaN rather
        # than a perfect score, because a batch that demanded nothing was not measured.
        'items_demanded'        : getattr(s, 'items_demanded', 0),
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


# ── the yard's two frames, and demand service ────────────────────────────────────
# THE ONE PLACE seconds become days in this suite.  `units.SECONDS_PER_DAY` is imported
# rather than restated for the reason `units` spells out: the last conversion spelled
# inline at each call site was wrong by 1000x in all five copies, because each copy stated
# the divisor instead of importing it.

#: The two carryover reasons that mean DEMAND WENT UNSERVED.  `unpicked_daycut` is
#: deliberately absent: the whistle stopping a picker is a staffing fact, and counting it
#: here would let a longer shift read as better inbound.  `unpicked_notasks` likewise —
#: no task was built for it, which is a scheduling outcome, not an availability one.
MISSED_REASONS = ('unpicked_unstocked', 'unpicked_unavailable')


def _ydf(rows, run_end_s, threshold_days):
    """Per-TRAILER frame: raw stamps in, spans and the fee proxy out.

    `run_end_s` closes the CENSORED rows.  A trailer still on site when the run stops has
    `emptied_s` NULL, and its detention is right-censored rather than absent: it has been
    held at least until the run ended, so the run end is the honest lower bound and the
    `censored` column is what keeps that visible.  Dropping those rows instead would report
    an adversarial ordering's fee as CLIPPED rather than concentrated, which inverts the
    signal that arm exists to produce.

    `threshold_days` is the run's own `INBOUND_FEE_THRESHOLD_DAYS`.  It enters HERE and
    nowhere else, which is what makes a finished run re-reportable under a different
    threshold without re-simulating.
    """
    if not rows:
        return pd.DataFrame(columns=['seq', 'status', 'censored', 'detention_days',
                                     'yard_wait_days', 'door_span_days', 'overage_days',
                                     'over_threshold'])
    day = float(units.SECONDS_PER_DAY)
    recs = []
    for r in rows:
        arrived = float(r['arrived_s'])
        staged = r['staged_s']
        emptied = r['emptied_s']
        censored = emptied is None
        end = float(run_end_s) if censored else float(emptied)
        # A censored trailer whose arrival postdates the last recorded batch epoch would
        # otherwise read as negative detention.  Clamp at zero rather than emitting a
        # negative span: the trailer HAS been held, for less than the resolution of the
        # bound available, and a negative number here would silently offset a real one.
        recs.append({
            'seq'           : int(r['seq']),
            'status'        : r['status'],
            'censored'      : censored,
            'detention_days': max(0.0, end - arrived) / day,
            'yard_wait_days': (np.nan if staged is None
                               else max(0.0, float(staged) - arrived) / day),
            # The time the trailer actually occupied a door — the numerator of door
            # utilization.  NaN when it never took one, or when it holds one still.
            'door_span_days': (np.nan if staged is None or censored
                               else max(0.0, float(emptied) - float(staged)) / day),
        })
    df = pd.DataFrame(recs)
    df['overage_days'] = (df['detention_days'] - float(threshold_days)).clip(lower=0.0)
    df['over_threshold'] = df['overage_days'] > 0.0
    return df


def _ddf(rows):
    """Per-DRAIN frame: the contention pair, the binding-cut pair, and the indicator.

    Every column here is a LEVEL.  `binding_cut` is the one derived value and it is a
    BOOLEAN per drain, never a magnitude: the additive statistic over the pair is the count
    of drains where inbound work was left, and summing the levels themselves would re-count
    the same standing trailer once per batch it waits (the `recv_cut` scar, 101x on a
    published headline).
    """
    if not rows:
        return pd.DataFrame(columns=['batch', 'yard_start', 'free_doors_start', 'yard_end',
                                     'staged_remainder_end', 'binding_cut'])
    df = pd.DataFrame([{
        'batch'               : int(r['batch']),
        'yard_start'          : int(r['yard_start']),
        'free_doors_start'    : int(r['free_doors_start']),
        'yard_end'            : int(r['yard_end']),
        'staged_remainder_end': int(r['staged_remainder_end']),
    } for r in rows])
    df['binding_cut'] = (df['yard_end'] > 0) | (df['staged_remainder_end'] > 0)
    return df


def _cdf(rows, df_b):
    """Per-BATCH demand service, folded from carryover rows over the two supply reasons.

    The fold is reason-SELECTIVE, and that is the whole design: three reason families share
    `carryover.qty` under one PK — the put-side ones are LEVELS re-emitted every batch and
    the pick-side ones are FLOWS — so a total over the table is not defined.  Filtering to
    two FLOWS first makes the sum legal; `sim_semantics`' ByDiscriminator on `reason` is
    the declaration that says so.

    The denominator is the STATED whole `items_demanded`, not what was picked: a
    denominator that shrinks as service degrades would flatter exactly the arms this
    quantity exists to expose.
    """
    demanded = (df_b.set_index('batch_id')['items_demanded']
                if 'items_demanded' in df_b else pd.Series(dtype=float))
    missed: dict = {int(b): 0 for b in demanded.index}
    for r in rows:
        if r['reason'] in MISSED_REASONS:
            b = int(r['batch_id'])
            missed[b] = missed.get(b, 0) + int(r['qty'])
    if not missed:
        return pd.DataFrame(columns=['batch_id', 'missed_pieces', 'missed_share'])
    df = pd.DataFrame({'batch_id': sorted(missed),
                       'missed_pieces': [missed[b] for b in sorted(missed)]})
    dem = df['batch_id'].map(demanded).astype(float)
    # NaN, not 0.0, on a batch that demanded nothing — the same rule `thr_batch` applies.
    # A batch with no demand did not achieve perfect service; it was not measured.
    df['missed_share'] = np.where(dem > 0.0, df['missed_pieces'] / dem * 100.0, np.nan)
    return df


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
