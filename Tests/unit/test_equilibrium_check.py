"""test_equilibrium_check.py — the calibrated era's equilibrium check CAN FAIL, clause by
clause, and reads its expectations off the staffing record.

`Optimization/simconfig/equilibrium.py` is one pure function with two callers (the throughput
audit's report, the equilibrium report CLI).  Every test here proves a clause fails on
exactly the synthetic window it was declared to fail on (.scratch/department-calibration,
"Declare the equilibrium bands", decision 4, and "Split the missed-share clause into supply
and labour", 2026-09-09): a day the ledger never closed, a nonzero `released_late` on a
drained day (which RAISES, not fails), a department out of band, a supply share off its
stamped level or trending, a cut share off its stamped expectation, a labour carry past a
day's capacity -- plus the passing window each is a one-row perturbation of, so no test
passes vacuously.  The sabotage pair pins the split itself: a pure-overflow series passes
`supply` and fails `labour`; a pure-stockout series passes `labour` and fails `supply`.
The last block round-trips a real sim DB through the persistence loaders the `check`
wrapper uses.

Run:  python -m pytest Tests/unit/test_equilibrium_check.py -q
"""
from __future__ import annotations

from dataclasses import MISSING, fields

import math

import pytest

from Optimization.simconfig import equilibrium as eq
from Optimization.simconfig import staffing as st

S = 28800.0
TOL = 0.10
FRESH = 1000          # fresh demand per day in every synthetic window

# ── a window that passes, and the one-row perturbations that fail it ─────────────────


def _expectations(**over):
    base = {'pair': 'p', 'channel': 'store', 'day_seconds': S, 'band_tol': TOL,
            'departments': {'pick': {'crew': 2, 'expected': 0.50, 's_pick': None},
                            'put': {'crew': 1, 'expected': 0.30},
                            'recv': {'crew': 1, 'expected': 0.20}},
            'absent': {},
            'flags': {'overridden': False, 'saturated': False}}
    base.update(over)
    return base


def _shift(day, *, drained=True, cap=None, end=None, last=None, standing=0, labour=0,
           supply=0):
    """One ledger row.  `standing` is put-queue units; `labour` / `supply` are the two
    halves of the carry (the cut's, the shelf's).  `drained` is the ROW'S verdict, as the
    runner wrote it -- the clause reads it, never re-derives it."""
    cap = S * (day + 1) if cap is None else cap
    end = (cap - 1000.0 if drained else cap) if end is None else end
    last = end if last is None else last
    return {'day': day, 'cap_end': cap, 'end_s': end, 'drained': int(drained),
            'standing': standing + labour + supply, 'standing_put': standing,
            'standing_dock': 0, 'standing_carry': labour + supply,
            'standing_carry_labour': labour, 'standing_carry_supply': supply,
            'last_finish': last}


def _batch(day, *, makespan, items, demanded, late=0.0, batch_id=None):
    return {'batch_id': day if batch_id is None else batch_id, 'work_day': day,
            'task_makespan': makespan, 'total_items': items, 'items_demanded': demanded,
            'released_late': late}


def _work(batch_id, role, seconds):
    return {'batch_id': batch_id, 'role': role, 'seconds': seconds, 'n_rows': 1,
            'n_timed': 1, 'units': 1}


def _carry(batch_id, reason, sku, qty):
    return {'batch_id': batch_id, 'reason': reason, 'sku': sku, 'qty': qty}


def _per_day(value, days):
    return list(value) if isinstance(value, (list, tuple)) else [value] * len(days)


def _window(days=range(0, 6), *, pick_s=None, put_s=None, recv_s=None, missed=0.05,
            cut=0.0):
    """A drained window at exactly the expected utilizations: pick 0.50 of 2×S, put 0.30
    of 1×S, receiving 0.20 of 1×S.  Each day asks for FRESH fresh units; a `missed` share
    is stocked out on a NEW SKU each day (so every stockout unit counts once) and a `cut`
    share is left by the whistle on SKU 7.  Both roll into the next day's batch and are
    served there, so `items_demanded` is FRESH plus the previous day's carry -- the
    effective batch, as the runner writes it.  `missed` / `cut` take a per-day list."""
    days = list(days)
    pick_s = 0.50 * 2 * S if pick_s is None else pick_s
    put_s = 0.30 * S if put_s is None else put_s
    recv_s = 0.20 * S if recv_s is None else recv_s
    missed, cut = _per_day(missed, days), _per_day(cut, days)
    shift = [_shift(d) for d in days]
    batch, carry = [], []
    prev = 0
    for k, d in enumerate(days):
        sup, cutq = int(FRESH * missed[k]), int(FRESH * cut[k])
        demanded = FRESH + prev
        batch.append(_batch(d, makespan=pick_s, items=demanded - sup - cutq,
                            demanded=demanded))
        if sup:
            carry.append(_carry(d, 'unpicked_unstocked', 100 + d, sup))
        if cutq:
            carry.append(_carry(d, 'unpicked_daycut', 7, cutq))
        prev = sup + cutq
    work = [_work(d, 'put', put_s) for d in days] + [_work(d, 'receive', recv_s) for d in days]
    return shift, batch, work, carry


def _check(shift, batch, work, carry, lo=0, hi=5, exp=None):
    return eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work,
                         carry_rows=carry, day_lo=lo, day_hi=hi,
                         expectations=exp or _expectations())


def test_the_reference_window_passes_and_every_clause_reports_its_reading():
    v = _check(*_window())
    assert v.passed and v.reasons == []
    assert tuple(v.clauses) == eq.CLAUSES == ('labour', 'released_late', 'utilization',
                                              'supply', 'rework')
    lab = v.clauses['labour'].reading
    d = lab['drained']
    assert (d['days'], d['drained'], d['capped'], d['missing']) == (6, 6, [], [])
    assert lab['level'] == 0.0 and lab['in_band'] is None        # no guarantee stamped
    assert lab['carry_bounded'] is None                          # no s_pick to price a cap
    u = v.clauses['utilization'].reading
    for dept, exp in (('pick', 0.50), ('put', 0.30), ('recv', 0.20)):
        assert u[dept]['realized'] == pytest.approx(exp) and u[dept]['in_band']
    m = v.clauses['supply'].reading
    assert m['level'] == pytest.approx(0.05) and m['trend'] == pytest.approx(0.0)
    assert m['units'] == {'fresh': 6000, 'demanded': 6250, 'supply': 300, 'supply_new': 300,
                          'reattempts': 0}
    assert v.as_dict()['clauses']['utilization']['reading']['pick']['crew'] == 2
    assert 'PASS' in eq.summarize(v)


def test_a_capped_day_is_a_reading_never_a_failed_clause():
    """Decision 9: with a declared day cv of a third, 14 of 40 days exceed a full shift at
    any headroom, so "every day drained" is not a property of equilibrium.  One capped day
    in twenty is recorded, named in the summary, and fails nothing."""
    shift, batch, work, carry = _window(range(0, 20))
    shift[7] = _shift(7, drained=False, standing=40)
    v = _check(shift, batch, work, carry, 0, 19)
    assert v.passed, v.reasons
    d = v.clauses['labour'].reading['drained']
    assert d['capped'] == [7] and d['drained'] == 19
    assert '1 capped' in eq.summarize(v)


def test_a_day_the_ledger_never_closed_fails_rather_than_passing_vacuously():
    shift, batch, work, carry = _window()
    del shift[3]
    v = _check(shift, batch, work, carry)
    assert not v.passed
    lab = v.clauses['labour']
    assert not lab.passed and lab.reading['drained']['missing'] == [3]
    assert 'never closed out' in lab.reason
    assert all(c.passed for k, c in v.clauses.items() if k != 'labour')


# ── the drained verdict is LABOUR-ONLY ("Choose the coverage floor", decision 7) ─────


def test_is_drained_reads_labour_only_and_never_the_supply_carry():
    import inspect
    # a crew that realized every task drained its day, whatever the shelf held
    assert eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=0,
                         overtime=False)
    # each labour term, ALONE, caps the day -- overtime included: labour that did not fit it
    assert not eq.is_drained(cut=True, standing_put=0, standing_dock=0, standing_carry_labour=0,
                             overtime=False)
    assert not eq.is_drained(cut=False, standing_put=1, standing_dock=0, standing_carry_labour=0,
                             overtime=False)
    assert not eq.is_drained(cut=False, standing_put=0, standing_dock=1, standing_carry_labour=0,
                             overtime=False)
    assert not eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=1,
                             overtime=False)
    assert not eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=0,
                             overtime=True)
    # the supply carry is not even an argument: there is nothing to pass that could cap a day;
    # overtime is one, and a caller must state it (no default to inherit the old verdict)
    params = inspect.signature(eq.is_drained).parameters
    assert 'standing_carry_supply' not in params and 'standing_carry' not in params
    assert params['overtime'].default is inspect.Parameter.empty


def test_a_day_with_supply_carry_only_is_drained_and_the_reading_says_so():
    shift, batch, work, carry = _window()
    # day 2 closed with 40 units the shelf could not serve rolling forward, and nothing else
    verdict = eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=0,
                            overtime=False)
    shift[2] = _shift(2, drained=verdict, supply=40)
    assert shift[2]['standing'] == 40, 'the mixed level still counts it'
    v = _check(shift, batch, work, carry)
    assert v.passed, v.reasons
    r = v.clauses['labour'].reading['drained']
    assert r['capped'] == [] and r['drained'] == 6
    assert r['supply_standing_days'] == [2] and r['supply_standing_max_units'] == 40
    assert r['supply_split_recorded'] is True
    assert '1 with supply carry standing' in eq.summarize(v)


def test_a_day_with_daycut_carry_only_is_capped_and_the_ledger_level_rides_the_reading():
    shift, batch, work, carry = _window()
    verdict = eq.is_drained(cut=True, standing_put=0, standing_dock=0, standing_carry_labour=15,
                            overtime=False)
    shift[2] = _shift(2, drained=verdict, labour=15)
    v = _check(shift, batch, work, carry)
    r = v.clauses['labour'].reading['drained']
    assert r['capped'] == [2] and r['supply_standing_days'] == []
    assert r['labour_standing_max_units'] == 15      # the ledger's LEVEL, a cross-check


# ── overtime caps a day ("Overtime behind a drained day raises the instrument") ─────────


def test_a_day_that_ended_at_its_cap_with_a_late_last_finish_is_capped_not_drained():
    """The store leaf of the line-floor check, day 3: ended AT the cap, the last picker
    finished 138 s past it, nothing standing.  Labour that did not fit the day."""
    shift, batch, work, carry = _window()
    late = eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=0,
                         overtime=True)
    assert late is False
    shift[1] = _shift(1, drained=late, end=2 * S, last=2 * S + 138.0)
    batch[2]['released_late'] = 138.0
    v = _check(shift, batch, work, carry)
    r = v.clauses['labour'].reading['drained']
    assert r['capped'] == [1] and r['overtime_days'] == [1]
    # named apart from a day that left work standing: this one delivered everything, late
    assert r['overtime_only_days'] == [1]
    assert '1 capped (1 by overtime alone)' in eq.summarize(v)
    # overtime with labour standing is not "overtime alone"
    shift[1] = _shift(1, drained=False, end=2 * S, last=2 * S + 138.0, standing=12)
    r2 = _check(shift, batch, work, carry).clauses['labour'].reading['drained']
    assert r2['overtime_days'] == [1] and r2['overtime_only_days'] == []
    # the lag on day 2's release is day 1's overrun, recorded -- not an instrument bug
    assert v.clauses['released_late'].passed
    assert v.clauses['released_late'].reading['lag_s_behind_capped_days'] == {2: 138.0}


def test_a_day_that_ended_early_with_nothing_standing_is_drained():
    early = eq.is_drained(cut=False, standing_put=0, standing_dock=0, standing_carry_labour=0,
                          overtime=False)
    assert early is True
    shift, batch, work, carry = _window()
    shift[1] = _shift(1, drained=early, end=2 * S - 900.0, last=2 * S - 900.0)
    v = _check(shift, batch, work, carry)
    assert v.passed, v.reasons
    r = v.clauses['labour'].reading['drained']
    assert r['overtime_days'] == [] and 1 in r['drained_early_days']


def test_the_pre_amendment_stamp_is_the_contradiction_the_released_late_clause_raises_on():
    """What the store leaf's ledger said before the amendment: day 1 stamped DRAINED with
    a finish past its cap, and 138 s of lag on the batch released into day 2.  Handed to the
    check RAW (the loaders fold the overtime term in; this bypasses them) it raises -- the
    instrument bug the clause exists to catch, and why the term belongs in the verdict."""
    shift, batch, work, carry = _window()
    shift[1] = _shift(1, drained=True, end=2 * S, last=2 * S + 138.0)
    batch[2]['released_late'] = 138.0
    with pytest.raises(eq.InstrumentError, match='day 1 is recorded DRAINED'):
        _check(shift, batch, work, carry)


def test_a_pre_split_ledger_reads_as_no_split_recorded_and_its_verdicts_stand():
    """487a65bf83a9's rows carry the halves as NULL (None off the loader, NaN off a frame);
    the reading reports the split as unrecorded and the row's own `drained` stands."""
    shift, batch, work, carry = _window()
    for k, row in enumerate(shift):
        row['standing_carry_labour'] = None if k < 3 else float('nan')
        row['standing_carry_supply'] = None if k < 3 else float('nan')
    v = _check(shift, batch, work, carry)
    assert v.passed, v.reasons
    r = v.clauses['labour'].reading['drained']
    assert r['supply_split_recorded'] is False and r['supply_standing_days'] == []
    assert r['supply_standing_max_units'] == 0 and r['labour_standing_max_units'] == 0


def test_a_late_release_behind_a_drained_day_raises_as_an_instrument_bug():
    shift, batch, work, carry = _window()
    batch[2]['released_late'] = 12.5
    with pytest.raises(eq.InstrumentError, match='day 1 is recorded DRAINED'):
        _check(shift, batch, work, carry)


def test_a_late_release_on_the_first_day_raises_nothing_could_overrun_it():
    shift, batch, work, carry = _window()
    batch[0]['released_late'] = 3.0
    with pytest.raises(eq.InstrumentError, match='first day of the run'):
        _check(shift, batch, work, carry)


def test_a_late_release_behind_a_capped_day_is_that_days_overrun_and_does_not_raise():
    """Measured on the first era smoke run: a capped day 1 left 69.9 s of lag on the
    batch released into day 2, which then DRAINED.  The lag belongs to day 1."""
    shift, batch, work, carry = _window()
    shift[1] = _shift(1, drained=False)
    batch[2]['released_late'] = 69.9
    v = _check(shift, batch, work, carry)
    assert v.passed, v.reasons                          # a capped day is a reading
    assert v.clauses['labour'].reading['drained']['capped'] == [1]
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
    shift, batch, work, carry = _window()
    batch = [b for b in batch if b['work_day'] != 5]        # day 5 drained with no batch
    v = _check(shift, batch, work, carry)
    r = v.clauses['utilization'].reading['pick']
    assert r['granted_s'] == pytest.approx(2 * S * 6)
    assert r['realized'] == pytest.approx(0.50 * 5 / 6)


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


# ── the flows: fresh demand recovered from the carry, re-attempts counted once ─────────


def test_fresh_demand_is_the_effective_batch_less_the_previous_carry():
    _shift_, batch, _work_, carry = _window(missed=0.05, cut=0.10)
    flows = eq.demand_flows(batch, carry)
    assert flows[0] == {'batch_id': 0, 'work_day': 0, 'demanded': 1000, 'carry_in': 0,
                        'fresh': 1000, 'picked': 850, 'cut': 100, 'supply': 50,
                        'supply_new': 50, 'notasks': 0, 'recorded': True}
    # day 1 was handed day 0's 150 carried units on top of its own 1000
    assert flows[1]['demanded'] == 1150 and flows[1]['carry_in'] == 150
    assert flows[1]['fresh'] == 1000 and flows[1]['picked'] == 1000


def test_a_re_attempted_stockout_is_counted_once_and_the_share_is_over_fresh_demand():
    """SKU 9 is out for three days: 50 fresh units fail on day 0, 100 on day 1 (the 50
    re-offered plus 50 fresh), 150 on day 2, and the shelf is back on day 3.  The raw
    supply flow is 300; the first-attempt count is 150 -- three days of 50 fresh units --
    and the level is over the 6,000 fresh units, not the 6,300 effective ones.  The old
    clause would have read `(demanded - picked) / demanded` = 300 / 6,300 with every
    re-attempt counted twice."""
    shift, batch, work, _ = _window(missed=0.0)
    carry = [_carry(0, 'unpicked_unstocked', 9, 50),
             _carry(1, 'unpicked_unstocked', 9, 100),
             _carry(2, 'unpicked_unstocked', 9, 150)]
    for b, (dem, got) in zip(batch, [(1000, 950), (1050, 950), (1100, 950), (1150, 1150),
                                      (1000, 1000), (1000, 1000)]):
        b['items_demanded'], b['total_items'] = dem, got
    v = _check(shift, batch, work, carry)
    m = v.clauses['supply'].reading
    assert m['units'] == {'fresh': 6000, 'demanded': 6300, 'supply': 300, 'supply_new': 150,
                          'reattempts': 150}
    assert m['level'] == pytest.approx(150 / 6000)
    assert '150 re-attempt unit(s) counted once' in eq.summarize(v)


def test_a_carry_larger_than_the_stated_demand_is_an_instrument_error():
    shift, batch, work, carry = _window()
    batch[1]['items_demanded'] = 10                     # yet day 0 rolled 50 units into it
    with pytest.raises(eq.InstrumentError, match='cannot exceed the effective demand'):
        _check(shift, batch, work, carry)


def test_unrecorded_flows_fail_both_clauses_rather_than_reading_as_everything_served():
    """No carryover rows and a demand-pick gap: a pre-`carryover` vintage, not a perfect
    run.  Both flow clauses say so instead of passing at zero."""
    shift, batch, work, _ = _window()
    v = _check(shift, batch, work, [])
    assert not v.passed
    for name in ('supply', 'labour'):
        assert not v.clauses[name].passed and 'unrecorded' in v.clauses[name].reason
    # no rows AND no gap: genuinely nothing missed, and the clauses pass at zero
    shift, batch, work, _ = _window(missed=0.0)
    v = _check(shift, batch, work, [])
    assert v.passed, v.reasons
    assert v.clauses['supply'].reading['level'] == 0.0
    # rows from the PUT side only (levels, never read) are not "recorded" pick flows either
    shift, batch, work, _ = _window()
    v = _check(shift, batch, work, [_carry(d, 'unplaced', 3, 500) for d in range(6)])
    assert not v.passed and 'unrecorded' in v.clauses['supply'].reason


# ── the supply clause: a level against the stamped fill, and a trend ──────────────────


def test_a_trending_supply_share_fails_and_names_the_halves():
    v = _check(*_window(missed=[0.05] * 3 + [0.13] * 3))
    m = v.clauses['supply']
    assert not m.passed and m.reading['trend'] == pytest.approx(0.08)
    assert m.reading['level'] == pytest.approx((3 * 50 + 3 * 130) / 6000)
    assert 'supply share trending' in m.reason
    assert v.clauses['labour'].passed, 'a supply trend is not a labour finding'


def test_the_supply_level_is_judged_against_the_stamped_fill_within_its_tolerance():
    # 0.050 against an expected 0.060: inside ±0.02, passes
    v = _check(*_window(missed=0.05), exp=_expectations(expected_missed_share=0.06))
    r = v.clauses['supply'].reading
    assert v.passed and r['in_band'] is True and math.isclose(r['delta'], -0.01)
    assert r['tol'] == eq.SUPPLY_LEVEL_TOL == 0.02
    # 0.050 against an expected 0.200: the shelf serves far better than the record promised,
    # which is a record that does not describe this run -- FAILS (the old clause reported it)
    v2 = _check(*_window(missed=0.05), exp=_expectations(expected_missed_share=0.20))
    assert not v2.passed and v2.clauses['supply'].reading['in_band'] is False
    assert 'supply share 0.050 vs expected 0.200' in v2.clauses['supply'].reason
    assert 'expected 0.200' in eq.summarize(v2) and '-0.150' in eq.summarize(v2)
    # no expectation on the record: reported, not judged, and the line does not pretend
    v0 = _check(*_window(missed=0.30))
    r0 = v0.clauses['supply'].reading
    assert v0.clauses['supply'].passed and r0['in_band'] is None and r0['delta'] is None
    assert 'expected' not in eq.summarize(v0)


# ── the labour clause: the cut share at its stamped expectation, carry bounded, no trend ──

CUT_SD = 0.05      # a synthetic per-day sd of the cut share; the band is 2·sd/sqrt(n)


def _labour_exp(cut_share=0.02, sd=CUT_SD, s_pick=10.0, **over):
    exp = _expectations(expected_cut_share=cut_share, expected_cut_share_sd=sd, **over)
    exp['departments']['pick']['s_pick'] = s_pick
    return exp


def test_the_cut_share_band_is_the_laws_sampling_sd_over_the_window():
    v = _check(*_window(cut=0.02), exp=_labour_exp())
    r = v.clauses['labour'].reading
    assert v.passed, v.reasons
    assert r['level'] == pytest.approx(0.02) and r['in_band'] is True
    assert r['tol'] == pytest.approx(eq.CUT_SHARE_Z * CUT_SD / math.sqrt(6))
    assert r['n_days'] == 6 and r['z'] == 2.0
    # the crew's day in units: 2 pickers × S ÷ 10 s/unit; the 20-unit carry is 0.003 of it
    assert r['cap_units'] == pytest.approx(2 * S / 10.0)
    assert r['carry_max_units'] == 20 and r['carry_bounded'] is True
    assert 'cut share 0.0200 vs expected 0.0200' in eq.summarize(v)


def test_a_cut_share_outside_the_band_fails_the_labour_clause():
    # 0.15 realized against 0.02 expected: |delta| = 0.13 > 2 × 0.05 / sqrt(6) = 0.041
    v = _check(*_window(cut=0.15), exp=_labour_exp())
    lab = v.clauses['labour']
    assert not v.passed and not lab.passed and lab.reading['in_band'] is False
    assert 'cut share 0.1500 vs expected 0.0200' in lab.reason
    assert v.clauses['supply'].passed


def test_a_labour_carry_past_a_days_capacity_fails_even_when_the_share_is_in_band():
    """s_pick = 100 s/unit makes the crew's day 576 units; a 600-unit carry on day 3 is a
    queue past a full day, whatever the window's mean share reads."""
    cut = [0.0, 0.0, 0.0, 0.6, 0.0, 0.0]
    exp = _labour_exp(cut_share=0.10, sd=0.5, s_pick=100.0)   # a band wide enough to pass
    v = _check(*_window(cut=cut), exp=exp)
    lab = v.clauses['labour']
    assert lab.reading['in_band'] is True, lab.reason
    assert not lab.passed and lab.reading['carry_bounded'] is False
    assert lab.reading['carry_max_units'] == 600 and lab.reading['carry_max_day'] == 3
    assert lab.reading['carry_max_days'] == pytest.approx(600 / 576)
    assert 'standing labour carry 600 unit(s) on day 3' in lab.reason
    assert 'carry max 600 units = 1.04 day(s)' in eq.summarize(v)


def test_a_trending_cut_share_fails_with_the_level_in_band():
    # halves 0.00 and 0.06: mean 0.03 sits on the expectation; under a declared per-day sd
    # of 0.01 the difference of two 3-day means has sd 0.0082, so 0.06 is 7 sds of drift
    v = _check(*_window(cut=[0.0] * 3 + [0.06] * 3), exp=_labour_exp(cut_share=0.03, sd=0.01))
    lab = v.clauses['labour']
    assert lab.reading['in_band'] is True and lab.reading['trend'] == pytest.approx(0.06)
    assert lab.reading['trend_tol'] == pytest.approx(2 * 0.01 * math.sqrt(1 / 3 + 1 / 3))
    assert lab.reading['trend_tol_source'] == 'declared'
    assert not lab.passed and 'cut share trending' in lab.reason
    # the same drift under a declared sd of 0.5 is noise (band 0.82) and passes
    v2 = _check(*_window(cut=[0.0] * 3 + [0.06] * 3), exp=_labour_exp(cut_share=0.03, sd=0.5))
    assert v2.clauses['labour'].passed, v2.clauses['labour'].reason


def test_the_labour_trend_band_is_the_laws_never_the_typed_supply_tolerance():
    """The review's critical finding: a per-day cut share has the day law's spread (0.06-0.12
    on the reference pair), and the typed 0.02 failed 26-52% of healthy iid windows.  Twenty
    iid days drawn at the stamped law must pass the trend term at least 95% of the time."""
    import random
    rng = random.Random(11)
    load, sd, K = 600000.0, 200000.0, 32
    exp_share = st.cut_share(load, sd, K, S)
    exp = _labour_exp(cut_share=exp_share, sd=st.cut_share_sd(load, sd, K, S), s_pick=100.0)
    days = list(range(0, 20))
    trend_fails = 0
    for _ in range(300):
        shares = [max(0.0, rng.gauss(load, sd) - K * S) / load for _ in days]
        shift, batch, work, carry = _window(days, cut=[min(s, 0.9) for s in shares])
        v = _check(shift, batch, work, carry, 0, 19, exp=exp)
        trend_fails += 'trending' in v.clauses['labour'].reason
    # a 2-sd band on a skewed overflow: a few percent expected; the typed 0.02 read 26-52%
    assert trend_fails <= 30, f'{trend_fails}/300 healthy windows failed on trend'


def test_without_a_stamped_law_the_trend_band_is_the_windows_own_within_half_spread():
    # no guarantee: the pooled within-half sd of the per-day shares prices the band ...
    cut = [0.10, 0.12, 0.08, 0.10, 0.30, 0.28, 0.32, 0.30]
    v = _check(*_window(range(0, 8), cut=cut), lo=0, hi=7)
    lab = v.clauses['labour'].reading
    assert lab['trend_tol_source'] == 'empirical'
    # two deviations of 0.02 in each half, pooled over n - 2 degrees of freedom
    assert lab['trend_sd'] == pytest.approx(math.sqrt(2 * 2 * 0.02 ** 2 / 6))
    assert lab['trend'] == pytest.approx(0.20) and lab['trend_tol'] < 0.20
    assert 'cut share trending' in v.clauses['labour'].reason
    # ... a real step with a tight spread fails; the same step inside a wide spread does not
    cut2 = [0.0, 0.40, 0.0, 0.40, 0.10, 0.50, 0.10, 0.50]
    v2 = _check(*_window(range(0, 8), cut=cut2), lo=0, hi=7)
    assert v2.clauses['labour'].reading['trend'] == pytest.approx(0.10)
    assert v2.clauses['labour'].passed, v2.clauses['labour'].reason
    # fewer than four days: nothing to pool, the typed tolerance stands in and says so
    v3 = _check(*_window(range(0, 3), cut=0.05), lo=0, hi=2)
    assert v3.clauses['labour'].reading['trend_tol_source'] == 'typed'
    assert v3.clauses['labour'].reading['trend_tol'] == eq.TREND_TOL


def test_a_spread_less_law_reports_the_level_rather_than_testing_float_equality():
    v = _check(*_window(cut=0.02), exp=_labour_exp(cut_share=0.02, sd=0.0))
    lab = v.clauses['labour'].reading
    assert lab['in_band'] is None and lab['tol'] is None and lab['level'] == pytest.approx(0.02)


def test_without_a_guarantee_the_cut_share_is_reported_not_judged():
    """The 2026-09-08 runs: a record with no guarantee block.  The level rides the reading
    and the summary, the band is None, and the clause still judges the carry and the trend."""
    v = _check(*_window(cut=0.15))
    lab = v.clauses['labour']
    assert lab.passed and lab.reading['in_band'] is None and lab.reading['tol'] is None
    assert lab.reading['level'] == pytest.approx(0.15)
    assert 'cut share 0.1500' in eq.summarize(v) and 'vs expected' not in eq.summarize(v)


# ── the sabotage pair: each clause fails on a series the other passes ──────────────────


def test_a_pure_overflow_series_passes_supply_and_fails_labour():
    """The store leaf of the 2026-09-08 check, in miniature: the shelf serves everything and
    the whistle cuts 15% -- what the old `missed_share` read as a trending supply share."""
    v = _check(*_window(missed=0.0, cut=0.15),
               exp=_labour_exp(cut_share=0.02, expected_missed_share=0.0))
    assert v.clauses['supply'].passed and v.clauses['supply'].reading['level'] == 0.0
    assert not v.clauses['labour'].passed
    assert 'cut share 0.1500 vs expected 0.0200' in v.clauses['labour'].reason


def test_a_pure_stockout_series_passes_labour_and_fails_supply():
    v = _check(*_window(missed=0.15, cut=0.0),
               exp=_labour_exp(cut_share=0.02, expected_missed_share=0.02))
    assert v.clauses['labour'].passed, v.clauses['labour'].reason
    assert v.clauses['labour'].reading['level'] == 0.0
    assert not v.clauses['supply'].passed
    assert 'supply share 0.150 vs expected 0.020' in v.clauses['supply'].reason


# ── the expectations come off the staffing record, keyed by pair and channel ────────


def _staffing(*, pickers=3, put_crew=2, recv_crew=1, overrides=(), guarantee=None):
    return {
        'inputs': {'store_pickers': pickers, 'ff_pickers': 2, 'band_tol': 0.10},
        'derived': {'pairA': {
            'day_seconds': S,
            'channels': {
                'store': {'pickers': pickers, 'expected_utilization': {'pick': 0.71},
                          'batch': {'saturated': True},
                          **({'guarantee': guarantee} if guarantee else {})},
                'fulfillment': None,
            },
            'put': {'crew': put_crew, 'expected_utilization': {'store': 0.33}},
            'receiving': {'crew': recv_crew, 'expected_utilization': {'store': 0.12}},
        }},
        'calibration': {'pairA': {'method': 'expected_travel', 'overrides': list(overrides)}},
    }


def test_expectations_are_read_per_department_off_the_derived_block():
    e = eq.expectations_for(_staffing(), pair='pairA', channel='store')
    assert e['day_seconds'] == S and e['band_tol'] == 0.10
    assert e['departments'] == {'pick': {'crew': 3, 'expected': 0.71, 's_pick': None},
                                'put': {'crew': 2, 'expected': 0.33},
                                'recv': {'crew': 1, 'expected': 0.12}}
    assert e['flags'] == {'overridden': False, 'saturated': True}
    assert e['departments']['pick']['s_pick'] is None      # this fixture stamps none
    # no guarantee on the record: the labour clause's expectation is None, not zero
    assert e['expected_cut_share'] is None and e['expected_cut_share_sd'] is None
    assert e['guarantee'] is None


def test_the_expected_cut_share_and_its_sd_come_off_the_stamped_guarantee():
    g = {'crew': {'pickers': 3, 'cut_share': 0.0203, 'cut_share_max': 0.0253,
                  'load_s': 62000.0, 'sd_s': 20000.0, 'cv': 0.32}}
    e = eq.expectations_for(_staffing(guarantee=g), pair='pairA', channel='store')
    assert e['expected_cut_share'] == 0.0203
    assert e['expected_cut_share_sd'] == pytest.approx(st.cut_share_sd(62000.0, 20000.0, 3, S))
    assert e['guarantee']['pickers'] == 3 and e['guarantee']['load_s'] == 62000.0


def test_arm_expectations_recentre_the_pick_band_and_the_cut_share_by_the_ratio_of_expected_seconds():
    pair = _expectations()
    pair['departments']['pick']['s_pick'] = 100.0
    arm = eq.arm_expectations(pair, {'s_pick': 90.0})
    assert math.isclose(arm['departments']['pick']['expected'], 0.50 * 0.9)
    assert arm['departments']['pick']['pair_expected'] == 0.50
    assert arm['departments']['put'] == pair['departments']['put']      # untouched
    assert pair['departments']['pick']['expected'] == 0.50              # the input is not mutated
    assert 'guarantee' not in arm or arm.get('guarantee') is None        # nothing to re-centre
    # with a guarantee the day's load and sd scale by the same ratio: a cheaper unit is cut less
    pair['guarantee'] = {'pickers': 2, 'load_s': 50000.0, 'sd_s': 16000.0,
                         'cut_share': st.cut_share(50000.0, 16000.0, 2, S),
                         'cut_share_sd': st.cut_share_sd(50000.0, 16000.0, 2, S)}
    pair['expected_cut_share'] = pair['guarantee']['cut_share']
    arm = eq.arm_expectations(pair, {'s_pick': 90.0})
    assert arm['expected_cut_share'] == pytest.approx(st.cut_share(45000.0, 14400.0, 2, S))
    assert arm['expected_cut_share_sd'] == pytest.approx(st.cut_share_sd(45000.0, 14400.0, 2, S))
    assert arm['expected_cut_share'] < pair['expected_cut_share']
    assert arm['guarantee']['pair_cut_share'] == pair['guarantee']['cut_share']
    assert pair['guarantee']['load_s'] == 50000.0                        # not mutated
    # nothing stamped, or no pair s_pick to scale from: the pair's expectations, unchanged
    assert eq.arm_expectations(pair, None) is pair
    assert eq.arm_expectations(_expectations(), {'s_pick': 90.0})['departments']['pick']['expected'] == 0.50
    over = eq.expectations_for(_staffing(overrides=('s_put',)), pair='pairA', channel='store')
    assert over['flags']['overridden'] is True
    # a store-only run passes channel=None and lands on the store section
    assert eq.expectations_for(_staffing(), pair='pairA', channel=None)['channel'] == 'store'


def test_a_department_with_no_crew_is_absent_not_expected_at_zero():
    e = eq.expectations_for(_staffing(recv_crew=0), pair='pairA', channel='store')
    assert 'recv' not in e['departments']
    assert e['absent'] == {'recv': 'no site crew derived'}


# ── the stamped fill rate: the supply level is read against 1 - fill ("Build the line floor") ──


def test_the_expected_supply_share_comes_off_the_stamped_fill_rate_or_is_none():
    rec = _staffing()
    e = eq.expectations_for(rec, pair='pairA', channel='store')
    assert e['fill_rate'] is None and e['expected_missed_share'] is None   # pre-floor record
    rec['calibration']['pairA']['coverage'] = {
        'final': {'store': {'fill': {'fill_rate': 0.93, 'expected_missed_share': 0.07}}}}
    e = eq.expectations_for(rec, pair='pairA', channel='store')
    assert e['fill_rate'] == 0.93 and math.isclose(e['expected_missed_share'], 0.07)
    # another channel's stamp is not this leaf's
    with pytest.raises(eq.RecordError):
        eq.expectations_for(rec, pair='pairA', channel='fulfillment')


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


def test_check_reads_the_four_sources_off_a_sim_db(tmp_path):
    from Optimization.persistence.Picking_Data import (
        load_carryover, load_shift_days, load_work_hours, save_checkpoint_bundle,
        save_shift_days)
    db, run_id = _fresh_db(tmp_path)
    # 50 units stocked out each day on a new SKU, re-offered and served the next day
    batches = [_bs(run_id, d, day=d, makespan=0.50 * 2 * S, items=950 + (50 if d else 0),
                   demanded=1000 + (50 if d else 0)) for d in range(3)]
    carry = [(d, 'unpicked_unstocked', 100 + d, 50) for d in range(3)]
    work = [_we(d, 2 * d, 'put', 4, 0.30 * S) for d in range(3)] + \
           [_we(d, 2 * d + 1, 'receive', 12, 0.20 * S) for d in range(3)]
    save_checkpoint_bundle(db, run_id, batch_stats=batches, task_stats=[],
                           picker_events=[], picks=[], bin_placements=[],
                           bin_evictions=[], aisle_metrics=[], reorder_queue=[],
                           work_events=work, carryover=carry,
                           shift_days=[(0, S, S - 500, True, 0, 0, 0, 0, 0, 0, S - 500),
                                       (1, 2 * S, 2 * S - 500, True, 0, 0, 0, 0, 0, 0,
                                        2 * S - 500)])
    save_shift_days(db, run_id, [(2, 3 * S, 3 * S, False, 9, 9, 0, 0, 0, 0, 3 * S + 40)])
    # the fold now carries units: Σ qty per (batch, role), the reference run's denominator
    rows = load_work_hours(db, run_id)
    assert {(r['role'], r['units']) for r in rows if r['batch_id'] == 0} == \
        {('put', 4), ('receive', 12)}
    assert len(load_carryover(db, run_id)) == 3
    v = eq.check(db, run_id, 0, 1, expectations=_expectations())
    assert v.passed, v.reasons
    assert v.clauses['utilization'].reading['put']['realized'] == pytest.approx(0.30)
    assert v.clauses['utilization'].reading['recv']['realized'] == pytest.approx(0.20)
    assert v.clauses['supply'].reading['level'] == pytest.approx(0.05)
    assert v.clauses['supply'].reading['units']['fresh'] == 2000
    # widen the window over the capped final day: recorded, with its overtime, and no failure
    v3 = eq.check(db, run_id, 0, 2, expectations=_expectations())
    assert v3.passed, v3.reasons
    assert v3.clauses['labour'].reading['drained']['capped'] == [2]
    assert v3.clauses['labour'].reading['drained']['overtime_days'] == [2]
    assert eq.window_of(load_shift_days(db, run_id)) == (0, 2)


def test_a_run_that_never_closed_a_day_fails_on_the_ledger_not_on_a_plausible_pass(tmp_path):
    db, run_id = _fresh_db(tmp_path)
    v = eq.check(db, run_id, 0, 0, expectations=_expectations())
    assert not v.passed and v.clauses['labour'].reading['drained']['missing'] == [0]
    assert eq.window_of([]) is None
