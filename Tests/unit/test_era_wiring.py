"""test_era_wiring.py — the calibrated era is launchable from the command line, its regime is
enforced, its inputs ride every seam, and its ledger is persisted.

The era (.scratch/department-calibration, "Define the calibrated era") is ONE flag,
`--shift-drain-or-cap`, that implies one release per day, the cut and the roll-over, derives
the put and receiving crews from the pickers, and refuses the legacy crew flags.  The campaign
specs carry it as `run_defaults` (whatif_config.ERA_RUN_DEFAULTS) and `calibration_reference`
is the reference run's launch shape ("Choose the calibration procedure").

A config knob in this repo has FIVE wirings and missing any one fails SILENTLY (memory
`config-knob-has-five-seams`), plus the sixth stamp onto sim_result.  The new knobs here --
the era flag, the era's scalars, the put crew's size and mode, the calibration overrides --
are pinned on each, and the `staffing` payload's era shape (`{inputs, derived, calibration}`)
is checked by the worker's own refusal (`strategy_runner._check_declared_crew`).

The drain-or-cap ledger (`shift_days`) is written through the checkpoint bundle and read back
from the FILE ("Declare the equilibrium bands", decision 5) -- a bundle argument accepted and
never inserted is that function's characteristic failure -- and the final day's flush sits
OUTSIDE the `if pb:` tail (memory `run-end-writers-miss-the-final-flush`).

Run:  python -m pytest Tests/unit/test_era_wiring.py -q
"""
from __future__ import annotations

import argparse
import inspect
import logging
import sqlite3

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import (
    CONFIG, STAFFING_KEYS, CALIBRATION_KEYS, staffing_spec, put_crew_spec, recv_crew_spec,
    work_day_spec, era_on,
)
from Optimization.config.whatif_config import SPECS, ERA_RUN_DEFAULTS

_ERA_INPUTS = ('rho_pick', 'rho_put', 'rho_recv', 'f_put', 'f_recv', 'band_tol',
               'put_crew_mode')
_NEW_KEYS = (*_ERA_INPUTS, *CALIBRATION_KEYS, 'shift_drain_or_cap', 'put_crew_size',
             'calibration_record', 'recv_crew_size')


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    before = {k: CONFIG['global'].get(k) for k in _NEW_KEYS}
    yield CONFIG['global']
    CONFIG['global'].update(before)


def _ns(**over) -> argparse.Namespace:
    base = dict(shift_drain_or_cap=False, releases_per_day=None, roll_over_unpicked=False,
                cut_at_day_end=False, put_queue_split=False, recv_crew_size=0,
                recv_day_seconds=None, recv_day_origin=0.0, put_crew_size=1, put_cart_crew=1,
                put_pallet_crew=1, put_ff_crew=1)
    base.update(over)
    return argparse.Namespace(**base)


# ── seam 1: declared ──────────────────────────────────────────────────────────────

def test_the_eras_scalars_are_declared_assumptions_with_their_flags_named():
    assert (_s.RHO_PICK, _s.RHO_PUT, _s.RHO_RECV) == (0.85, 0.85, 0.85)
    assert (_s.F_PUT, _s.F_RECV, _s.BAND_TOL) == (1.0, 1.0, 0.10)
    assert (_s.S_PICK_STORE, _s.S_PICK_FF, _s.S_PUT) == (None, None, None)
    src = inspect.getsource(_s)
    for flag in ('--rho-pick', '--rho-put', '--rho-recv', '--f-put', '--f-recv', '--band-tol',
                 '--s-pick-store', '--s-pick-ff', '--s-put', '--put-crew-size',
                 '--put-crew-mode', '--shift-drain-or-cap', '--calibration-record'):
        assert flag in src, f'{flag} is not named beside its setting'


# ── seam 2: CONFIG + call-time accessors ──────────────────────────────────────────

def test_staffing_keys_carry_the_scalars_the_mode_and_the_overrides():
    assert set(_ERA_INPUTS) <= set(STAFFING_KEYS)
    assert set(CALIBRATION_KEYS) <= set(STAFFING_KEYS)
    assert CALIBRATION_KEYS == ('s_pick_store', 's_pick_ff', 's_put')
    for k in (*STAFFING_KEYS, 'shift_drain_or_cap', 'put_crew_size', 'calibration_record'):
        assert k in CONFIG['global'], k


def test_staffing_spec_resolves_a_none_scalar_to_its_default_and_keeps_a_none_override(restore):
    restore.update(rho_pick=None, f_put=None, put_crew_mode=None, s_put=None, s_pick_ff=4.5)
    spec = staffing_spec()
    assert spec['rho_pick'] == _s.RHO_PICK and spec['f_put'] == _s.F_PUT
    assert spec['put_crew_mode'] == _s.PUT_CREW_MODE
    assert spec['s_put'] is None, '"no override" is a value the calibration loader reads'
    assert spec['s_pick_ff'] == 4.5
    assert set(spec) == set(STAFFING_KEYS)


def test_era_on_reads_the_same_key_the_worker_is_handed(restore):
    restore['shift_drain_or_cap'] = True
    assert era_on() is True and work_day_spec()['drain_or_cap'] is True
    restore['shift_drain_or_cap'] = False
    assert era_on() is False and work_day_spec()['drain_or_cap'] is False


def test_the_derived_size_overrides_the_declared_key_in_both_crew_accessors(restore):
    restore.update(put_crew_size=1, recv_crew_size=0)
    assert put_crew_spec(size=4)['size'] == 4
    assert recv_crew_spec(size=3)['size'] == 3
    assert recv_crew_spec(size=0) is None, 'a derived crew of nobody is still no dock'
    assert recv_crew_spec() is None and put_crew_spec()['size'] == 1


def test_a_bad_put_mode_is_refused_at_the_accessor(restore):
    restore['put_crew_mode'] = 'forklift'
    with pytest.raises(ValueError, match='put_crew_mode'):
        put_crew_spec()


# ── seam 3: the CLI, and the era's regime ─────────────────────────────────────────

@pytest.mark.parametrize('flag', ['--shift-drain-or-cap', '--put-crew-size', '--put-crew-mode',
                                  '--rho-pick', '--rho-put', '--rho-recv', '--f-put',
                                  '--f-recv', '--band-tol', '--s-pick-store', '--s-pick-ff',
                                  '--s-put', '--calibration-record'])
def test_each_new_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), flag


def test_a_utilization_target_must_lie_in_the_unit_interval():
    from Optimization.run_simulation import _unit_fraction
    assert _unit_fraction('0.85') == 0.85 and _unit_fraction('1') == 1.0
    for bad in ('0', '1.5', '-0.2', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            _unit_fraction(bad)


def test_the_campaign_specs_and_the_reference_run_default_to_the_era():
    assert ERA_RUN_DEFAULTS == {'shift_drain_or_cap': True, 'releases_per_day': 1,
                                'roll_over_unpicked': True, 'cut_at_day_end': True}
    for name in ('inbound_select', 'inbound_policies', 'inbound_pilot', 'calibration_reference'):
        assert SPECS[name]['run_defaults'] is ERA_RUN_DEFAULTS, name
    for name in ('single', 'scheduler_ab', '_canary_single', '_canary_sweep'):
        assert 'run_defaults' not in SPECS[name], f'{name} must stay flag-off (byte-identical)'
    ref = SPECS['calibration_reference']
    assert ref['arms'] == ('fifo',) and ref['schedulers'] == ['lpt']
    assert len(ref['ks']) == 1 and ref['zoning'] == [('off', {'enabled': False})]


def test_run_defaults_overlay_args_but_an_explicit_flag_wins_with_a_note():
    from Optimization.run_simulation import _apply_run_defaults
    args = _ns(releases_per_day=3)
    notes = _apply_run_defaults(args, {'run_defaults': ERA_RUN_DEFAULTS},
                                explicit={'releases_per_day'})
    assert args.shift_drain_or_cap is True and args.roll_over_unpicked is True
    assert args.releases_per_day == 3 and any('releases_per_day' in n for n in notes)
    args = _ns()
    assert _apply_run_defaults(args, {'ks': [1]}, explicit=set()) == []
    assert args.shift_drain_or_cap is False


def test_the_era_completes_the_cadence_the_cut_and_the_roll_over():
    from Optimization.run_simulation import _check_era_flags
    args = _ns(shift_drain_or_cap=True)
    notes = _check_era_flags(args, explicit={'shift_drain_or_cap'})
    assert (args.releases_per_day, args.roll_over_unpicked, args.cut_at_day_end) == (1, True, True)
    assert len(notes) == 3
    assert _check_era_flags(_ns(), explicit=set()) == [], 'flag-off: nothing to complete'


@pytest.mark.parametrize('legacy', ['recv_crew_size', 'recv_day_seconds', 'recv_day_origin',
                                    'put_crew_size', 'put_cart_crew', 'put_pallet_crew',
                                    'put_ff_crew'])
def test_a_legacy_crew_flag_typed_under_the_era_is_an_error(legacy):
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match=legacy.replace('_', '-')):
        _check_era_flags(_ns(shift_drain_or_cap=True), explicit={'shift_drain_or_cap', legacy})


def test_a_split_put_queue_and_a_foreign_cadence_are_errors_under_the_era():
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match='put-queue-split'):
        _check_era_flags(_ns(shift_drain_or_cap=True, put_queue_split=True), explicit=set())
    with pytest.raises(SystemExit, match='releases-per-day'):
        _check_era_flags(_ns(shift_drain_or_cap=True, releases_per_day=2),
                         explicit={'releases_per_day'})


def test_the_legacy_flags_keep_working_flag_off():
    from Optimization.run_simulation import _check_era_flags
    args = _ns(recv_crew_size=4, put_crew_size=2, put_queue_split=True)
    assert _check_era_flags(args, explicit={'recv_crew_size', 'put_crew_size',
                                            'put_queue_split'}) == []


def test_the_override_loop_writes_the_era_flag_and_the_put_crew_size():
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation.main)
    assert "g['shift_drain_or_cap'] = bool(args.shift_drain_or_cap)" in src
    assert "g['put_crew_size']      = args.put_crew_size" in src
    assert '_check_era_flags(args, explicit)' in src, 'the era regime is not enforced in main'
    assert '_apply_run_defaults(args, spec_dict, explicit)' in src


# ── seam 4: recorded and restored at BOTH sites ───────────────────────────────────

def test_the_era_flag_and_put_crew_size_are_recorded_and_restored_on_resume():
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation)
    for key in ("'shift_drain_or_cap'", "'put_crew_size'"):
        assert src.count(key) >= 3, f'{key}: written to run_spec, restored on resume, and set'
    from Optimization.run_simulation import _apply_run_spec
    args = argparse.Namespace(shift_drain_or_cap=False, put_crew_size=1, rho_pick=0.85, s_put=None)
    _apply_run_spec(args, {'shift_drain_or_cap': True, 'put_crew_size': 2,
                           'staffing': {'inputs': {'rho_pick': 0.7, 's_put': 3.0}}},
                    explicit=set())
    assert (args.shift_drain_or_cap, args.put_crew_size, args.rho_pick, args.s_put) == (
        True, 2, 0.7, 3.0)


def test_a_standalone_reanalysis_restores_the_era_and_the_scalars(tmp_path, restore):
    from Optimization import run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {
        'n_batches': 3, 'shift_drain_or_cap': True, 'put_crew_size': 2,
        'staffing': {'inputs': {'store_pickers': 7, 'ff_pickers': 5, 'rho_pick': 0.7,
                                'put_crew_mode': 'machine', 's_put': 3.0},
                     'derived': {'pair': {'put': {'crew': 4}}}}})
    run_analysis._apply_run_shape(str(tmp_path), logging.getLogger('t'))
    assert era_on() is True
    assert put_crew_spec()['size'] == 2 and put_crew_spec()['mode'] == 'machine'
    spec = staffing_spec()
    assert (spec['rho_pick'], spec['s_put'], spec['rho_put']) == (0.7, 3.0, _s.RHO_PUT)
    assert run_analysis._RUN_STAFFING['derived'] == {'pair': {'put': {'crew': 4}}}


def test_a_pre_era_spec_restores_to_flag_off(tmp_path, restore):
    from Optimization import run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    restore.update(shift_drain_or_cap=True, put_crew_size=5)      # "this checkout", perturbed
    _write_run_spec(str(tmp_path), {'n_batches': 3})
    run_analysis._apply_run_shape(str(tmp_path), logging.getLogger('t'))
    assert era_on() is False
    assert put_crew_spec()['size'] == _s.PUT_CREW_SIZE


# ── seam 5: the worker payload ────────────────────────────────────────────────────

def test_the_payload_carries_derived_crews_under_the_era_and_the_inputs_flag_off():
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits._prepare_channel_run)
    assert "_st = shared.get('staffing')" in src
    assert "_put_size = _st['derived']['put']['crew'] if _st else None" in src
    assert "put_crew            = put_crew_spec(size=_put_size)," in src
    assert "recv_crew           = recv_crew_spec(size=_recv_size)," in src
    assert "_staffing_payload = ({'inputs': staffing_spec(), **_st} if _st else staffing_spec())" in src
    build = inspect.getsource(workunits._build_work_units)
    assert 'if era_on():' in build and '_derive_staffing_for_pair(' in build
    assert "shared['staffing'] = {'derived': _derived, 'calibration': _cal}" in build


def _era_payload(put=4, recv=2, **over):
    p = {'channel_name': 'store', 'put_crew': {'size': put}, 'recv_crew': {'size': recv},
         'staffing': {'inputs': {'store_pickers': 7, 'ff_pickers': 5},
                      'derived': {'put': {'crew': 4}, 'receiving': {'crew': 2}}}}
    p.update(over)
    return p


def test_the_worker_refuses_a_put_or_receiving_crew_its_record_did_not_derive():
    from Optimization.simdriver.strategy_runner import _check_declared_crew
    _check_declared_crew(_era_payload(), 7)                      # agreeing: silent
    with pytest.raises(ValueError, match='put crew of 3'):
        _check_declared_crew(_era_payload(put=3), 7)
    with pytest.raises(ValueError, match='receiving crew of 1'):
        _check_declared_crew(_era_payload(recv=1), 7)
    with pytest.raises(ValueError, match='receiving crew of 0'):
        _check_declared_crew(_era_payload(recv_crew=None), 7)
    with pytest.raises(ValueError, match='store_pickers=7'):
        _check_declared_crew(_era_payload(), 9)


def test_the_flag_off_payload_shape_is_still_accepted():
    from Optimization.simdriver.strategy_runner import _check_declared_crew
    _check_declared_crew({'channel_name': 'fulfillment',
                          'staffing': {'store_pickers': 25, 'ff_pickers': 20}}, 20)
    _check_declared_crew({}, 99)                                 # no record: nothing to check


# ── the ledger: shift_days ────────────────────────────────────────────────────────

def _fresh_db(tmp_path):
    from Optimization.persistence.Picking_Data import create_run, init_run_db
    db = str(tmp_path / 'sim_fifo.db')
    init_run_db(db)
    run_id = create_run(db, 'test', {'num_pickers': 1, 'x_speed': 1.0, 'y_speed': 1.0,
                                     'pick_intercept': 1.0, 'pick_weight_coef': 0.0,
                                     'pick_volume_coef': 0.0, 'cart_swap_coef': 0.0,
                                     'k_pickers': 1, 'n_batches': 2, 'seed_world': 1,
                                     'keyframe_interval': 0, 'optimal_sigma_fd': 0.0,
                                     'optimal_work': 0.0})
    return db, run_id


def test_shift_days_rows_ride_the_bundle_and_read_back_from_the_file(tmp_path):
    from Optimization.persistence.Picking_Data import (
        load_shift_days, save_checkpoint_bundle, save_shift_days)
    db, run_id = _fresh_db(tmp_path)
    save_checkpoint_bundle(db, run_id, batch_stats=[], task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[],
                           shift_days=[(0, 28800.0, 20000.0, True, 0, 0, 0, 0, 20000.0),
                                       (1, 57600.0, 57600.0, False, 233, 200, 30, 3, 58000.0)])
    save_shift_days(db, run_id, [(2, 86400.0, 86400.0, False, 5, 5, 0, 0, 86400.0)])
    rows = load_shift_days(db, run_id)
    assert [r['day'] for r in rows] == [0, 1, 2]
    assert rows[0]['drained'] == 1 and rows[0]['end_s'] == 20000.0
    assert (rows[1]['standing'], rows[1]['standing_put'], rows[1]['standing_dock'],
            rows[1]['standing_carry']) == (233, 200, 30, 3)
    assert rows[1]['last_finish'] == 58000.0 > rows[1]['cap_end'], 'START-gate overtime kept'
    # INSERT OR REPLACE: re-flushing a day (a resume replaying a window) does not duplicate
    save_shift_days(db, run_id, [(2, 86400.0, 80000.0, True, 0, 0, 0, 0, 80000.0)])
    rows = load_shift_days(db, run_id)
    assert len(rows) == 3 and rows[2]['drained'] == 1
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM shift_days').fetchone()[0] == 3


def test_an_era_less_run_and_a_pre_era_vintage_both_read_as_no_days(tmp_path):
    from Optimization.persistence.Picking_Data import load_shift_days
    db, run_id = _fresh_db(tmp_path)
    assert load_shift_days(db, run_id) == []


def test_the_table_is_in_the_declared_shape_and_the_semantics_cover_it():
    from Optimization.persistence.Picking_Data import declared_sim_schema_shape, CONDITIONAL_READS
    from Optimization.persistence.sim_semantics import SIM_DB_SEMANTICS
    shape = declared_sim_schema_shape()
    assert 'shift_days' in shape['tables']
    cols = {c['name'] for c in shape['tables']['shift_days']['columns']}
    assert set(SIM_DB_SEMANTICS['shift_days']) == cols
    assert CONDITIONAL_READS['load_shift_days'] == 'shift_days'


def test_the_runner_writes_the_ledger_and_flushes_the_final_day_outside_the_tail():
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr._run_strategy_worker_impl)
    assert 'sd.append(_shift_close_out(_shift_prev_day, _shift_standing,' in src
    assert src.count('shift_days=sd') == 2, 'both bundle flushes carry the ledger'
    assert 'sd.clear()' in src
    # The boundary test precedes the fold of this batch's clocks/cut/depths, so a day closes
    # on ITS last batch's state (the log-only ledger mis-attributed every first batch).
    fold = src.index('_shift_last_finish = max(_shift_last_finish, arm_clock')
    assert src.index('elif _d != _shift_prev_day:') < fold
    assert src.index('_shift_standing = (mgr.queue_depth, mgr.dock_depth') > fold
    tail = src[src.index('if pb:'):]
    final = tail.index('if _drain_or_cap and _shift_prev_day is not None:')
    assert 'save_shift_days(db_path, run_id, [_shift_close_out(' in tail
    # the final-day flush is a sibling of `if pb:`, not nested inside it
    line = tail[:final].rsplit('\n', 1)[-1]
    assert line == '    ', 'the final-day flush must sit at the `if pb:` indentation, outside it'
