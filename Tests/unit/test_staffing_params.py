"""test_staffing_params.py — the declared picker counts reach a real run, through all six seams.

Pickers are THE one declared staffing input of the calibrated era
(.scratch/department-calibration, "Define the calibrated era"): every other crew is derived
from them.  Until this seam existed they were the one department with no runtime knob at
all -- two compile-time constants, restated in every pick-config module, with no flag, no
run-spec record and no payload carriage.

A config knob in this repo has FIVE wirings and missing any one of them fails SILENTLY:

  1. declared in `settings.py`
  2. threaded into `CONFIG`, via an accessor read at CALL time
  3. a CLI flag
  4. recorded in `run_spec.json` AND restored in both `_apply_run_spec` (resume) and
     `run_analysis._apply_run_shape` (standalone re-analysis)
  5. carried in `workunits._shared`, the picklable worker payload

and this knob has a SIXTH: the evaluations run in a SPAWNED analysis worker, so the count an
evaluation prices against must be stamped onto `sim_result` by `_sim_result_from_meta` --
CONFIG is not a channel to it (memory: config-is-not-a-channel-to-an-evaluation).  Before this
seam that stamp was a literal 25, which priced every fulfillment leaf at the store's crew.

Two shape decisions from "Design the staffing record" are pinned here as well: the counts
are two flat GLOBAL keys on one spliced `STAFFING_KEYS` list (never a per-channel entry),
and a pick-config module's own `num_pickers` that DISAGREES with the channel's declared
count raises at setup -- the batch script is derived from the declared crew, so an arm may
not field a different one.  The committed modules therefore name no count at all.

Run:  python -m pytest Tests/unit/test_staffing_params.py -q
"""
from __future__ import annotations

import argparse
import inspect
import logging

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import (
    CONFIG, STAFFING_KEYS, channel_pickers, staffing_spec, k_pickers,
)
from Optimization.simconfig import PICK_CONFIGS
from Optimization.simconfig.constants import _FF_PICKERS, _STORE_PICKERS, PROVENANCE

_DEFAULTS = {'store_pickers': _STORE_PICKERS, 'ff_pickers': _FF_PICKERS}


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    before = {k: CONFIG['global'].get(k) for k in STAFFING_KEYS}
    yield CONFIG['global']
    CONFIG['global'].update(before)


# ── seam 1: declared ──────────────────────────────────────────────────────────────

def test_the_defaults_are_the_leaf_constants():
    """settings names them; the leaf module holds the values, because the staffing records
    live below sim_config and may not import it back."""
    assert _s.STORE_PICKERS is _STORE_PICKERS
    assert _s.FF_PICKERS is _FF_PICKERS
    assert STAFFING_KEYS == ('store_pickers', 'ff_pickers')


def test_settings_names_the_flag_beside_each_knob():
    src = inspect.getsource(_s)
    assert '--store-pickers' in src and '--ff-pickers' in src


@pytest.mark.parametrize('spec', PICK_CONFIGS, ids=lambda s: s.name)
def test_no_committed_pick_config_restates_a_picker_count(spec):
    """A module literal would DISAGREE with the channel the moment a flag moved the count,
    and disagreement raises -- so a restated literal is a knob that cannot be turned."""
    assert 'num_pickers' not in spec.cfg, (
        f'{spec.name} restates num_pickers; the channel value is the only legal thing it '
        f'could say, and saying nothing says exactly that')


def test_the_provenance_enum_is_the_five_agreed_values():
    assert PROVENANCE == ('assumed', 'declared', 'seed', 'measured', 'derived')


# ── seam 2: CONFIG + a call-time accessor ─────────────────────────────────────────

def test_the_keys_are_global_and_not_per_channel():
    for k in STAFFING_KEYS:
        assert k in CONFIG['global'], k
    for ch in ('store', 'fulfillment'):
        assert 'num_pickers' not in CONFIG['channels'][ch], (
            f'{ch} carries a second copy of its picker count; the global key is the one home')


def test_the_accessor_reads_config_and_not_the_module(restore):
    """THE trap that `put_crew_spec` falls into. If this read `_s.STORE_PICKERS` directly,
    the CLI flag -- which writes CONFIG -- would be accepted and ignored forever."""
    restore.update(store_pickers=7, ff_pickers=5)
    assert channel_pickers('store') == 7
    assert channel_pickers('fulfillment') == 5
    assert k_pickers() == 7
    assert staffing_spec() == {'store_pickers': 7, 'ff_pickers': 5}

    src = inspect.getsource(channel_pickers) + inspect.getsource(staffing_spec)
    assert '_s.' not in src, (
        'the accessor reads settings directly, so every CLI flag writing CONFIG is inert')


def test_a_pre_record_none_resolves_to_the_leaf_default(restore):
    """`_apply_run_shape` restores None for a run that predates the record. That run
    fielded the compile-time constant of its day, which is what the constant still is."""
    restore.update(store_pickers=None, ff_pickers=None)
    assert staffing_spec() == _DEFAULTS


@pytest.mark.parametrize('bad', [0, -3])
def test_a_non_positive_count_raises_at_the_accessor(restore, bad):
    restore['store_pickers'] = bad
    with pytest.raises(ValueError):
        channel_pickers('store')


def test_an_unknown_channel_is_a_keyerror_not_a_share():
    with pytest.raises(KeyError):
        channel_pickers('returns')


# ── the disagreement rule ─────────────────────────────────────────────────────────

class _NoOrders:
    orders: list = []


def _with_store_config(monkeypatch, cfg: dict):
    from Optimization.config.sim_config import STORE_CONFIGS
    monkeypatch.setitem(CONFIG['channels']['store'], 'configs', [{**STORE_CONFIGS[0], **cfg}])


def test_a_disagreeing_per_arm_count_raises_at_setup(monkeypatch, restore):
    from Optimization.simdriver.workunits import _channel_runs_for
    restore['store_pickers'] = 25
    _with_store_config(monkeypatch, {'num_pickers': 7})
    with pytest.raises(ValueError, match='declares num_pickers=7'):
        _channel_runs_for(_NoOrders())


def test_a_restated_per_arm_count_stays_legal(monkeypatch, restore):
    from Optimization.simdriver.workunits import _channel_runs_for
    restore['store_pickers'] = 25
    _with_store_config(monkeypatch, {'num_pickers': 25})
    _mixed, runs = _channel_runs_for(_NoOrders())
    assert runs and runs[0][0].picker.num_pickers == 25


def test_the_channel_is_built_from_the_declared_count(monkeypatch, restore):
    """The value that reaches `PickerProfile` -- and from there the Crew, the run params and
    the payload -- is the global key, read at call time."""
    from Optimization.simdriver.workunits import _channel_runs_for
    restore['store_pickers'] = 9
    _with_store_config(monkeypatch, {})
    _mixed, runs = _channel_runs_for(_NoOrders())
    ch, _cfg = runs[0]
    assert ch.picker.num_pickers == 9
    assert ch.picker.cost.num_pickers == 9


# ── seam 3: the CLI ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('flag', ['--store-pickers', '--ff-pickers'])
def test_each_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), (
        f'{flag} is reachable only by editing settings.py, which is in SHAPE_SOURCES')


def test_the_flag_rejects_a_zero_crew_at_the_parser():
    """A crew of none is not a configuration; `_positive_int` names the flag and exits 2
    instead of a truthiness guard turning it into the default downstream."""
    from Optimization.run_simulation import _positive_int
    assert _positive_int('7') == 7
    for bad in ('0', '-1'):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int(bad)


def test_the_override_loop_writes_every_staffing_key():
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation.main)
    assert 'for _k in STAFFING_KEYS:\n        g[_k] = getattr(args, _k)' in src


# ── seam 4: recorded, and restored on BOTH paths ──────────────────────────────────

def _nested_spec(store=7, ff=5) -> dict:
    return {'staffing': {'inputs': {'store_pickers': store, 'ff_pickers': ff},
                         'provenance': {'store_pickers': 'declared',
                                        'ff_pickers': 'assumed'}}}


def test_recorded_as_one_nested_staffing_key():
    """One `staffing` record with `inputs` and `provenance` sub-blocks, not flat entries:
    the record grows (the derivation adds `derived`) and is stamped whole onto sim_result."""
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation.main)
    assert "'staffing': {" in src
    assert "'inputs'    : staffing_spec()," in src
    assert "'provenance': {k: ('declared' if k in explicit else 'assumed')" in src


def test_a_resume_restores_the_inputs_from_the_nested_record():
    from Optimization.run_simulation import _apply_run_spec
    args = argparse.Namespace(store_pickers=25, ff_pickers=20)
    _apply_run_spec(args, _nested_spec(), explicit=set())
    assert (args.store_pickers, args.ff_pickers) == (7, 5)


def test_an_explicit_flag_still_wins_on_resume_with_a_note():
    from Optimization.run_simulation import _apply_run_spec
    args = argparse.Namespace(store_pickers=11, ff_pickers=20)
    _comp, notes = _apply_run_spec(args, _nested_spec(), explicit={'store_pickers'})
    assert args.store_pickers == 11 and args.ff_pickers == 5
    assert any('store_pickers' in n for n in notes)


def test_a_pre_record_spec_leaves_the_flags_alone_on_resume():
    from Optimization.run_simulation import _apply_run_spec
    args = argparse.Namespace(store_pickers=25, ff_pickers=20)
    _apply_run_spec(args, {'n_batches': 3}, explicit=set())
    assert (args.store_pickers, args.ff_pickers) == (25, 20)


def test_a_standalone_reanalysis_restores_the_inputs(tmp_path, restore):
    """The half of seam 4 with no observable symptom: a re-analysis that priced against
    this checkout's count would report a crew the run never fielded."""
    from Optimization import run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {'n_batches': 3, **_nested_spec()})
    run_analysis._apply_run_shape(str(tmp_path), logging.getLogger('t'))
    assert staffing_spec() == {'store_pickers': 7, 'ff_pickers': 5}
    assert run_analysis._RUN_STAFFING == _nested_spec()['staffing']


def test_a_pre_record_run_reanalyses_at_the_leaf_defaults(tmp_path, restore):
    """Never this checkout's CONFIG: a flag in this process could have moved it."""
    from Optimization import run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    restore.update(store_pickers=99, ff_pickers=98)          # "this checkout", perturbed
    _write_run_spec(str(tmp_path), {'n_batches': 3})
    run_analysis._apply_run_shape(str(tmp_path), logging.getLogger('t'))
    assert staffing_spec() == _DEFAULTS
    assert run_analysis._RUN_STAFFING is None


# ── seam 5: the worker payload ────────────────────────────────────────────────────

def test_the_record_reaches_the_worker_payload():
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits)
    assert 'staffing            = staffing_spec(),' in src, (
        'the worker payload does not carry the staffing record')
    assert 'k_pickers           = ch.picker.num_pickers,' in src, (
        'the count the worker sizes its crew from no longer rides the payload')


def test_the_worker_checks_its_crew_against_the_record_and_never_the_module():
    """A worker is SPAWNED. It sizes from `k_pickers`, checks that against the record it was
    handed, and calls no accessor -- an accessor would return the pristine default."""
    import ast

    from Optimization.simdriver import strategy_runner as sr

    def _code(fn) -> str:
        """The function's CODE, docstrings stripped -- prose may name the accessor."""
        tree = ast.parse(inspect.getsource(fn))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body.pop(0)
        return ast.unparse(tree)

    body = _code(sr._run_strategy_worker_impl)
    assert '_check_declared_crew(args, k_pickers)' in body, 'the worker no longer checks'
    helper = _code(sr._check_declared_crew)
    assert "args.get('staffing')" in helper, 'the record no longer comes from the payload'
    for src in (body, helper):
        assert 'staffing_spec' not in src and 'channel_pickers' not in src, (
            'the worker calls an accessor directly; a spawned worker would get the default')


def test_the_check_reads_the_payload_in_the_shape_the_parent_sends(restore):
    """The payload carries `staffing_spec()` -- the INPUTS dict, flat -- and the check must
    index it that way.  The first build indexed a nested `['inputs']` and every arm of a real
    pool run died with a KeyError the harness swallowed ("these arms produced no data")."""
    from Optimization.simdriver.strategy_runner import _check_declared_crew
    restore.update(store_pickers=7, ff_pickers=5)
    payload = staffing_spec()
    _check_declared_crew({'staffing': payload, 'channel_name': 'store'}, 7)
    _check_declared_crew({'staffing': payload, 'channel_name': 'fulfillment'}, 5)


def test_a_hand_assembled_payload_that_disagrees_is_refused():
    """The check is real, not decorative: it raises on the disagreement it names, per channel,
    and an absent record (a bench harness) is nothing to check against."""
    from Optimization.simdriver.strategy_runner import _check_declared_crew
    payload = {'store_pickers': 7, 'ff_pickers': 5}
    with pytest.raises(ValueError, match='store_pickers=7'):
        _check_declared_crew({'staffing': payload, 'channel_name': 'store'}, 25)
    with pytest.raises(ValueError, match='ff_pickers=5'):
        _check_declared_crew({'staffing': payload, 'channel_name': 'fulfillment'}, 7)
    _check_declared_crew({'channel_name': 'store'}, 25)          # no record: nothing to check


# ── seam 6: stamped onto sim_result for the evaluations ───────────────────────────

def test_sim_result_carries_the_whole_record_and_the_channel(restore, monkeypatch):
    from Optimization import run_analysis
    monkeypatch.setattr(run_analysis, '_RUN_STAFFING', _nested_spec()['staffing'])
    meta = {'name': 'store', 'run_dir': 'x', 'strategies': [], 'channel': 'fulfillment'}
    sr = run_analysis._sim_result_from_meta(meta)
    assert sr['staffing'] == _nested_spec()['staffing']
    assert sr['channel'] == 'fulfillment'


def test_a_pre_record_stamp_is_a_reconstruction_marked_assumed(restore, monkeypatch):
    from Optimization import run_analysis
    monkeypatch.setattr(run_analysis, '_RUN_STAFFING', None)
    restore.update(store_pickers=None, ff_pickers=None)
    sr = run_analysis._sim_result_from_meta({'name': 'n', 'run_dir': 'x', 'strategies': []})
    assert sr['staffing'] == {'inputs': _DEFAULTS,
                              'provenance': {k: 'assumed' for k in STAFFING_KEYS}}
    assert sr['channel'] == 'store', 'a pre-channel meta is a store-only run'


def test_the_evaluation_context_reads_its_own_channels_count():
    """The literal-25 fallback is gone: a fulfillment leaf prices against ff_pickers, and a
    record without the key raises instead of quietly pricing at someone else's crew."""
    from Optimization.Performance_Evaluations.core.context import EvalContext
    src = inspect.getsource(EvalContext.__init__)
    assert "slim.get('k_pickers'" not in src, 'the literal fallback is back'
    assert "_key = 'ff_pickers' if sim_result.get('channel') == 'fulfillment' else 'store_pickers'" in src
    assert "self.k_pickers = int(_staffing['inputs'][_key])" in src
    from Optimization.run_analysis import _SLIM_KEYS
    assert 'k_pickers' not in _SLIM_KEYS, 'the store-only slice is still shipped to the worker'
