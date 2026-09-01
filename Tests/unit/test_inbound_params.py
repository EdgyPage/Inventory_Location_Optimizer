"""test_inbound_params.py — the inbound family reaches a real run, through all five seams.

Every knob the inbound effort added (the standing yard, the gain arms, the futuresight window,
the lead distribution) declared seams 1-2 and DEFERRED seams 3-4 to "the first sweep", on the
precedent the standing-yard build set. The funnel is the first sweep, so the debt fell due
together — and it is exactly the debt that fails silently:

  1. declared in `settings.py`
  2. threaded into `CONFIG`, read at CALL time by `inbound_spec()`
  3. a CLI flag
  4. recorded in `run_spec.json` AND restored in both `_apply_run_spec` (resume) and
     `run_analysis._apply_run_shape` (standalone re-analysis)
  5. carried in `workunits._shared`, the picklable worker payload

Seam 5 the family has had since it was built: the whole thing crosses as ONE `inbound` record.
Seams 3 and 4 are what this file pins, plus the two things that only matter once they exist:

  * a phase-2 cell that cannot record its lead shape and its fee threshold is not
    re-analysable, and
  * the yard's fee report DERIVES overage at analysis time from the run's recorded threshold —
    which is the whole reason `yard_trailers` stores raw stamps. Reading this checkout's
    threshold instead would quietly report about a different configuration.

Run:  python -m pytest Tests/unit/test_inbound_params.py -q
"""
from __future__ import annotations

import argparse
import ast
import inspect
import logging

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import CONFIG, INBOUND_KEYS, inbound_spec


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    before = {k: CONFIG['global'][k] for k in INBOUND_KEYS}
    yield CONFIG['global']
    CONFIG['global'].update(before)


def _main_tree():
    """`run_simulation.main` as an AST — the parser is built inline there."""
    import Optimization.run_simulation as rs
    tree = ast.parse(inspect.getsource(rs))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == 'main')


def _add_arguments(fn):
    """{flag: {keyword: unparsed expression}} for every `parser.add_argument` under `fn`.

    Read off the AST rather than by importing and inspecting a parser object, because the
    parser is constructed inside `main` alongside the whole run. The keyword EXPRESSIONS are
    what matter here: `default=CONFIG['global'][...]` is the invariant that makes the
    unconditional override loop safe, and a parsed parser would show only the resolved value.
    """
    out = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'add_argument' and node.args):
            continue
        flags = [a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        # The loop-generated flags (`for _flag, _key, _what in (...)`) pass a NAME, not a
        # literal; their tuple literals are walked below by the caller instead.
        for flag in flags:
            out[flag] = {kw.arg: ast.unparse(kw.value) for kw in node.keywords}
    # Flags registered through a loop: collect the literal tuples feeding `_flag`.
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith('--inbound-'):
            out.setdefault(node.value, {})
    return out


# ── seam 1: declared, and INERT by default ────────────────────────────────────────

def test_the_defaults_are_no_inbound_pipeline():
    """Off unless a trailer type is named, and STRUCTURALLY so: `inbound_spec()` returns None,
    no transit is built, and the manager keeps the batch lead queue byte-identically. Naming a
    type is a RESULTS ERA, so it can never be a silent default."""
    assert _s.INBOUND_TRAILER_TYPE is None
    assert _s.INBOUND_STANDING_YARD is False
    assert _s.INBOUND_LEAD_MINUTES == 0.0 and _s.INBOUND_LEAD_SPREAD == 0.0
    assert _s.INBOUND_FUTURESIGHT_BATCHES is None
    assert inbound_spec() is None


def test_the_key_list_is_exactly_the_inbound_family_in_config():
    """One list, derived from nothing and deriving everything: the flags, the run-spec record
    and both restore sites all read it, so a new inbound knob cannot be recorded and then not
    restored — the half-fix that makes seam 4 fail silently."""
    in_config = {k for k in CONFIG['global'] if k.startswith('inbound_')}
    assert set(INBOUND_KEYS) == in_config
    assert len(INBOUND_KEYS) == len(set(INBOUND_KEYS)), 'a repeated key'


# ── seam 3: every knob has a flag, and every flag defaults FROM CONFIG ────────────

def test_every_inbound_knob_has_a_cli_flag():
    args = _add_arguments(_main_tree())
    for key in INBOUND_KEYS:
        flag = '--' + key.replace('_', '-')
        assert flag in args, f'{key} has no CLI, so it is reachable only by editing settings.py'


def test_every_inbound_flag_defaults_from_config():
    """The `--keyframe-interval` precedent, and here it is load-bearing rather than tidy.

    `main` assigns the whole family into CONFIG unconditionally (`g[k] = getattr(args, k)`), so
    a flag defaulting to a LITERAL would overwrite settings.py's value on every flag-less run —
    silently, and with a number that looks deliberate.
    """
    args = _add_arguments(_main_tree())
    for key in INBOUND_KEYS:
        kw = args['--' + key.replace('_', '-')]
        if not kw:                      # loop-registered (the three unload coefficients)
            continue
        assert kw.get('default', '').startswith("CONFIG['global']"), (
            f"--{key.replace('_', '-')} defaults to {kw.get('default')!r}, not CONFIG's value; "
            f'the unconditional override loop would make settings.py dead for this knob')


def test_the_futuresight_flag_converts_at_the_parser():
    """`_futuresight_batches` rejects `'5'` as firmly as `'oracle'` — its `w != raw` test is
    what stops a fractional value, and a numeric STRING fails it too. Without a converting
    `type=`, the flag would parse cleanly and refuse three layers down, naming the settings
    constant rather than the flag the user typed."""
    from Optimization.run_simulation import _futuresight_window
    assert _futuresight_window('all') == 'all'
    assert _futuresight_window('5') == 5
    for bad in ('oracle', '2.5', '-1'):
        with pytest.raises(argparse.ArgumentTypeError):
            _futuresight_window(bad)


# ── seam 4a: recorded in the run spec, with the un-re-derivable TAG beside it ─────

def test_the_family_is_recorded_in_the_run_spec():
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert '**{k: g[k] for k in INBOUND_KEYS}' in src, (
        'the inbound family is not written to run_spec.json')
    assert "'inbound_lead_tag'" in src, (
        'the lead TAG is un-re-derivable, so a comparison spanning a TAG change would span two '
        'different arrival schedules with nothing to detect it from')


def test_the_recorded_tag_is_the_one_the_draw_uses():
    from Inbound.transit import _LEAD_TAG
    from Optimization.run_simulation import _LEAD_TAG as recorded
    assert recorded is _LEAD_TAG, 'the run spec records a second copy of the tag, which can drift'


# ── seam 4b: BOTH restore sites, which is the half-fix that hides ────────────────

def test_a_resume_restores_the_whole_family():
    """`_apply_run_spec` overlays the saved spec onto argv. An arm that resumed without its
    yard would finish on v1's drain-everything dock; one that resumed without its lead shape
    would redraw a different arrival schedule."""
    import Optimization.run_simulation as rs
    saved = {k: 'SAVED' for k in INBOUND_KEYS}
    args = argparse.Namespace(**{k: 'ARGV' for k in INBOUND_KEYS})
    rs._apply_run_spec(args, saved, explicit=set())
    for k in INBOUND_KEYS:
        assert getattr(args, k) == 'SAVED', f'{k} is recorded but not restored on resume'


def test_a_standalone_reanalysis_restores_the_whole_family(tmp_path, restore):
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {
        'n_batches': 4,
        'inbound_trailer_type': '53', 'inbound_standing_yard': True,
        'inbound_yard_policy': 'gain_gated', 'inbound_dock_policy': 'gain_gated',
        'inbound_fee_threshold_days': 3.5, 'inbound_urgency_horizon_days': 1.75,
        'inbound_lead_minutes': 480.0, 'inbound_lead_spread': 0.7,
    })
    _apply_run_shape(str(tmp_path), logging.getLogger('t-inbound'))
    g = CONFIG['global']
    assert g['inbound_trailer_type'] == '53' and g['inbound_standing_yard'] is True
    assert g['inbound_yard_policy'] == 'gain_gated'
    assert g['inbound_fee_threshold_days'] == 3.5
    assert g['inbound_urgency_horizon_days'] == 1.75
    assert g['inbound_lead_spread'] == 0.7


def test_a_pre_field_spec_restores_inbound_OFF_not_this_checkout(tmp_path, restore):
    """The `sampler` reasoning, applied to a whole family. A run_spec written before the
    inbound fields existed belongs to a run that had no inbound pipeline — so its absence must
    restore OFF, never whatever this checkout happens to be configured with. Otherwise a
    re-analysis of an archived run reads a fee threshold and a lead shape it never ran under."""
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    CONFIG['global'].update(inbound_trailer_type='53', inbound_standing_yard=True,
                            inbound_fee_threshold_days=9.0)
    _write_run_spec(str(tmp_path), {'n_batches': 4, 'max_skus': 100})
    _apply_run_shape(str(tmp_path), logging.getLogger('t-prefield'))
    for k in INBOUND_KEYS:
        assert CONFIG['global'][k] is None, f'{k} kept this checkout\'s value'
    assert inbound_spec() is None, 'a pre-field run must re-analyse as having no inbound'


# ── what the recording BUYS: the fee is derived from the RUN's threshold ──────────

def test_the_recorded_threshold_reaches_the_evaluation_through_the_job(restore):
    """`EvalContext.fee_threshold_days` runs in a SPAWNED analysis worker, which re-imports
    sim_config and gets pristine defaults — so CONFIG is not a channel between the parent and
    the evaluation. The pickled job is, which is why the value is stamped onto `sim_result`."""
    from Optimization.run_analysis import _sim_result_from_meta
    CONFIG['global']['inbound_fee_threshold_days'] = 3.5
    meta = {'name': 'store', 'run_dir': 'x', 'strategies': []}
    assert _sim_result_from_meta(meta)['inbound_fee_threshold_days'] == 3.5


def test_the_context_prefers_the_recorded_threshold_and_says_so_when_it_falls_back(caplog):
    from Optimization.Performance_Evaluations.core.context import EvalContext
    from types import SimpleNamespace
    log = logging.getLogger('t-fee')

    # The METHOD, against a stand-in: constructing a real context opens every arm's sim DB, and
    # this accessor reads four attributes and nothing else. Calling it unbound keeps the test on
    # the code that ships rather than on a re-implementation of it.
    def _ask(sim_result):
        return EvalContext.fee_threshold_days(
            SimpleNamespace(_fee_days=None, sim_result=sim_result, log=log, name='store'))

    assert _ask({'inbound_fee_threshold_days': 3.5}) == 3.5
    with caplog.at_level(logging.INFO, logger='t-fee'):
        assert _ask({}) == float(_s.INBOUND_FEE_THRESHOLD_DAYS)
    assert any('no recorded fee threshold' in r.message for r in caplog.records), (
        'a run that predates recording must SAY it is using this build\'s default')


# ── seam 5: already paid, and pinned so it stays paid ─────────────────────────────

def test_the_family_crosses_the_worker_payload_as_one_record():
    """The seam `settings.py` does not name. A worker is SPAWNED: it re-imports sim_config and
    gets pristine module defaults, so a knob absent from the payload parses on the command
    line, is echoed in the log, is written to the run spec, is restored on resume — and is
    ignored by every worker that actually runs the simulation."""
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits)
    assert 'inbound             = inbound_spec()' in src, (
        'the worker payload does not carry the inbound family')
