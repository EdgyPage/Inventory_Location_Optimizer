"""test_production_hours.py — the objective's third leg, and the traps around measuring it.

Total production hours (unload + put + pick) is the inbound funnel's selection metric, and
one of its three legs had never been persisted as a scalar: `putaway_seconds` is an in-sim
property on `Inventory_Manager` that reaches no table, so the hours live only in
`work_events` — a table this analysis suite had never read a row of.

Every test here guards a way that measurement could look right and be wrong:

  * **A frame of zeros is not a measurement.**  A run predating `work_events` and a run
    whose crews did nothing are indistinguishable by row count, so `_wdf` returns an EMPTY
    frame rather than zeros when there are no rows at all.  Filled zeros WITHIN a populated
    frame are the opposite case and are asserted too — a batch that put nothing away
    belongs in the frame at zero, or this frame's batch index silently disagrees with the
    batch frame's.
  * **NULLs are counted, not just skipped.**  `work_events.duration` is nullable by design
    (an interval for a put, NULL for a pick, which is an instant) and `SUM` skips NULLs
    silently.  It was `NOT NULL DEFAULT 0` until 2026-08-25, so on an older vintage every
    pick row claims zero seconds and a row-free `SUM(duration)` looks like a total while
    being put+receive only.
  * **A misrouted metric source now RAISES.**  `_metric_series` was written as "batch, ELSE
    the task frame", so a kind added to `FRAME_TABLE` and forgotten in the dispatch was
    looked up in `df_t`, found absent, and returned as an empty Series — reported as
    unmeasurable rather than as misrouted, on every arm, in silence.
  * **The era gate still refuses, and `quantities_optional` does not weaken it.**  The
    headline figure draws two quantities it can survive without; every other consumer of
    them takes the refusal.

Run:  python -m pytest Tests/unit/test_production_hours.py -q
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from Optimization.persistence import Picking_Data as PD
from Optimization.Performance_Evaluations.common.frames import _metric_series, _wdf
from Optimization.Performance_Evaluations.common.series import _build_series
from Optimization.Performance_Evaluations.core import era, quantities as Q
from Optimization.Performance_Evaluations.core import requests as R
from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY, evaluation


def _rows(*specs):
    """`work_hours_frame` rows: (batch, role, seconds, n_rows, n_timed)."""
    return [{'batch_id': b, 'role': r, 'seconds': s, 'n_rows': n, 'n_timed': t}
            for b, r, s, n, t in specs]


_BATCHES = pd.DataFrame({'batch_id': [0, 1, 2]})
_TASKS = pd.DataFrame({'batch_id': [0, 0, 1], 'duration': [10.0, 5.0, 7.0]})


# ── 1. absent is not zero ────────────────────────────────────────────────────────

def test_no_work_rows_gives_an_empty_frame_and_not_a_frame_of_zeros():
    """THE load-bearing one.

    A frame of zeros would make `total_production_time` equal the pick leg exactly, on
    every archived run, while claiming to be the sum of three — which is the substitution
    the whole quantity exists to prevent. Empty makes the metric ABSENT instead, which is
    what `_aligned` and the capability gate are both written to handle.
    """
    df = _wdf([], _BATCHES, _TASKS)
    assert df.empty
    # The columns must still be there: a consumer asking `col not in df` has to get a
    # useful answer from an empty frame, not a KeyError.
    assert {'put_seconds', 'unload_seconds', 'pick_seconds',
            'production_seconds'} <= set(df.columns)


def test_a_populated_frame_fills_zeros_for_the_batches_that_did_none():
    """The opposite rule, and it is not in tension with the one above.

    Once the table HAS rows, a batch with no put row genuinely put nothing away. Dropping
    it would make this frame's batch index disagree with the batch frame's for no reason a
    reader could recover, and every paired statistic in the suite aligns on that index.
    """
    df = _wdf(_rows((0, 'put', 4.0, 2, 2)), _BATCHES, _TASKS).set_index('batch_id')
    assert list(df.index) == [0, 1, 2]
    assert df.loc[1, 'put_seconds'] == 0.0
    assert df.loc[2, 'put_seconds'] == 0.0
    # ...and batch 1's pick leg is its own, so its total is not zero either.
    assert df.loc[1, 'production_seconds'] == pytest.approx(7.0)


def test_the_batch_index_comes_from_the_batch_frame_not_from_the_work_rows():
    """A batch that picked and put nothing away must still appear.

    The work rows alone would have named batches {0}, and the two batches that did no
    put-away would have vanished from the objective entirely — taking their picking hours
    with them and flattering exactly the arms that defer put-away.
    """
    df = _wdf(_rows((0, 'put', 4.0, 1, 1)), _BATCHES, _TASKS)
    assert list(df['batch_id']) == [0, 1, 2]


# ── 2. the three legs, and what is deliberately not among them ──────────────────

def test_the_three_legs_sum_to_the_objective_per_batch():
    df = _wdf(_rows((0, 'put', 4.0, 2, 2), (0, 'receive', 3.0, 1, 1),
                    (2, 'put', 2.0, 1, 1)), _BATCHES, _TASKS).set_index('batch_id')
    assert df.loc[0, 'put_seconds'] == pytest.approx(4.0)
    assert df.loc[0, 'unload_seconds'] == pytest.approx(3.0)
    assert df.loc[0, 'pick_seconds'] == pytest.approx(15.0)     # 10 + 5, summed per batch
    for b in df.index:
        assert df.loc[b, 'production_seconds'] == pytest.approx(
            df.loc[b, 'put_seconds'] + df.loc[b, 'unload_seconds']
            + df.loc[b, 'pick_seconds'])


def test_the_pick_leg_comes_from_the_task_frame_and_never_from_work_events():
    """`work_events` cannot supply it, and a pick row's seconds are not a number.

    A pick row is a state change stamped at an instant and carries a NULL duration; the
    work is the SPAN between two of them, which `task_stats.duration` already measures.
    Reading the pick leg out of the event table would have read ZERO on every vintage
    before `duration` became nullable — the 29,657-rows-claiming-no-time defect, folded
    silently into the objective.
    """
    rows = _rows((0, 'pick', None, 9, 0), (0, 'put', 4.0, 2, 2))
    df = _wdf(rows, _BATCHES, _TASKS).set_index('batch_id')
    assert df.loc[0, 'pick_seconds'] == pytest.approx(15.0)   # the TASK frame's number
    assert df.loc[0, 'production_seconds'] == pytest.approx(19.0)
    # A pick row contributes to no leg, so it cannot inflate put or unload either.
    assert df.loc[0, 'put_seconds'] == pytest.approx(4.0)
    assert df.loc[0, 'unload_seconds'] == 0.0


def test_rows_the_sum_skipped_are_counted_only_where_skipping_is_a_defect():
    """The NULL handling is stated in the frame, not only in a docstring — and SCOPED.

    `SUM` skips NULLs in silence, so "this role did no timed work" and "this role's rows
    carry no durations at all" arrive as the same number. The count is what lets the figure
    say so. It covers ONLY the roles this frame folds: a pick row with no duration is the
    contract (its leg comes from the task frame), and counting those put a subtitle on the
    figure announcing that fifty thousand rows of work had been excluded from the total —
    a louder and more alarming claim than the true one, on every healthy run.
    """
    df = _wdf(_rows((0, 'pick', None, 9, 0), (0, 'put', 4.0, 3, 2)),
              _BATCHES, _TASKS).set_index('batch_id')
    assert df.loc[0, 'untimed_rows'] == 1, 'the 9 pick rows are the contract, not a loss'
    assert df.loc[1, 'untimed_rows'] == 0
    # A run with nothing but correctly-untimed pick rows reports NOTHING to warn about.
    clean = _wdf(_rows((0, 'pick', None, 9000, 0), (0, 'put', 4.0, 3, 3)),
                 _BATCHES, _TASKS)
    assert int(clean['untimed_rows'].sum()) == 0
    # A role whose every row is an instant sums to NULL, which must land as 0.0 seconds
    # rather than as NaN poisoning the arm's whole total.
    df2 = _wdf(_rows((0, 'put', None, 2, 0)), _BATCHES, _TASKS).set_index('batch_id')
    assert df2.loc[0, 'put_seconds'] == 0.0
    assert df2.loc[0, 'untimed_rows'] == 2, 'an untimed PUT row is a missing interval'
    assert not math.isnan(df2.loc[0, 'production_seconds'])


# ── 3. the dispatch that used to fall through ───────────────────────────────────

def test_an_unrouted_metric_source_raises_instead_of_reading_the_task_frame():
    """The trap `quantities.PAIRED_KINDS` warns about, closed at the mechanism.

    'trailer' is a real `FRAME_TABLE` kind. Under the old fallthrough it was looked up in
    the per-task frame and came back as an empty Series, so the quantity reported as
    unmeasurable rather than as misrouted.
    """
    frames = {'batch': _BATCHES, 'task': _TASKS, 'work': pd.DataFrame()}
    with pytest.raises(KeyError, match='not a metric source'):
        _metric_series(frames, 'trailer', 'overage_days', 0)


def test_a_missing_frame_raises_rather_than_reading_an_absent_one():
    with pytest.raises(KeyError, match="needs the 'work' frame"):
        _metric_series({'batch': _BATCHES, 'task': _TASKS}, 'work',
                       'production_seconds', 0)


def test_every_frame_kind_the_quantity_table_declares_can_actually_be_read():
    """`FRAME_TABLE` and the dispatch must not drift apart again.

    A kind in `PAIRED_KINDS` is handed to the significance suite, so it MUST resolve here;
    a kind outside it is read by its own family module through its own accessor and is
    excluded on purpose (`drain`'s rows are levels, `trailer`'s are not per-batch).
    """
    from Optimization.Performance_Evaluations.common import frames as F
    for kind in Q.PAIRED_KINDS:
        assert kind in F._SOURCE_FRAME, (
            f'{kind!r} is paired but nothing routes it — it would have fallen through')
    for kind in F._SOURCE_FRAME:
        assert kind in Q.FRAME_TABLE, f'{kind!r} is routed but declares no source table'


def test_the_work_kind_pairs_by_batch_id_like_the_batch_kind():
    """The funnel's decision rule is a moving-block CI over paired per-batch values."""
    df = _wdf(_rows((0, 'put', 4.0, 1, 1), (2, 'put', 2.0, 1, 1)), _BATCHES, _TASKS)
    s = _metric_series({'batch': _BATCHES, 'task': _TASKS, 'work': df},
                       'work', 'production_seconds', 0)
    assert list(s.index) == [0, 1, 2]
    assert s.loc[0] == pytest.approx(19.0)
    # ...and the steady-state cut applies to it exactly as it does to a batch column.
    assert list(_metric_series({'batch': _BATCHES, 'task': _TASKS, 'work': df},
                               'work', 'production_seconds', 2).index) == [2]


# ── 4. the series document, which is what a headline slot reads ─────────────────

def _one_strategy():
    return [dict(key='a', color='#000000', label='a', initial='', assignment='',
                 reslot='')]


def _series_batch_frame():
    """A batch frame with the columns `_build_series` reads, over three batches."""
    return pd.DataFrame({
        'batch_id': [0, 1, 2], 'duration': [10.0, 10.0, 10.0],
        'completion_rate': [1.0, 1.0, 1.0], 'sigma_fd': [5.0, 5.0, 5.0],
        'picking_pct': [90.0, 90.0, 90.0], 'traveling_pct': [10.0, 10.0, 10.0],
        'total_items': [100.0, 100.0, 100.0]})


def test_the_new_scalars_are_nan_when_their_frames_are_absent():
    """NOT zero. A headline group whose value is NaN on every arm is DROPPED; a group of
    zeros is drawn, and a drawn zero is a claim that the run measured nothing."""
    S = _build_series(_one_strategy(), {'a': _series_batch_frame()}, {'a': _TASKS})
    d = S['a']
    for field in ('ss_prod_total', 'ss_putaway', 'ss_unload', 'yard_overage_total'):
        assert math.isnan(d[field]), f'{field} is not NaN without its frame'


def test_the_production_scalar_is_the_steady_state_mean_of_the_objective():
    work = _wdf(_rows((0, 'put', 4.0, 1, 1), (1, 'put', 6.0, 1, 1),
                      (2, 'put', 2.0, 1, 1)), _BATCHES, _TASKS)
    S = _build_series(_one_strategy(), {'a': _series_batch_frame()}, {'a': _TASKS},
                      {'a': work})
    # production per batch: (4+15)=19, (6+7)=13, (2+0)=2 -> mean 34/3 over the whole run,
    # which is the steady-state window at this depth.
    assert S['a']['ss_prod_total'] == pytest.approx(34.0 / 3.0)
    assert S['a']['ss_putaway'] == pytest.approx(4.0)       # (4 + 6 + 2) / 3


def test_the_yard_scalar_is_a_run_total_and_says_so_in_its_name():
    """The one scalar here that is NOT a steady-state mean.

    The fee's instances are trailers, which carry no batch index to take a trailing window
    over, so it is the whole run's accrued trailer-days — the number a carrier bills. It
    is named `yard_overage_total` rather than `ss_*` for exactly that reason.
    """
    yard = pd.DataFrame({'overage_days': [1.5, 0.0, 2.25], 'censored': [False] * 3})
    S = _build_series(_one_strategy(), {'a': _series_batch_frame()}, {'a': _TASKS},
                      None, {'a': yard})
    assert S['a']['yard_overage_total'] == pytest.approx(3.75)
    assert 'ss_yard_overage' not in S['a']


def test_every_headline_slot_can_actually_be_read_from_the_series_document():
    """`_check_order` proves each names a field; this proves the builder WRITES it.

    The two are different claims and only the second one is about behaviour: a slot could
    name `ss_whatever` forever and pass the import check while rendering an empty panel.
    """
    work = _wdf(_rows((0, 'put', 4.0, 1, 1)), _BATCHES, _TASKS)
    yard = pd.DataFrame({'overage_days': [1.0], 'censored': [False]})
    S = _build_series(_one_strategy(), {'a': _series_batch_frame()}, {'a': _TASKS},
                      {'a': work}, {'a': yard})
    for key in Q.HEADLINE_ORDER:
        field = Q.BY_KEY[key].source.steady_state
        assert field in S['a'], f'{key} names {field!r}, which _build_series never writes'


# ── 5. the era gate, and the narrow permission beside it ────────────────────────

def test_the_new_quantities_are_gated_and_the_static_gate_still_passes():
    for key in ('total_production_time', 'putaway_time', 'unload_time'):
        assert Q.BY_KEY[key].capability == 'work_events'
        assert Q.BY_KEY[key].capability in PD.SIM_CAPABILITIES
    assert era.findings() == [], era.findings()


def test_the_unload_leg_reads_work_events_and_not_the_dock_column():
    """The one recorded deviation from the ticket, pinned so it cannot drift back.

    `batch_stats.recv_seconds` carries the same measurement and is the obvious source, but
    it sits OUTSIDE the guaranteed sim-DB surface and POSTDATES `work_events` — so no
    honest capability covers it, and an unguarded read fills a pre-dock vintage's unload
    leg with a plausible zero. The second assertion is the load-bearing half: if
    `recv_seconds` ever enters the guaranteed surface the deviation stops being necessary
    and this test should be the thing that says so.
    """
    from Schema import compat
    src = Q.BY_KEY['unload_time'].source
    assert src.db_reads[0] == 'work_events'
    gaps = compat.validate(compat.Requires(family='sim_db', label='probe',
                                           tables={'batch_stats': ('recv_seconds',)}))
    assert gaps, ('batch_stats.recv_seconds is now version-free, so the unload leg no '
                  'longer needs to route through work_events — revisit the deviation')


def test_an_optional_quantity_is_drawn_but_does_not_trigger_the_era_refusal():
    """`quantities_optional` must weaken the gate for its own figure and nothing else."""
    ev = EVAL_BY_KEY['headline.top_vs_baseline']
    assert 'total_production_time' in ev.quantities_optional
    assert 'yard_overage_days' in ev.quantities_optional

    class _Ctx:
        def capabilities(self):
            return frozenset()                       # a run that can answer nothing

    assert R.era_shortfall(_Ctx(), ev) is None, (
        'the headline was refused over a quantity it degrades per group')
    # ...while a figure that declares the same quantity as REQUIRED is refused.
    legs = EVAL_BY_KEY['labor.production_legs']
    unmet = R.era_shortfall(_Ctx(), legs)
    assert isinstance(unmet, R.EraUnmet) and 'work_events' in unmet.missing


def test_a_quantity_cannot_be_declared_both_required_and_optional():
    with pytest.raises(ValueError, match='both required and optional'):
        @evaluation(key='test.both_ways', label='x', scope='config', family='labor',
                    shape='ranked', quantities=('production_time',),
                    quantities_optional=('production_time',))
        def _render(ctx, params):                              # pragma: no cover
            pass


def test_a_group_with_no_finite_value_is_dropped_rather_than_drawn_empty():
    """THE mechanism that made appending the two headline entries safe.

    Without it, every archived run — inbound-off, so no yard, and most of them older than
    `work_events` — gets two labelled panels with no bars in them, and a reader concludes
    "measured, and it was nothing" rather than "this run cannot answer that". That is the
    era gate's own failure mode arriving one layer below where the gate can see it, and
    this figure survives its optional quantities on purpose, so nothing else refuses on
    its behalf.
    """
    from Optimization.Performance_Evaluations.headline.top_vs_baseline import _drawable
    nan = float('nan')
    keep, dropped = _drawable({'Task makespan': [1.0, -2.0],
                               'Total production time': [nan, nan],
                               'Yard overage': [nan, nan]})
    assert dropped == ['Total production time', 'Yard overage']
    assert list(keep) == ['Task makespan']
    # A group with even ONE finite arm survives — a quantity the run CAN answer for some
    # arms is a comparison, not an absence, and dropping it would hide a real number.
    keep2, dropped2 = _drawable({'Yard overage': [nan, 3.0]})
    assert not dropped2 and list(keep2) == ['Yard overage']


def test_an_optional_quantity_still_feeds_the_view_derivation():
    """It is DRAWN, so it must be drawable — the permission is about the era gate alone."""
    ev = EVAL_BY_KEY['headline.top_vs_baseline']
    for key in ev.quantities_optional:
        views = Q.derive_views(Q.BY_KEY[key], Q.SHAPE_BY_NAME['ranked'])
        assert views <= set(ev.views) | {'absolute'}, (
            f'{key} would need a view the headline does not carry')
