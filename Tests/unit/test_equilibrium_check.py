"""test_equilibrium_check.py — the calibrated era's equilibrium check CAN FAIL, clause by
clause, and reads its expectations off the staffing record.

`Optimization/simconfig/equilibrium.py` is one pure function with two callers (the
reference-run driver's precondition, the throughput audit's report).  Every test here proves
a clause fails on exactly the synthetic window it was declared to fail on
(.scratch/department-calibration, "Declare the equilibrium bands", decision 4): a capped day,
a nonzero `released_late` on a drained day (which RAISES, not fails), a department out of
band, a trending missed share -- plus the passing window each is a one-row perturbation of,
so no test passes vacuously.  The last block round-trips a real sim DB through the
persistence loaders the `check` wrapper uses.

Run:  python -m pytest Tests/unit/test_equilibrium_check.py -q
"""
from __future__ import annotations

from dataclasses import MISSING, fields

import pytest

from Optimization.simconfig import equilibrium as eq

S = 28800.0
TOL = 0.10

# ── a window that passes, and the one-row perturbations that fail it ─────────────────


def _expectations(**over):
    base = {'pair': 'p', 'channel': 'store', 'day_seconds': S, 'band_tol': TOL,
            'departments': {'pick': {'crew': 2, 'expected': 0.50},
                            'put': {'crew': 1, 'expected': 0.30},
                            'recv': {'crew': 1, 'expected': 0.20}},
            'absent': {},
            'flags': {'calibration_stale': False, 'calibration_measured': False,
                      'k_max_exceeded': False, 'saturated': False}}
    base.update(over)
    return base


def _shift(day, *, drained=True, cap=None, end=None, last=None, standing=0):
    cap = S * (day + 1) if cap is None else cap
    end = (cap - 1000.0 if drained else cap) if end is None else end
    last = end if last is None else last
    return {'day': day, 'cap_end': cap, 'end_s': end, 'drained': int(drained),
            'standing': standing, 'standing_put': standing, 'standing_dock': 0,
            'standing_carry': 0, 'last_finish': last}


def _batch(day, *, makespan, items, demanded, late=0.0, batch_id=None):
    return {'batch_id': day if batch_id is None else batch_id, 'work_day': day,
            'task_makespan': makespan, 'total_items': items, 'items_demanded': demanded,
            'released_late': late}


def _work(batch_id, role, seconds):
    return {'batch_id': batch_id, 'role': role, 'seconds': seconds, 'n_rows': 1,
            'n_timed': 1, 'units': 1}


def _window(days=range(0, 6), *, pick_s=None, put_s=None, recv_s=None, missed=0.05):
    """A drained window at exactly the expected utilizations: pick 0.50 of 2×S, put 0.30
    of 1×S, receiving 0.20 of 1×S, missed share `missed` every day."""
    pick_s = 0.50 * 2 * S if pick_s is None else pick_s
    put_s = 0.30 * S if put_s is None else put_s
    recv_s = 0.20 * S if recv_s is None else recv_s
    shift = [_shift(d) for d in days]
    batch = [_batch(d, makespan=pick_s, items=int(1000 * (1 - missed)), demanded=1000)
             for d in days]
    work = [_work(d, 'put', put_s) for d in days] + [_work(d, 'receive', recv_s) for d in days]
    return shift, batch, work


def _check(shift, batch, work, lo=0, hi=5, exp=None):
    return eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work,
                         day_lo=lo, day_hi=hi, expectations=exp or _expectations())


def test_the_reference_window_passes_and_every_clause_reports_its_reading():
    v = _check(*_window())
    assert v.passed and v.reasons == []
    assert tuple(v.clauses) == eq.CLAUSES
    d = v.clauses['drained'].reading
    assert (d['days'], d['drained'], d['capped'], d['missing']) == (6, 6, [], [])
    u = v.clauses['utilization'].reading
    for dept, exp in (('pick', 0.50), ('put', 0.30), ('recv', 0.20)):
        assert u[dept]['realized'] == pytest.approx(exp) and u[dept]['in_band']
    m = v.clauses['missed_share'].reading
    assert m['level'] == pytest.approx(0.05) and m['trend'] == pytest.approx(0.0)
    assert v.as_dict()['clauses']['utilization']['reading']['pick']['crew'] == 2
    assert 'PASS' in eq.summarize(v)


def test_one_capped_day_in_twenty_fails_the_drained_clause_and_names_it():
    shift, batch, work = _window(range(0, 20))
    shift[7] = _shift(7, drained=False, standing=40)
    v = _check(shift, batch, work, 0, 19)
    assert not v.passed
    assert not v.clauses['drained'].passed
    assert v.clauses['drained'].reading['capped'] == [7]
    assert 'declared throughput not delivered' in v.clauses['drained'].reason
    assert all(c.passed for k, c in v.clauses.items() if k != 'drained'), \
        'only the drained clause fails on a capped day the crews still worked in band'


def test_a_day_the_ledger_never_closed_fails_rather_than_passing_vacuously():
    shift, batch, work = _window()
    del shift[3]
    v = _check(shift, batch, work)
    assert not v.passed and v.clauses['drained'].reading['missing'] == [3]
    assert 'never closed out' in v.clauses['drained'].reason


def test_a_late_release_behind_a_drained_day_raises_as_an_instrument_bug():
    shift, batch, work = _window()
    batch[2] = _batch(2, makespan=0.50 * 2 * S, items=950, demanded=1000, late=12.5)
    with pytest.raises(eq.InstrumentError, match='day 1 is recorded DRAINED'):
        _check(shift, batch, work)


def test_a_late_release_on_the_first_day_raises_nothing_could_overrun_it():
    shift, batch, work = _window()
    batch[0] = _batch(0, makespan=0.50 * 2 * S, items=950, demanded=1000, late=3.0)
    with pytest.raises(eq.InstrumentError, match='first day of the run'):
        _check(shift, batch, work)


def test_a_late_release_behind_a_capped_day_is_that_days_overrun_and_does_not_raise():
    """Measured on the first era smoke run: a capped day 1 left 69.9 s of lag on the
    batch released into day 2, which then DRAINED.  The lag belongs to day 1."""
    shift, batch, work = _window()
    shift[1] = _shift(1, drained=False)
    batch[2] = _batch(2, makespan=0.50 * 2 * S, items=950, demanded=1000, late=69.9)
    v = _check(shift, batch, work)
    assert not v.passed                                    # the capped day 1 fails it
    assert v.clauses['drained'].reading['capped'] == [1]
    assert v.clauses['released_late'].passed
    assert v.clauses['released_late'].reading['lag_s_behind_capped_days'] == {2: 69.9}


def test_a_department_outside_the_band_fails_the_utilization_clause():
    # put-away worked 0.45 of its grant against an expected 0.30: |delta| = 0.15 > 0.10
    v = _check(*_window(put_s=0.45 * S))
    assert not v.passed
    u = v.clauses['utilization']
    assert not u.passed and not u.reading['put']['in_band']
    assert u.reading['put']['realized'] == pytest.approx(0.45)
    assert 'put 0.450 vs expected 0.300' in u.reason
    assert u.reading['pick']['in_band'] and u.reading['recv']['in_band']


def test_the_band_is_around_the_expected_value_not_around_rho():
    """A single-channel leaf's share of a site crew sits far below ρ = 0.85 by
    construction; a window realizing exactly its EXPECTED 0.20 passes."""
    v = _check(*_window(recv_s=0.20 * S))
    assert v.clauses['utilization'].reading['recv']['expected'] == 0.20
    assert v.passed


def test_utilization_is_a_ratio_of_sums_over_the_window_never_a_mean_of_days():
    """Two days at 0.90 and four at 0.30 average 0.50 per day AND as a ratio of sums
    (equal grants), but with a day missing its batch the two diverge: the ratio of sums
    charges the whole grant, a mean of per-day ratios would not."""
    shift, batch, work = _window()
    batch = [b for b in batch if b['work_day'] != 5]        # day 5 drained with no batch
    v = _check(shift, batch, work)
    r = v.clauses['utilization'].reading['pick']
    assert r['granted_s'] == pytest.approx(2 * S * 6)
    assert r['realized'] == pytest.approx(0.50 * 5 / 6)


def test_a_trending_missed_share_fails_and_the_level_is_recorded_not_gated():
    shift, batch, work = _window()
    for b in batch[3:]:                                    # second half misses 8% more
        b['total_items'] = 870
    v = _check(shift, batch, work)
    m = v.clauses['missed_share']
    assert not m.passed and m.reading['trend'] == pytest.approx(0.08)
    assert m.reading['level'] == pytest.approx((3 * 0.05 + 3 * 0.13) / 6)
    assert 'trending' in m.reason
    # a HIGH but stable missed share passes: the level is never gated
    v2 = _check(*_window(missed=0.30))
    assert v2.clauses['missed_share'].passed
    assert v2.clauses['missed_share'].reading['level'] == pytest.approx(0.30)


def test_an_absent_department_is_not_gated_and_says_why():
    exp = _expectations()
    del exp['departments']['recv']
    exp['absent']['recv'] = 'no site crew derived'
    v = _check(*_window(recv_s=0.0), exp=exp)
    assert v.passed
    assert v.clauses['utilization'].reading['recv'] == {'absent': 'no site crew derived'}


def test_an_empty_window_is_refused():
    with pytest.raises(ValueError, match='empty window'):
        _check(*_window(), lo=4, hi=3)


# ── the expectations come off the staffing record, keyed by pair and channel ────────


def _staffing(*, pickers=3, put_crew=2, recv_crew=1, stale=False):
    return {
        'inputs': {'store_pickers': pickers, 'ff_pickers': 2, 'band_tol': 0.10},
        'derived': {'pairA': {
            'day_seconds': S,
            'channels': {
                'store': {'pickers': pickers, 'expected_utilization': {'pick': 0.71},
                          'batch': {'saturated': True}},
                'fulfillment': None,
            },
            'put': {'crew': put_crew, 'expected_utilization': {'store': 0.33}},
            'receiving': {'crew': recv_crew, 'expected_utilization': {'store': 0.12}},
            'k_max_exceeded': {'store': {'declared': pickers, 'k_max': 2}},
        }},
        'calibration': {'pairA': {'calibration_stale': stale, 'calibration_measured': stale}},
    }


def test_expectations_are_read_per_department_off_the_derived_block():
    e = eq.expectations_for(_staffing(), pair='pairA', channel='store')
    assert e['day_seconds'] == S and e['band_tol'] == 0.10
    assert e['departments'] == {'pick': {'crew': 3, 'expected': 0.71},
                                'put': {'crew': 2, 'expected': 0.33},
                                'recv': {'crew': 1, 'expected': 0.12}}
    assert e['flags'] == {'calibration_stale': False, 'calibration_measured': False,
                          'k_max_exceeded': True, 'saturated': True}
    # a store-only run passes channel=None and lands on the store section
    assert eq.expectations_for(_staffing(), pair='pairA', channel=None)['channel'] == 'store'


def test_a_department_with_no_crew_is_absent_not_expected_at_zero():
    e = eq.expectations_for(_staffing(recv_crew=0), pair='pairA', channel='store')
    assert 'recv' not in e['departments']
    assert e['absent'] == {'recv': 'no site crew derived'}


def test_a_record_without_the_pair_or_the_channel_is_a_record_error():
    with pytest.raises(eq.RecordError, match='no derived block for pair'):
        eq.expectations_for(_staffing(), pair='pairB', channel='store')
    with pytest.raises(eq.RecordError, match="no 'fulfillment' channel section"):
        eq.expectations_for(_staffing(), pair='pairA', channel='fulfillment')
    with pytest.raises(eq.RecordError, match='not a calibrated-era run'):
        eq.expectations_for({'inputs': {'store_pickers': 25}}, pair='pairA', channel='store')


# ── the wrapper reads a real sim DB through the persistence loaders ──────────────────


def _fresh_db(tmp_path):
    from Optimization.persistence.Picking_Data import create_run, init_run_db
    db = str(tmp_path / 'sim_fifo.db')
    init_run_db(db)
    run_id = create_run(db, 'test', {'num_pickers': 2, 'x_speed': 1.0, 'y_speed': 1.0,
                                     'pick_intercept': 1.0, 'pick_weight_coef': 0.0,
                                     'pick_volume_coef': 0.0, 'cart_swap_coef': 0.0,
                                     'k_pickers': 2, 'n_batches': 3, 'seed_world': 1,
                                     'keyframe_interval': 0, 'optimal_sigma_fd': 0.0,
                                     'optimal_work': 0.0})
    return db, run_id


def _bs(run_id, batch_id, *, day, makespan, items, demanded, late=0.0):
    from Optimization.persistence.Picking_Data import BatchStats
    kw = {f.name: 0 for f in fields(BatchStats)
          if f.default is MISSING and f.default_factory is MISSING}
    kw.update(run_id=run_id, batch_id=batch_id, duration=makespan / 2, num_tasks=1,
              total_items=items, task_makespan=makespan, avg_concurrent_pickers=2.0,
              picking_pct=0.5, traveling_pct=0.5, items_demanded=demanded, work_day=day,
              released_late=late)
    return BatchStats(**kw)


def _we(batch_id, seq, role, qty, duration):
    """One `work_events` row in `_WORK_EVENT_COLS` order."""
    return (batch_id, seq, 100.0 * seq, 100.0 * seq, 0, 7, 0, role, 'foot', role, 1, 5,
            qty, duration, 'reorder')


def test_check_reads_the_three_sources_off_a_sim_db(tmp_path):
    from Optimization.persistence.Picking_Data import (
        load_shift_days, load_work_hours, save_checkpoint_bundle, save_shift_days)
    db, run_id = _fresh_db(tmp_path)
    batches = [_bs(run_id, d, day=d, makespan=0.50 * 2 * S, items=950, demanded=1000)
               for d in range(3)]
    work = [_we(d, 2 * d, 'put', 4, 0.30 * S) for d in range(3)] + \
           [_we(d, 2 * d + 1, 'receive', 12, 0.20 * S) for d in range(3)]
    save_checkpoint_bundle(db, run_id, batch_stats=batches, task_stats=[],
                           picker_events=[], picks=[], bin_placements=[],
                           bin_evictions=[], aisle_metrics=[], reorder_queue=[],
                           work_events=work,
                           shift_days=[(0, S, S - 500, True, 0, 0, 0, 0, S - 500),
                                       (1, 2 * S, 2 * S - 500, True, 0, 0, 0, 0, 2 * S - 500)])
    save_shift_days(db, run_id, [(2, 3 * S, 3 * S, False, 9, 9, 0, 0, 3 * S + 40)])
    # the fold now carries units: Σ qty per (batch, role), the reference run's denominator
    rows = load_work_hours(db, run_id)
    assert {(r['role'], r['units']) for r in rows if r['batch_id'] == 0} == \
        {('put', 4), ('receive', 12)}
    v = eq.check(db, run_id, 0, 1, expectations=_expectations())
    assert v.passed, v.reasons
    assert v.clauses['utilization'].reading['put']['realized'] == pytest.approx(0.30)
    assert v.clauses['utilization'].reading['recv']['realized'] == pytest.approx(0.20)
    # widen the window over the capped final day and it fails on that day, with overtime
    v3 = eq.check(db, run_id, 0, 2, expectations=_expectations())
    assert not v3.passed and v3.clauses['drained'].reading['capped'] == [2]
    assert v3.clauses['drained'].reading['overtime_days'] == [2]
    assert eq.window_of(load_shift_days(db, run_id)) == (0, 2)


def test_a_run_that_never_closed_a_day_fails_on_the_ledger_not_on_a_plausible_pass(tmp_path):
    db, run_id = _fresh_db(tmp_path)
    v = eq.check(db, run_id, 0, 0, expectations=_expectations())
    assert not v.passed and v.clauses['drained'].reading['missing'] == [0]
    assert eq.window_of([]) is None
