"""test_reference_run.py — the reference-run driver discards a failing window rather than
averaging it in, measures a passing one as ratios of sums, and iterates to the fixed point.

`Optimization/simconfig/reference.py` is driven here with STUBBED `launch` / `measure`
callables (no simulation) so the loop's contract can be pinned exactly
(.scratch/department-calibration, "Choose the calibration procedure" decisions 1-3, 6):

  - a DISCARDED window's ratios re-seed the next pass (provenance `seed`) and are never
    stamped `measured`; the final record's constants come from the PASSING window alone;
  - the pass list carries every pass, accepted or not, with its reasons;
  - the fixed point closes when derived daily demand moves < 5%, else the record says cut off;
  - a receiving self-check outside tolerance is a broken run, not a discarded window.

The leaf measurement is checked against a hand computation over a real sim DB.

Run:  python -m pytest Tests/unit/test_reference_run.py -q
"""
from __future__ import annotations

import json
import os
from dataclasses import MISSING, fields

import pytest

from Optimization.simconfig import calibration as cal
from Optimization.simconfig import equilibrium as eq
from Optimization.simconfig import reference as ref

S = 28800.0


# ── a pass result, as `measure_pass` would return it ──────────────────────────────────

def _verdict(passed, lo=20, hi=39):
    clauses = {name: eq.Clause(name, True, {'days': hi - lo + 1, 'drained': hi - lo + 1,
                                            'capped': [], 'missing': [],
                                            'max_lag_s': 0.0, 'level': 0.05})
               for name in eq.CLAUSES}
    if not passed:
        clauses['drained'] = eq.Clause('drained', False,
                                       {'days': 20, 'drained': 17, 'capped': [21, 30, 35],
                                        'missing': []},
                                       '3 of 20 day(s) ended CAPPED')
    return eq.Verdict(passed, lo, hi, clauses)


def _staffing(daily_store=5000.0, daily_ff=9000.0):
    return {
        'inputs': {'store_pickers': 25, 'ff_pickers': 20, 'band_tol': 0.10},
        'derived': {'inv': {
            'day_seconds': S,
            'channels': {
                'store': {'daily_demand_units': daily_store, 'pricing_config': 'store_base',
                          'script': {'analytic_s_pick': 60.0, 'put_s': 900.0,
                                     'put_units': 100.0}},
                'fulfillment': {'daily_demand_units': daily_ff, 'pricing_config': 'ff_base',
                                'script': {'analytic_s_pick': 2.5, 'put_s': 400.0,
                                           'put_units': 100.0}},
            },
            'put': {'crew': 3}, 'receiving': {'crew': 2, 's_recv': {'value': 40.0}},
        }},
        'calibration': {'inv': {'run_fingerprint': 'wh-abc'}},
    }


def _result(run_root, *, passed, s_store, s_ff, s_put, daily=(5000.0, 9000.0)):
    v = _verdict(passed)
    return {
        'run_root': run_root, 'day_lo': 20, 'day_hi': 39, 'passed': passed,
        'reasons': [] if passed else ['inv/k1_off/store: 3 of 20 day(s) ended CAPPED'],
        'leaves': {'inv/k1_off/store': {'verdict': v}, 'inv/k1_off/fulfillment': {'verdict': v}},
        'pairs': ['inv'],
        's_pick': {'store': {'value': s_store, 'seconds': 1.0, 'units': 1.0, 'analytic': 60.0,
                             'travel_share': s_store / 60.0},
                   'fulfillment': {'value': s_ff, 'seconds': 1.0, 'units': 1.0,
                                   'analytic': 2.5, 'travel_share': s_ff / 2.5}},
        's_put': {'value': s_put, 'seconds': 1.0, 'units': 1.0, 'analytic': 6.5,
                  'travel_share': s_put / 6.5, 'per_leaf': {}},
        'recv_check': {'measured_s': 32000.0, 'expected_s': 32000.0, 'rel_error': 0.0,
                       'tol': 1e-6, 'passed': True, 'packs': 800, 'per_leaf': {},
                       'script_s_recv': {'inv': 40.0}, 'measured_s_per_pack': 40.0},
        'k_max': {'store': 31, 'fulfillment': 27},
        'daily_demand': {'store': daily[0], 'fulfillment': daily[1]},
        'staffing': _staffing(*daily),
        'spec': {'repo_commit': 'deadbeef', 'repo_dirty': False, 'releases_per_day': 1,
                 'shift_drain_or_cap': True, 'roll_over_unpicked': True,
                 'cut_at_day_end': True, 'crew_cost': {'put_intercept_scale': 0.5}},
    }


def _seed():
    return cal.load_record()                       # the committed pass-0 seed


# ── the fixed point ───────────────────────────────────────────────────────────────────

def test_a_failing_window_reseeds_and_is_never_averaged_into_the_measured_record(tmp_path):
    outcomes = [dict(passed=False, s_store=70.0, s_ff=3.5, s_put=8.0, daily=(5000.0, 9000.0)),
                dict(passed=True, s_store=64.0, s_ff=3.1, s_put=7.2, daily=(4700.0, 8600.0))]
    launched = []

    def launch(record_path, n):
        launched.append((record_path, n))
        return str(tmp_path / f'run{n}')

    def measure(run_root):
        n = int(run_root[-1])
        return _result(run_root, **outcomes[n])

    got = ref.fixed_point(launch=launch, measure=measure, seed_record=_seed(),
                          out_dir=str(tmp_path / 'out'), max_passes=2)
    rec = got['record']
    # pass 1 ran under the RE-SEED written after pass 0, whose constants say `seed`
    with open(launched[1][0], encoding='utf-8') as fh:
        reseed = json.load(fh)
    e0 = reseed['constants']['s_pick']['store']
    assert e0['value'] == 70.0 and e0['provenance'] == 'seed' and e0['analytic'] == 60.0
    assert e0['travel_share'] == pytest.approx(70.0 / 60.0)
    assert 'DISCARDED' in e0['note']
    assert not cal.is_measured(reseed)
    assert reseed['warehouse_fingerprint'] is None, 'a discarded window stamps no provenance'
    # the final record is the PASSING window's values alone -- not (70+64)/2
    assert got['measured'] and cal.is_measured(rec)
    assert rec['constants']['s_pick']['store']['value'] == 64.0
    assert rec['constants']['s_pick']['store']['provenance'] == 'measured'
    assert rec['constants']['s_pick']['fulfillment']['value'] == 3.1
    assert rec['constants']['s_put']['value'] == 7.2
    assert rec['constants']['k_max'] == {'store': 31, 'fulfillment': 27}
    assert rec['warehouse_fingerprint'] == 'wh-abc' and rec['commit'] == 'deadbeef'
    assert rec['era']['shift_drain_or_cap'] is True and rec['era']['day_seconds'] == S
    # every pass is on record, with its acceptance and its reasons
    assert [(p['pass'], p['accepted']) for p in rec['passes']] == [(0, False), (1, True)]
    assert rec['passes'][0]['reasons'] and rec['passes'][0]['s_pick']['store']['value'] == 70.0
    assert rec['passes'][0]['verdicts']['inv/k1_off/store']['passed'] is False
    # daily demand moved 6% store / 4.4% ff between the passes: NOT converged, cut off
    assert got['converged'] is False and rec['cut_off'] is True
    assert os.path.exists(got['record_path'])
    cal.validate_record(rec)                       # loadable by the run


def test_two_passes_that_both_fail_leave_a_reseed_and_nothing_measured(tmp_path):
    got = ref.fixed_point(
        launch=lambda p, n: f'r{n}',
        measure=lambda root: _result(root, passed=False, s_store=70.0, s_ff=3.5, s_put=8.0),
        seed_record=_seed(), out_dir=str(tmp_path), max_passes=2)
    assert got['measured'] is False and got['converged'] is False
    assert got['record']['constants']['s_pick']['store']['provenance'] == 'seed'
    assert 'RE-SEEDED' in got['record']['notes']
    assert len(got['passes']) == 2


def test_the_fixed_point_stops_early_when_daily_demand_holds_still(tmp_path):
    calls = []

    def measure(root):
        calls.append(root)
        return _result(root, passed=True, s_store=64.0, s_ff=3.1, s_put=7.2,
                       daily=(4700.0, 8600.0))

    got = ref.fixed_point(launch=lambda p, n: f'r{n}', measure=measure, seed_record=_seed(),
                          out_dir=str(tmp_path), max_passes=4)
    assert len(calls) == 2, 'pass 1 agreed with pass 0 within 5%, so passes 2-3 never ran'
    assert got['converged'] and got['record']['converged'] and not got['record']['cut_off']


def test_converged_is_a_relative_move_per_channel():
    ok, moves = ref.converged({'store': 5000.0, 'fulfillment': 9000.0},
                              {'store': 5200.0, 'fulfillment': 8700.0})
    assert ok and moves == pytest.approx({'store': 0.04, 'fulfillment': 1 / 30})
    ok, moves = ref.converged({'store': 5000.0}, {'store': 5300.0})
    assert not ok and moves['store'] == pytest.approx(0.06)
    assert ref.converged({}, {'store': 1.0})[0] is False, 'no prior pass, nothing converged'


def test_a_travel_share_below_one_is_dropped_with_its_reason_and_the_record_still_loads():
    r = _result('r0', passed=True, s_store=55.0, s_ff=3.1, s_put=7.2)   # 55 < analytic 60
    rec = ref.candidate_record(_seed(), r, pass_no=0)
    e = rec['constants']['s_pick']['store']
    assert e['value'] == 55.0 and e['travel_share'] is None and '< 1.0' in e['note']
    cal.validate_record(rec)


# ── the leaf measurement, against a hand computation over a real DB ──────────────────

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


def _bs(run_id, batch_id, *, day, makespan, items):
    from Optimization.persistence.Picking_Data import BatchStats
    kw = {f.name: 0 for f in fields(BatchStats)
          if f.default is MISSING and f.default_factory is MISSING}
    kw.update(run_id=run_id, batch_id=batch_id, duration=makespan / 2, num_tasks=2,
              total_items=items, task_makespan=makespan, avg_concurrent_pickers=2.0,
              picking_pct=0.5, traveling_pct=0.5, items_demanded=items, work_day=day)
    return BatchStats(**kw)


def _ts(run_id, batch_id, aisle, duration):
    from Optimization.persistence.Picking_Data import TaskStats
    return TaskStats(run_id=run_id, batch_id=batch_id, aisle_id=aisle, picker_id=0,
                     task_start_time=0.0, task_end_time=duration, duration=duration, W=0.0,
                     lift_sum=0.0, num_bins_visited=1, total_items=10)


def _we(batch_id, seq, role, qty, duration):
    return (batch_id, seq, 100.0 * seq, 100.0 * seq, 0, 7, 0, role, 'foot', role, 1, 5,
            qty, duration, 'reorder')


def test_the_receiving_self_check_is_exact_per_pack_and_fails_on_one_mispriced_row(tmp_path):
    """Each received pack is re-priced from its SKU and quantity with the channel's own
    unload price; a mix of packs that would move any AVERAGE passes exactly, and one row
    charged a second too much fails at float tolerance."""
    from Inbound.unload import unload_cost
    from Optimization.persistence.Picking_Data import (
        load_receive_events, save_checkpoint_bundle)
    from Warehouse.catalog.Order import Order
    Order.next_sku = 1
    orders = {1: Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 4, 0.5, 2.0,
                             equilibrium_qty=60, reorder_point=20),
              2: Order.build(2, 'non-conveyable', 'seasonal', 30, 30, 30, 40, 0.5, 1.0,
                             equilibrium_qty=60, reorder_point=20)}
    cost = ref.unload_price_for('store', {'put_intercept_scale': 0.5, 'put_item_ratio': 0.2,
                                          'recv_intercept_scale': 1.0})
    db, run_id = _fresh_db(tmp_path)
    rows = [(0, 0, 'receive', 1, 3), (0, 1, 'receive', 2, 1), (1, 2, 'receive', 1, 8),
            (1, 3, 'receive', 2, 2)]
    work = [(b, seq, 10.0 * seq, 10.0 * seq, 0, 7, 0, role, 'foot', role, 1, sku, qty,
             unload_cost(orders[sku].weight, orders[sku].volume(), qty, cost), 'reorder')
            for b, seq, role, sku, qty in rows]
    save_checkpoint_bundle(db, run_id, batch_stats=[], task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], work_events=work)
    got = ref.recv_exact_check(load_receive_events(db, run_id), orders, cost,
                               in_window={0, 1})
    assert got['passed'] and got['packs'] == 4 and got['units'] == 14
    assert got['rel_error'] == pytest.approx(0.0, abs=1e-12)
    # the window selects: batch 1 alone is two packs
    assert ref.recv_exact_check(load_receive_events(db, run_id), orders, cost,
                                in_window={1})['packs'] == 2
    # one row charged one second too much fails; an unpriceable SKU fails too
    bad = [dict(r) for r in load_receive_events(db, run_id)]
    bad[2]['duration'] += 1.0
    assert not ref.recv_exact_check(bad, orders, cost, in_window={0, 1})['passed']
    assert ref.recv_exact_check(load_receive_events(db, run_id), {1: orders[1]}, cost,
                                in_window={0, 1})['unknown_skus'] == 2
    # a spec with the scales recorded as None prices at the class defaults, and loads
    assert ref.unload_price_for('store', {}).intercept > 0


def test_measure_leaf_is_ratios_of_sums_and_the_window_minimum_of_k_max(tmp_path):
    from Optimization.persistence.Picking_Data import save_checkpoint_bundle
    db, run_id = _fresh_db(tmp_path)
    batches = [_bs(run_id, 0, day=0, makespan=1000.0, items=100),
               _bs(run_id, 1, day=1, makespan=3000.0, items=200),
               _bs(run_id, 2, day=2, makespan=9999.0, items=1)]         # outside the window
    tasks = [_ts(run_id, 0, 1, 600.0), _ts(run_id, 0, 2, 400.0),       # day 0: 1000/600 -> 1
             _ts(run_id, 1, 1, 1000.0), _ts(run_id, 1, 2, 1000.0),
             _ts(run_id, 1, 3, 1000.0)]                                 # day 1: 3000/1000 -> 3
    work = [_we(0, 0, 'put', 10, 70.0), _we(1, 1, 'put', 30, 90.0),
            _we(0, 2, 'receive', 12, 40.0), _we(1, 3, 'receive', 12, 44.0),
            _we(2, 4, 'put', 999, 9999.0)]
    save_checkpoint_bundle(db, run_id, batch_stats=batches, task_stats=tasks,
                           picker_events=[], picks=[], bin_placements=[], bin_evictions=[],
                           aisle_metrics=[], reorder_queue=[], work_events=work)
    m = ref.measure_leaf(db, run_id, day_lo=0, day_hi=1)
    assert m['batches'] == 2
    assert m['s_pick']['value'] == pytest.approx(4000.0 / 300.0)       # NOT mean(10, 15)
    assert m['k_max_by_day'] == {0: 1, 1: 3} and m['k_max'] == 1
    assert m['put']['value'] == pytest.approx(160.0 / 40.0)
    assert (m['recv']['packs'], m['recv']['value']) == (2, pytest.approx(42.0))
