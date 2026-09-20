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
             'recv_crew_size')


def _squash(text: str) -> str:
    """Collapse every run of whitespace to one space -- for an assertion about a CALL
    rather than about its line breaks."""
    import re as _re
    return _re.sub(r'\s+', ' ', text)


def _leaf_source() -> str:
    """One leaf, in three pieces since ticket 06.

    `_build_arm` constructs the arm and returns an `ArmAssembly`; `_build_leaf` closes the two
    batch halves over it; `_shift_close_out` is the day close-out the stepping half calls.
    A question about "what the runner does" spans all three, and a scan of one would now pass
    by looking in the wrong place.

    All three are module-level since ticket 07, so the result parses as a module with no
    dedent — these scans `ast.parse` what they get back.
    """
    import inspect

    from Optimization.simdriver import strategy_runner as _sr
    return (inspect.getsource(_sr._build_arm)
            + inspect.getsource(_sr._build_leaf)
            + inspect.getsource(_sr._shift_close_out))


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
                 '--put-crew-mode', '--shift-drain-or-cap'):
        assert flag in src, f'{flag} is not named beside its setting'


# ── seam 2: CONFIG + call-time accessors ──────────────────────────────────────────

def test_staffing_keys_carry_the_scalars_the_mode_and_the_overrides():
    assert set(_ERA_INPUTS) <= set(STAFFING_KEYS)
    assert set(CALIBRATION_KEYS) <= set(STAFFING_KEYS)
    assert CALIBRATION_KEYS == ('s_pick_store', 's_pick_ff', 's_put')
    for k in (*STAFFING_KEYS, 'shift_drain_or_cap', 'put_crew_size'):
        assert k in CONFIG['global'], k
    assert 'calibration_record' not in CONFIG['global'], 'the calibration record is retired'


def test_staffing_spec_resolves_a_none_scalar_to_its_default_and_keeps_a_none_override(restore):
    restore.update(rho_pick=None, f_put=None, put_crew_mode=None, s_put=None, s_pick_ff=4.5)
    spec = staffing_spec()
    assert spec['rho_pick'] == _s.RHO_PICK and spec['f_put'] == _s.F_PUT
    assert spec['put_crew_mode'] == _s.PUT_CREW_MODE
    assert spec['s_put'] is None, '"no override" is a value the derivation reads'
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
                                  '--s-put'])
def test_each_new_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), flag


def test_a_utilization_target_must_lie_in_the_unit_interval():
    from Optimization.run_simulation import _unit_fraction
    assert _unit_fraction('0.85') == 0.85 and _unit_fraction('1') == 1.0
    for bad in ('0', '1.5', '-0.2', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            _unit_fraction(bad)


def test_the_campaign_specs_default_to_the_era_and_the_reference_run_is_gone():
    assert ERA_RUN_DEFAULTS == {'shift_drain_or_cap': True, 'releases_per_day': 1,
                                'roll_over_unpicked': True, 'cut_at_day_end': True}
    # Phase 1 carries the era PLUS the arrival regime and the declared depth
    # (PHASE1_RUN_DEFAULTS, "Re-size the funnel in site days"): since department-calibration
    # decision 11 it runs the yard ON under `fifo`, so both phases solve their line floor at
    # the same order-to-shelf lead. What it still does NOT carry is coupling -- asserted in
    # Tests/unit/test_funnel_spec_pairs.py and again in Tests/unit/test_funnel_window.py.
    assert ERA_RUN_DEFAULTS.items() <= SPECS['inbound_select']['run_defaults'].items()
    # Phase 2 carries the era PLUS coupling (site-dock 23: PHASE2_RUN_DEFAULTS), so it is a
    # SUPERSET rather than the same object — the era keys are what this asserts, and
    # Tests/unit/test_funnel_spec_pairs.py owns the coupling key.
    assert ERA_RUN_DEFAULTS.items() <= SPECS['inbound_policies']['run_defaults'].items()
    # The pilot gate carries the era PLUS its arrival regime, and no crew key: the crew is
    # derived under the era ("Verify the derived receiving crew").
    pilot = SPECS['inbound_pilot']['run_defaults']
    assert ERA_RUN_DEFAULTS.items() <= pilot.items()
    assert pilot['inbound_standing_yard'] is True and pilot['inbound_trailer_type'] == '53'
    assert pilot['inbound_lead_spread'] > 0 and pilot['inbound_lead_minutes'] > 0
    # ... and COUPLING, since the gate re-verifies on the SITE's dock rather than per leaf
    # ("Re-verify the gate under the lead-aware record"): the pilot measures yard contention,
    # and a per-leaf yard is the artefact the site-dock effort retired.  Asserted HERE beside
    # the rest of the pilot's regime, and against phase 2's, so the two cannot drift apart.
    assert pilot['couple_channels'] is True
    assert SPECS['inbound_policies']['run_defaults']['couple_channels'] is True
    assert not {k for k in pilot if k.startswith('recv_') or k.startswith('put_')}
    for name in ('single', 'scheduler_ab', '_canary_single', '_canary_sweep'):
        assert 'run_defaults' not in SPECS[name], f'{name} must stay flag-off (byte-identical)'
    # No calibration simulations ("Derive the expected-travel closed form"): the reference
    # run's spec left with its driver.
    assert 'calibration_reference' not in SPECS


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
    with pytest.raises(ValueError, match='inbound_lead_sprad'):
        _apply_run_defaults(_ns(), {'run_defaults': {'inbound_lead_sprad': 0.7}},
                            explicit=set())


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
    from Optimization.config.sim_config import KNOB_BY_NAME
    # One registry loop writes every knob now; `bool` is declared rather than written out.
    assert KNOB_BY_NAME['shift_drain_or_cap'].coerce == 'bool', (
        'the era flag must be coerced to a real bool, not left as the store_true value')
    assert KNOB_BY_NAME['shift_drain_or_cap'].apply == 'always'
    assert KNOB_BY_NAME['put_crew_size'].apply == 'always', (
        'the declared put crew must be written back unconditionally')
    assert '_check_era_flags(args, explicit)' in src, 'the era regime is not enforced in main'
    assert '_apply_run_defaults(args, spec_dict, explicit)' in src


# ── seam 4: recorded and restored at BOTH sites ───────────────────────────────────

def test_the_era_flag_and_put_crew_size_are_recorded_and_restored_on_resume():
    from Optimization.config.sim_config import KNOB_BY_NAME, SPEC_KNOB_NAMES
    for key in ('shift_drain_or_cap', 'put_crew_size'):
        assert key in KNOB_BY_NAME, f'{key}: not a declared knob, so nothing carries it'
        assert key in SPEC_KNOB_NAMES, (
            f'{key}: written to run_spec and restored on resume, both derived from the'
            f' registry')
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
    # The restored value reaches CONFIG; under the era the accessor records `rho_pick` as
    # None (ADR-0004: the first-time confidence replaced it and nothing reads it), while
    # the shared scalars and the override resolve as before.
    assert CONFIG['global']['rho_pick'] == 0.7
    assert spec['rho_pick'] is None
    assert (spec['s_put'], spec['rho_put']) == (3.0, _s.RHO_PUT)
    assert (spec['store_pickers'], spec['ff_pickers']) == (None, None)
    assert spec['first_time_confidence'] == _s.FIRST_TIME_CONFIDENCE
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
    from Optimization.persistence.Picking_Data import load_shift_days
    from Optimization.persistence.checkpoint_buffer import write_rows
    db, run_id = _fresh_db(tmp_path)
    write_rows(db, run_id, batch_stats=[], task_stats=[], picker_events=[],
               picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
               reorder_queue=[],
               shift_days=[(0, 28800.0, 20000.0, True, 0, 0, 0, 0, 0, 0, 20000.0),
                           (1, 57600.0, 57600.0, False, 233, 200, 30, 3, 1, 2,
                            58000.0)])
    write_rows(db, run_id, shift_days=[(2, 86400.0, 86400.0, False, 5, 5, 0, 0, 0, 0, 86400.0)])
    rows = load_shift_days(db, run_id)
    assert [r['day'] for r in rows] == [0, 1, 2]
    assert rows[0]['drained'] == 1 and rows[0]['end_s'] == 20000.0
    assert (rows[1]['standing'], rows[1]['standing_put'], rows[1]['standing_dock'],
            rows[1]['standing_carry']) == (233, 200, 30, 3)
    # the carry by cause: the cut's (labour) and the shelf's (supply) halves
    assert (rows[1]['standing_carry_labour'], rows[1]['standing_carry_supply']) == (1, 2)
    assert rows[1]['last_finish'] == 58000.0 > rows[1]['cap_end'], 'START-gate overtime kept'
    # INSERT OR REPLACE: re-flushing a day (a resume replaying a window) does not duplicate
    write_rows(db, run_id, shift_days=[(2, 86400.0, 80000.0, True, 0, 0, 0, 0, 0, 0, 80000.0)])
    rows = load_shift_days(db, run_id)
    assert len(rows) == 3 and rows[2]['drained'] == 1
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM shift_days').fetchone()[0] == 3


def test_the_pre_split_vintage_reads_the_carry_halves_as_none_and_its_verdict_as_written(
        tmp_path):
    """487a65bf83a9's ledger has one `standing_carry` and a `drained` judged with the supply
    carry counted as standing work.  The `shift_day_frame` override serves it with the two
    halves as NULL -- unknown, never 0 -- and the verdict is NOT re-derived (one definition:
    `equilibrium.is_drained`, and it lives in the writer)."""
    from Optimization.persistence.Picking_Data import (
        PRE_CARRY_SPLIT_SIM_SCHEMA_ID, load_shift_days)
    from Optimization.persistence.checkpoint_buffer import write_rows
    db, run_id = _fresh_db(tmp_path)
    write_rows(db, run_id, shift_days=[(0, 28800.0, 28800.0, False, 7, 0, 0, 7, 0, 7, 28800.0),
                                       # an overtime day that vintage's runner stamped DRAINED
                                       (1, 57600.0, 57600.0, True, 0, 0, 0, 0, 0, 0, 57738.0)])
    con = sqlite3.connect(db)
    con.execute('ALTER TABLE shift_days DROP COLUMN standing_carry_labour')
    con.execute('ALTER TABLE shift_days DROP COLUMN standing_carry_supply')
    # EVERY column added since that vintage has to come back off, not just the two this
    # test is about: the fake is built from the CURRENT DDL, so the file's observed shape is
    # `487a65bf83a9` only while nothing else has been added -- and `dataset.bind` verifies
    # the stamp against the observed shape, so a missed drop fails here rather than in the
    # code under test.  These five are ADR-0003's (2026-09-08); the table, the spill column
    # and the tier pair are the per-bucket free index's (2026-09-10); `site_receiving` is
    # the site dock's own per-batch totals (site-dock 25, 2026-09-12).
    con.execute('DROP TABLE free_index')
    # And the mirror case, which the paragraph above did not anticipate: a column REMOVED
    # since that vintage has to be put BACK, or the fake is missing something the real file
    # had.  `aisle_metrics.lift_sum` was dropped on 2026-09-16 as write-only.  It has to be
    # RECREATED rather than ALTERed in, because the observed shape records column ORDER and
    # `ADD COLUMN` can only append -- which reads as a different shape, not a restored one.
    con.execute('DROP TABLE aisle_metrics')
    con.execute('''CREATE TABLE aisle_metrics (
        run_id        INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id      INTEGER NOT NULL,
        aisle_id      INTEGER NOT NULL,
        n_skus        INTEGER NOT NULL DEFAULT 0,
        n_bins        INTEGER NOT NULL DEFAULT 0,
        demand_sum    REAL    NOT NULL DEFAULT 0.0,
        lift_sum      REAL    NOT NULL DEFAULT 0.0,
        pick_load_sum REAL    NOT NULL DEFAULT 0.0,
        PRIMARY KEY (run_id, batch_id, aisle_id)
    )''')
    # DROP TABLE took the indexes with it, and the observed shape records those too.
    con.execute('CREATE INDEX ix_am_run_batch ON aisle_metrics (run_id, batch_id)')
    con.execute('CREATE INDEX ix_am_run_aisle ON aisle_metrics (run_id, aisle_id)')
    con.execute('DROP TABLE site_receiving')
    for _c in ('unit_size', 'bin_size', 'bin_state'):
        con.execute(f'ALTER TABLE bin_placement DROP COLUMN {_c}')
    for _c in ('put_spills', 'put_topups', 'recv_repacks', 'recv_repacked_packs',
               'free_bins',
               # The placement score, 2026-09-19 -- postdates this vintage like the five
               # above it, so today's schema carries it and the fake must not.
               'pick_owed_s', 'unservable_weight'):
        con.execute(f'ALTER TABLE batch_stats DROP COLUMN {_c}')
    # THE THREE RETIRED INDEXES, PUT BACK.  `ix_bp_bin`, `ix_be_bin` and `ix_picks_run_sku`
    # existed on every file of both vintages faked here and were removed from the schema by the
    # deletion test (nothing read them). The observed shape records indexes, so a fake built
    # from TODAY's schema is missing three of them and re-derives to an id neither vintage ever
    # had -- the same trap as `lift_sum` above, in the other direction.
    con.execute('CREATE INDEX ix_picks_run_sku ON picks (run_id, sku)')
    con.execute('CREATE INDEX ix_bp_bin ON bin_placement '
                '(run_id, aisle_id, bayX, bayY, batch_id)')
    con.execute('CREATE INDEX ix_be_bin ON bin_eviction '
                '(run_id, aisle_id, bayX, bayY, batch_id)')
    con.execute('UPDATE simulation_runs SET sim_schema_id = ?',
                (PRE_CARRY_SPLIT_SIM_SCHEMA_ID,))
    con.commit()
    # fold the WAL into the main file: the loaders open IMMUTABLE and never read a hot WAL
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    # the file now IS the pre-split vintage -- stamped, and the observed shape agrees
    from Schema import dataset
    with dataset.bind(db, 'sim_db', immutable=True) as ds:
        assert (ds.schema_id, ds.source) == (PRE_CARRY_SPLIT_SIM_SCHEMA_ID, 'stamped')
    rows = load_shift_days(db, run_id)
    assert len(rows) == 2
    r = rows[0]
    assert r['drained'] == 0 and r['standing_carry'] == 7, 'the old verdict stands as written'
    assert r['standing_carry_labour'] is None and r['standing_carry_supply'] is None
    # ... except for the overtime term, folded in for this vintage as for every other
    assert rows[1]['drained'] == 0 and rows[1]['last_finish'] == 57738.0


def test_a_ledger_stamped_before_the_overtime_amendment_reads_its_overtime_days_capped(
        tmp_path):
    """Until 2026-09-07 the runner stamped an overtime day DRAINED (the store leaf of the
    line-floor check, day 3: ended at its cap, last finish 138 s past it, nothing standing),
    and the lag it left on the next release raised the released-late clause.  The
    amendment moved no column, so the loader folds the term in off the row's own two
    stamps for every vintage -- and `_sdf`'s `capped`, hence `days_capped`, follows."""
    from Optimization.persistence.Picking_Data import load_shift_days
    from Optimization.persistence.checkpoint_buffer import write_rows
    from Optimization.Performance_Evaluations.common.frames import _sdf
    db, run_id = _fresh_db(tmp_path)
    S = 28800.0
    write_rows(db, run_id, shift_days=[
        (3, 4 * S, 4 * S, True, 0, 0, 0, 0, 0, 0, 4 * S + 138.1),           # the old stamp
        (4, 5 * S, 5 * S - 500.0, True, 0, 0, 0, 0, 0, 0, 5 * S - 500.0),   # drained early
        (5, 6 * S, 6 * S, False, 9, 9, 0, 0, 0, 0, 6 * S + 40.0)])          # capped anyway
    rows = load_shift_days(db, run_id)
    assert [r['drained'] for r in rows] == [0, 1, 0]
    assert rows[0]['last_finish'] == 4 * S + 138.1 and rows[0]['end_s'] == 4 * S, \
        'the stamps stand; only the verdict is served amended'
    df = _sdf(rows, None, None)
    assert list(df['capped']) == [1, 0, 1]
    assert [bool(x) for x in df['overtime_s'] > 0] == [True, False, True]


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
    """The four shift-ledger closure variables became `ShiftLedger` (simdriver/shift_ledger.py).

    Every fact this gate pinned is re-expressed against the new shape rather than dropped --
    the ORDERING and the run-end flush are the two it exists for, and both survive verbatim
    in meaning. What changed is that the ordering is now enforced by the ledger's INTERFACE
    (`advance_to` returns the day to close; `note` folds this batch in) and additionally
    unit-tested in `Tests/unit/test_shift_ledger.py`, so this file guards the CALL SITE while
    that one guards the rule.
    """
    import Optimization.simdriver.strategy_runner as sr
    src = _leaf_source()
    assert 'asm.sd.append(_shift_close_out(*_closed, release=asm._release, log=asm.log))' in src
    # THE LEDGER RIDES THE BUFFER, which is what "both flushes carry it" became. `sd` is
    # the buffer's own `shift_days` list (ticket 07), so a row appended to it is in the
    # channel that inserts it -- there is no second keyword list to forget it from.
    assert "sd  = buf.rows('shift_days')" in src, (
        'the ledger is no longer a checkpoint-buffer channel')
    assert 'save_checkpoint_bundle' not in src, (
        'a 15-keyword flush is back; a row reaches the DB by being in a CHANNEL now')
    # No hand-written clear any more: `CheckpointBuffer.flush` clears its own channels,
    # and a clear statement naming every list is what used to leave one out.
    assert 'asm.sd.clear()' not in src, 'a hand-written per-list clear is back'
    # ONE definition of drained, and it is labour-only: the close-out calls
    # `equilibrium.is_drained` with the LABOUR half of the carry and the overtime stamp
    # (the last finish past the cap) and never re-derives it
    # Whitespace-normalised: the continuation's alignment moved when `shift_close_out`
    # became an `ArmAssembly` method (ticket 06), and the claim is about the CALL and its
    # arguments, not about which column the second line starts in.
    assert ('_is_drained(cut=cut, standing_put=_s_put, standing_dock=_s_dock, '
            'standing_carry_labour=_s_labour, overtime=_overtime)') in _squash(src)
    assert '_overtime = float(last_finish) > _cap_end' in src,         'overtime is the last finish past the cap, computed before the verdict'
    assert '_drained = (not cut)' not in src, 'the verdict must not be re-derived inline'
    # The standing tuple carries the DOCK FLOOR beside the put queues, and under a site
    # dock it carries this leaf's SHARE of it -- `mgr.dock_depth` refuses on a coupled leaf
    # because one floor holding both channels' merchandise has no per-channel answer.
    assert 'standing=(asm.mgr.queue_depth,' in src
    # Ticket 08 made that dispatch the scope's, so the claim moves to the two places that
    # now carry it: the call site asks ONE object, and the object answers per rung. Asserting
    # only the call site would pass on a scope whose two rungs returned the same thing.
    assert 'asm.scope.dock_depth(asm.mgr),' in src
    from Optimization.simdriver.leaf_scope import LeafScope, SiteScope
    import inspect as _insp
    assert 'mgr.dock_depth' in _insp.getsource(LeafScope.dock_depth)
    assert 'dock_depth_for(mgr)' in _insp.getsource(SiteScope.dock_depth)
    assert '*_pending_split))' in src
    assert "if _reason == 'unpicked_daycut':" in src, 'the carry is split by cause family'
    # THE ORDERING. The boundary test precedes the fold of this batch's clocks/cut/depths, so
    # a day closes on ITS last batch's state (the log-only ledger mis-attributed every first
    # batch). Now a call order rather than a statement order, and the ledger's own test
    # sabotages the reverse to prove the rule is load-bearing.
    assert src.index('asm.shift.advance_to(_d)') < src.index('asm.shift.note('),         'the day boundary must be tested BEFORE this batch folds in'
    # THE RUN-END FLUSH, the defect this gate was written for: a flush nested inside
    # `if pb:` silently writes nothing whenever n_batches divides the checkpoint cadence.
    #
    # THERE IS NO `if pb:` ANY MORE. `CheckpointBuffer.close()` always writes, open window
    # or not, and that is where the decision lives now -- so the two writers that had been
    # lifted OUT of the guard by hand (this ledger, and the censored yard tail) came back
    # inside it, and their two explanatory paragraphs went with the guard. What this gate
    # pins is therefore the DECISION rather than a statement's indent.
    assert 'if asm.pb:' not in src, (
        'the `if pb:` proxy guard is back -- one of thirteen buffers standing in for "is '
        'there an unflushed window", which is the shape that lost the final day twice')
    assert 'asm.buf.close(' in src, 'the run end no longer closes the buffer'
    assert "asm.buf.append('shift_days'," in src and '_shift_close_out(*_last_day' in src, (
        "the final day's close-out no longer rides the buffer")

    from Optimization.persistence.checkpoint_buffer import CheckpointBuffer
    import inspect as _i
    close_src = _i.getsource(CheckpointBuffer.close)
    assert 'if ' not in close_src.split('"""')[-1], (
        'close() grew a condition; "a run-end close always writes" is the whole decision')
def test_a_nonzero_put_swap_coef_is_an_error_under_the_era_at_the_cli_and_at_the_seam():
    """Gap 2 of "Close the put closed form's three known gaps": the derivation has no
    cart-swap term and the single queue the era runs never reads one, so a nonzero
    coefficient would be recorded and ignored.  Refused at the parser AND at the derivation
    seam (a programmatic launch skips the parser); a split queue set is refused at the seam
    too, where it used to be parser-only."""
    from Optimization.run_simulation import _check_era_flags
    from Optimization.simdriver.workunits import refuse_unpriceable_put
    with pytest.raises(SystemExit, match='put-swap-coef'):
        _check_era_flags(_ns(shift_drain_or_cap=True, put_swap_coef=30.0), explicit=set())
    assert _check_era_flags(_ns(shift_drain_or_cap=True, put_swap_coef=0.0),
                            explicit={'shift_drain_or_cap'})
    assert _check_era_flags(_ns(put_swap_coef=30.0), explicit={'put_swap_coef'}) == [], \
        'flag-off keeps the coefficient'
    with pytest.raises(ValueError, match='put_swap_coef'):
        refuse_unpriceable_put({'put_queue_split': False, 'put_swap_coef': 30.0})
    with pytest.raises(ValueError, match='put_queue_split'):
        refuse_unpriceable_put({'put_queue_split': True, 'put_swap_coef': 0.0})
    assert refuse_unpriceable_put({'put_queue_split': False, 'put_swap_coef': 0.0}) is None
    assert refuse_unpriceable_put({}) is None
