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
    # Production labour.  `duration` is a SPAN and SPAN is additive, so the SUM is legal —
    # but only once `role` has selected the rows, which is why `role` is READ here and the
    # fold lives in `_wdf` rather than in a total over the table.  A row-free
    # `SUM(duration)` would add put intervals to pick instants, and before 2026-08-25 it
    # silently did exactly that with every pick row defaulting to zero seconds.
    'work_events.batch_id': 'read', 'work_events.role': 'read',
    'work_events.duration': 'sum',
    # The calibrated era.  `work_day` is the LABEL a batch is grouped into a day by;
    # `released_late` is a SPAN and is summed per DAY (the check's second clause reads the
    # per-day total against the ledger's verdict).  The ledger's `drained` is read per day
    # and COUNTED, never summed; its stamps are read and differenced on one clock
    # (`last_finish - cap_end` is the START-gate overtime); its levels are read and never
    # summed across days.
    'batch_stats.work_day': 'read', 'batch_stats.released_late': 'sum',
    'shift_days.day': 'read', 'shift_days.cap_end': 'read', 'shift_days.end_s': 'read',
    'shift_days.drained': 'read', 'shift_days.standing': 'read',
    'shift_days.standing_put': 'read', 'shift_days.standing_dock': 'read',
    'shift_days.standing_carry': 'read', 'shift_days.standing_carry_labour': 'read',
    'shift_days.standing_carry_supply': 'read', 'shift_days.last_finish': 'read',
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
        # The working day a batch was RELEASED into (0 on every continuous-release run)
        # and the seconds it missed its slot by.  Carried so the day frame below can join
        # the ledger to the batches through `work_day`; `tables.tidy` lists `work_day` as
        # bookkeeping so it never becomes a metric row.
        'work_day'              : getattr(s, 'work_day', 0),
        'released_late'         : getattr(s, 'released_late', 0.0),
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


# ── production labour: the three legs of the objective, per batch ────────────────
#: The roles whose `work_events` rows are INTERVALS, and the frame column each becomes.
#:
#: 'pick' is absent and that is the design, not an omission.  A pick row is a state change
#: stamped at an instant (task_start / arrive / pick / done / cut) and carries a NULL
#: duration; a picker's work is the span BETWEEN two rows, which `task_stats.duration`
#: already measures and which `production_time` already publishes.  Reading the pick leg
#: out of this table instead would re-derive a number the task frame states directly, and
#: would have read ZERO on every vintage before `duration` became nullable.
_WORK_ROLES = {'put': 'put_seconds', 'receive': 'unload_seconds'}


def _wdf(rows, df_b, df_t):
    """Per-BATCH production labour: put + unload from `work_events`, pick from the tasks.

    THE OBJECTIVE'S FRAME.  `total_production_seconds` is the quantity the whole inbound
    effort selects on — put-away hours and picking hours move in OPPOSITE directions under
    a placement rule, so a comparison on either leg alone systematically favours arms that
    buy one with the other.

    An EMPTY frame when `rows` is empty, never a frame of zeros.  This is the same rule
    `_ydf` and `_ddf` follow and it is the load-bearing one here: a run predating
    `work_events` and a run whose crews did no work are indistinguishable by row count, and
    filling the missing legs with 0.0 would publish `total = pick` as if the put leg had
    been measured and found to be nothing.  Empty makes the metric ABSENT instead, which is
    what the capability gate and every downstream `_aligned` are written to handle.

    Zeros WITHIN a populated frame are honest and are filled deliberately: once the table
    has rows, a batch with no put row genuinely put nothing away, and dropping it would
    make the batch index of this frame disagree with the batch frame's for no reason a
    reader could recover.
    """
    if not rows:
        return pd.DataFrame(columns=['batch_id', 'put_seconds', 'unload_seconds',
                                     'pick_seconds', 'production_seconds',
                                     'untimed_rows'])
    per_batch: dict = {}
    for r in rows:
        b = int(r['batch_id'])
        rec = per_batch.setdefault(b, {'put_seconds': 0.0, 'unload_seconds': 0.0,
                                       'untimed_rows': 0})
        column = _WORK_ROLES.get(r['role'])
        if column is None:
            continue
        # `n_rows - n_timed` counts rows the SUM silently skipped, and it counts them ONLY
        # among the roles this frame folds.  A pick row carrying no duration is the
        # contract, not a loss — its leg comes from the task frame — and counting those
        # would report tens of thousands of correctly-untimed rows as work excluded from
        # the total, which is a louder and more alarming claim than the one it replaced.
        # A PUT or RECEIVE row with no duration is the real thing worth surfacing: an
        # interval that went missing, so a leg reads short with nothing else to say so.
        rec['untimed_rows'] += int(r['n_rows']) - int(r['n_timed'])
        # NULL when every row of this (batch, role) is an instant — 0.0 seconds of
        # interval work, which for put and receive means the role did nothing here.
        rec[column] += float(r['seconds'] or 0.0)
    # The batch INDEX comes from the batch frame: it is the run's own statement of which
    # batches happened, and a batch that picked without putting anything away belongs in
    # this frame at zero rather than being invisible.  Falling back to the work rows' own
    # batches keeps the frame buildable in a test that has no batch frame to hand.
    if df_b is not None and not df_b.empty and 'batch_id' in df_b:
        index = sorted(int(b) for b in df_b['batch_id'].unique())
    else:
        index = sorted(per_batch)
    picked = (df_t.groupby('batch_id')['duration'].sum()
              if df_t is not None and not df_t.empty and 'duration' in df_t
              else pd.Series(dtype=float))
    recs = []
    for b in index:
        rec = per_batch.get(b, {'put_seconds': 0.0, 'unload_seconds': 0.0,
                                'untimed_rows': 0})
        pick = float(picked.get(b, 0.0))
        recs.append({'batch_id': b,
                     'put_seconds': rec['put_seconds'],
                     'unload_seconds': rec['unload_seconds'],
                     'pick_seconds': pick,
                     'production_seconds': (rec['put_seconds'] + rec['unload_seconds']
                                            + pick),
                     'untimed_rows': rec['untimed_rows']})
    return pd.DataFrame(recs)


_SHIFT_COLS = ['day', 'cap_end', 'end_s', 'drained', 'capped', 'standing', 'standing_put',
               'standing_dock', 'standing_carry', 'standing_carry_labour',
               'standing_carry_supply', 'last_finish', 'overtime_s', 'n_batches',
               'released_late_s', 'items_demanded', 'total_items', 'pick_seconds',
               'put_seconds', 'unload_seconds', 'pick_utilization', 'put_utilization',
               'recv_utilization']


def _level_or_nan(v):
    """A close-out LEVEL the vintage did not record (the pre-split carry halves) is NaN --
    unknown -- never 0, which would read as "nothing stood"."""
    return np.nan if v is None else int(v)


def _sdf(rows, df_b, df_w, expectations=None):
    """Per-DAY frame of the drain-or-cap ledger, joined to the batches and the labour.

    THE THROUGHPUT AUDIT'S FRAME.  One row per working day the ledger closed: the verdict
    (`drained` / `capped` -- as `load_shift_days` serves it, with overtime folded in, so an
    `overtime_s` above 0 is always a capped row and `days_capped` follows), the close-out
    levels, the START-gate overtime
    (`last_finish - cap_end`, floored at 0), and -- joined through `batch_stats.work_day`
    -- the day's batches, released-late seconds, demand and picks, and the three crews'
    worked seconds.  With `expectations` (`equilibrium.expectations_for`) each department's
    UTILIZATION is worked ÷ (crew × S) for that day; without them the three columns are
    NaN, never zero -- an unknown crew is not a crew of nobody.

    An EMPTY frame when `rows` is empty, by the same rule `_ydf` / `_wdf` follow: a run
    without the drain-or-cap shift never closed a day, and a frame of zeros would say every
    day drained.  Utilization here is per DAY for the figures; the equilibrium check
    computes it as a ratio of sums over the window itself and never from these rows.
    """
    if not rows:
        return pd.DataFrame(columns=_SHIFT_COLS)
    df = pd.DataFrame([{
        'day'           : int(r['day']),
        'cap_end'       : float(r['cap_end']),
        'end_s'         : float(r['end_s']),
        'drained'       : int(bool(r['drained'])),
        'standing'      : int(r['standing']),
        'standing_put'  : int(r['standing_put']),
        'standing_dock' : int(r['standing_dock']),
        'standing_carry': int(r['standing_carry']),
        # The carry by cause: labour (the cut's) is what the drained verdict read; supply
        # (the shelf's) is reported beside it.  NaN on the pre-split vintage.
        'standing_carry_labour': _level_or_nan(r.get('standing_carry_labour')),
        'standing_carry_supply': _level_or_nan(r.get('standing_carry_supply')),
        'last_finish'   : float(r['last_finish']),
    } for r in rows]).sort_values('day').reset_index(drop=True)
    df['capped'] = 1 - df['drained']
    df['overtime_s'] = (df['last_finish'] - df['cap_end']).clip(lower=0.0)
    # The day's batches: count, lag, demand, picks and the pick leg (Σ task time).
    if df_b is not None and not df_b.empty and 'work_day' in df_b:
        g = df_b.groupby('work_day')
        per_day = pd.DataFrame({
            'n_batches'      : g.size(),
            'released_late_s': g['released_late'].sum(),
            'items_demanded' : g['items_demanded'].sum(),
            'total_items'    : g['total_items'].sum(),
            'pick_seconds'   : g['task_makespan'].sum(min_count=1),
        })
        df = df.merge(per_day, left_on='day', right_index=True, how='left')
    else:
        for c in ('n_batches', 'released_late_s', 'items_demanded', 'total_items',
                  'pick_seconds'):
            df[c] = np.nan
    # The put and unload legs, per batch in the work frame, folded to the day.
    if (df_w is not None and not df_w.empty and df_b is not None and not df_b.empty
            and 'work_day' in df_b):
        day_of = df_b.set_index('batch_id')['work_day']
        w = df_w.assign(day=df_w['batch_id'].map(day_of)).dropna(subset=['day'])
        gw = w.groupby(w['day'].astype(int))
        legs = pd.DataFrame({'put_seconds': gw['put_seconds'].sum(),
                             'unload_seconds': gw['unload_seconds'].sum()})
        df = df.merge(legs, left_on='day', right_index=True, how='left')
    else:
        df['put_seconds'] = np.nan
        df['unload_seconds'] = np.nan
    for col in ('n_batches',):
        df[col] = df[col].fillna(0).astype(int)
    df['released_late_s'] = df['released_late_s'].fillna(0.0)
    # Utilization per day against the whole day's grant, only where a crew is expected.
    for dept, col in (('pick', 'pick_seconds'), ('put', 'put_seconds'),
                      ('recv', 'unload_seconds')):
        spec = ((expectations or {}).get('departments') or {}).get(dept)
        if spec is None:
            df[f'{dept}_utilization'] = np.nan
            continue
        granted = float(spec['crew']) * float(expectations['day_seconds'])
        df[f'{dept}_utilization'] = df[col] / granted if granted > 0 else np.nan
    return df[_SHIFT_COLS]


def _roll(df, col, win=50):
    return df.sort_values('batch_id')[col].rolling(win, min_periods=1).mean().values


#: metric-source kind -> (which frame serves it, how it reduces to one value per batch).
#:
#: This table replaced an `if source == 'batch': ... else: <the task frame>` fallthrough,
#: and the replacement is the point.  Under the fallthrough a kind added to
#: `quantities.FRAME_TABLE` and forgotten here was looked up in the TASK frame — a
#: per-trailer column asked of a per-task frame, found absent, and returned as an empty
#: Series — so the quantity reported as unmeasurable rather than as misrouted, on every
#: arm, silently.  An unknown kind now raises.
_SOURCE_FRAME = {
    'batch':     ('batch', None),
    'task_mean': ('task',  'mean'),
    'task_sum':  ('task',  'sum'),
    # Already one row per batch, like 'batch' — the fold happened in SQL and again in
    # `_wdf`, because the rows underneath are events and there are ~30k of them per arm.
    'work':      ('work',  None),
}


def _metric_series(frames, source, col, ss_lo):
    """Per-batch steady-state Series (indexed by batch_id) for one strategy/metric.

    `frames` is {'batch': df_b, 'task': df_t, 'work': df_w} for ONE strategy.  A mapping
    rather than positional frames because the set grows: each new per-batch source used to
    mean a new positional argument threaded through six call sites, and the one that was
    missed would have fallen through to the task frame rather than failing.
    """
    try:
        which, fold = _SOURCE_FRAME[source]
    except KeyError:
        raise KeyError(
            f'{source!r} is not a metric source ({sorted(_SOURCE_FRAME)}). A frame kind '
            f'declared in quantities.FRAME_TABLE must be routed here too, or nothing can '
            f'read it.') from None
    df = frames.get(which)
    if df is None:
        raise KeyError(f'metric source {source!r} needs the {which!r} frame and the '
                       f'caller passed none (has: {sorted(frames)})')
    # Guarded before the filter: an arm whose DB holds no batch rows builds a frame with no
    # COLUMNS at all, and `df['batch_id']` on that is a KeyError rather than an empty read.
    if df.empty or 'batch_id' not in df or col not in df:
        return pd.Series(dtype=float)
    d = df[df['batch_id'] >= ss_lo]
    if d.empty:
        return pd.Series(dtype=float)
    if fold is None:
        return d.set_index('batch_id')[col]
    g = d.groupby('batch_id')[col]
    return g.mean() if fold == 'mean' else g.sum()


def _aligned(series_by_key, keys):
    """Intersect batch indices common to all strategies → paired matrix (n × k)."""
    idxs = [set(series_by_key[k].index) for k in keys if len(series_by_key[k])]
    if len(idxs) != len(keys) or not idxs:
        return None
    common = sorted(set.intersection(*idxs))
    if len(common) < 3:
        return None
    return np.column_stack([series_by_key[k].loc[common].values for k in keys])
