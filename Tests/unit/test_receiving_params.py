"""test_receiving_params.py — the receiving crew reaches a real run, through all five seams.

A config knob in this repo has FIVE wirings and missing any one of them fails SILENTLY:

  1. declared in `settings.py`
  2. threaded into `CONFIG`, via an accessor read at CALL time
  3. a CLI flag
  4. recorded in `run_spec.json` AND restored in both `_apply_run_spec` (resume) and
     `run_analysis._apply_run_shape` (standalone re-analysis)
  5. carried in `workunits._shared`, the picklable worker payload

The fifth is the one that has shipped broken twice. The pool is SPAWN, not fork: a worker
re-imports `sim_config` and gets pristine module defaults, so a knob absent from the payload
parses on the command line, is echoed in the log, is written to the run spec, is restored on
resume — and is ignored by every one of the 24 workers that actually run the simulation.

There is a sixth trap specific to this knob, and it is why `recv_crew_spec` is copied from
`work_day_spec` and NOT from `put_crew_spec`: that one reads `_s.PUT_CREW_SIZE` directly and
there is no `CONFIG['global']['put_crew_*']` key at all. A `--recv-*` flag writes CONFIG, so
an accessor built on that template would accept the flag and ignore it forever, and a
standalone re-analysis would size a dock the run never had.

Run:  python -m pytest Tests/unit/test_receiving_params.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import CONFIG, recv_crew_spec

_KEYS = ('recv_crew_size', 'recv_day_seconds', 'recv_day_origin')


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    before = {k: CONFIG['global'][k] for k in _KEYS}
    yield CONFIG['global']
    CONFIG['global'].update(before)


# ── seam 1: declared ──────────────────────────────────────────────────────────────

def test_the_defaults_are_no_crew():
    """Off by default, because a receiving crew changes WHEN merchandise reaches a put queue
    and therefore which units are binned in which batch. It can never be a silent default."""
    assert _s.RECV_CREW_SIZE == 0
    assert _s.RECV_DAY_SECONDS is None
    assert _s.RECV_DAY_ORIGIN == 0.0


def test_there_is_no_receiving_speed_or_mode_knob():
    """Deliberate, and worth pinning because adding one looks like an obvious improvement.

    An unload has no travel term — there is no dock coordinate anywhere in the model — so
    nothing consumes a speed. Four constants would assert a distinction the model cannot
    express, and a sweep over a mode knob would publish "mode makes no difference to
    receiving". Crew SIZE is the only lever, because size is the number of clocks.

    `RECV_INTERCEPT_SCALE` is not physics: it is the receiving PRICE as a scalar of
    put-away's (ADR-0001), the one declared way the crews' numbers may differ. It moves
    seconds, not a makespan's shape, and it is allowed here for that reason.
    """
    for name in dir(_s):
        assert not name.startswith('RECV_') or name in (
            'RECV_CREW_SIZE', 'RECV_DAY_SECONDS', 'RECV_DAY_ORIGIN', 'RECV_INTERCEPT_SCALE'), (
            f'{name} declares receiving-specific physics the cost model cannot use')


# ── seam 2: CONFIG, read at call time ─────────────────────────────────────────────

def test_the_keys_are_in_config():
    for k in _KEYS:
        assert k in CONFIG['global'], k


def test_the_accessor_reads_config_and_not_the_module(restore):
    """THE trap that `put_crew_spec` falls into. If this read `_s.RECV_CREW_SIZE` directly,
    the CLI flag — which writes CONFIG — would be accepted and ignored forever."""
    restore.update(recv_crew_size=3, recv_day_seconds=7200.0, recv_day_origin=100.0)
    spec = recv_crew_spec()
    assert spec['size'] == 3
    assert spec['day_seconds'] == 7200.0
    assert spec['day_origin'] == 100.0

    src = inspect.getsource(recv_crew_spec)
    assert '_s.RECV_' not in src, (
        'the accessor reads settings directly, so every CLI flag writing CONFIG is inert')


def test_no_crew_is_none_and_not_an_empty_dict(restore):
    """The off switch. None is checkable as "nothing was constructed"; an empty dict is an
    object something downstream can still fold into a clock or a snapshot."""
    restore['recv_crew_size'] = 0
    assert recv_crew_spec() is None
    restore['recv_crew_size'] = 1
    assert isinstance(recv_crew_spec(), dict)


def test_a_zero_length_day_is_not_the_same_as_no_day(restore):
    """`or` would turn "the crew has no day today" into "the crew has no whistle" — the
    exact inverse. Resolved with an explicit `is not None`, and rejected at the parser too,
    so the same mistake is guarded twice."""
    restore.update(recv_crew_size=1, recv_day_seconds=0.0)
    assert recv_crew_spec()['day_seconds'] == 0.0
    restore['recv_day_seconds'] = None
    assert recv_crew_spec()['day_seconds'] is None


# ── seam 3: the CLI ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('flag', ['--recv-crew-size', '--recv-day-seconds',
                                  '--recv-day-origin'])
def test_each_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), (
        f'{flag} is reachable only by editing settings.py, which is in SHAPE_SOURCES — '
        f'changing a VALUE there trips the run-tree preflight')


def test_the_crew_size_accepts_zero_but_not_negative():
    """0 is the off switch and must parse. Negative would reach `crew_clock.new_clocks` and
    raise three layers down, or be swallowed by a truthiness guard on the way."""
    import argparse

    from Optimization.run_simulation import _nonneg_int
    assert _nonneg_int('0') == 0
    assert _nonneg_int('3') == 3
    with pytest.raises(argparse.ArgumentTypeError):
        _nonneg_int('-1')


def test_the_day_length_rejects_zero_at_the_parser():
    """`--n-batches 0` was once accepted, discarded by a truthiness guard, and the run went
    ahead on CONFIG's value. A duration meaningless at zero fails where argparse can name
    the flag."""
    import argparse

    from Optimization.run_simulation import _positive_float
    assert _positive_float('3600') == 3600.0
    for bad in ('0', '-5'):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float(bad)


# ── seam 4: recorded, and restored on BOTH paths ──────────────────────────────────

def test_recorded_in_the_run_spec_and_restored_on_resume():
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation)
    for k in _KEYS:
        assert src.count(f"'{k}'") >= 2, (
            f'{k} must be WRITTEN to run_spec and RESTORED on resume; two runs with '
            f'different docks are otherwise indistinguishable after the fact')


def test_restored_by_a_standalone_reanalysis():
    """A re-analysis that rebuilt from this checkout's settings would size a dock the run
    never had. A pre-field spec yields 0/None, which correctly means "no receiving crew"."""
    from Optimization import run_analysis
    src = inspect.getsource(run_analysis)
    for k in _KEYS:
        assert f"spec.get('{k}')" in src, f're-analysis does not restore {k}'


# ── seam 5: the worker payload ────────────────────────────────────────────────────

def test_the_crew_reaches_the_worker_payload():
    """THE fifth seam. Without it the flag parses, the spec records it, the resume restores
    it, and every spawned worker runs with no dock at all."""
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits)
    assert 'recv_crew           = recv_crew_spec(),' in src, (
        'the worker payload does not carry the receiving crew; a spawned worker re-imports '
        'sim_config and gets pristine defaults')


def test_the_worker_reads_the_crew_only_from_its_arguments():
    """A worker is SPAWNED. Any value read from the module instead of the payload silently
    reverts to the default, and the arm would finish having received for free while its run
    spec recorded a crew."""
    import ast

    from Optimization.simdriver import strategy_runner as sr
    tree = ast.parse(inspect.getsource(sr._run_strategy_worker_impl))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    body = ast.unparse(tree)
    assert "args.get('recv_crew')" in body, 'the crew no longer comes from the payload'
    assert 'recv_crew_spec' not in body, (
        'the worker calls the accessor directly; a spawned worker would get the default')


# ── the runner gives it its OWN day and its OWN carry ─────────────────────────────

def test_the_receive_whistle_is_not_the_put_whistle():
    """Reusing `_put_deadline` would be arithmetically well-formed and wrong: it is the PUT
    crew's remaining day, already shrunk by the PUT crew's backlog. The only symptom would
    be a `recv_cut` that reads like a legitimately short day."""
    import ast

    from Optimization.simdriver import strategy_runner as sr
    body = ast.unparse(ast.parse(inspect.getsource(sr._run_strategy_worker_impl)))
    assert 'recv_deadline=_recv_deadline' in body
    assert 'recv_deadline=_put_deadline' not in body, 'the two crews share one whistle'
    assert '_recv_day.end_of' in body, 'the receive whistle is not built from its own day'
    assert 'max(arm_clock, recv_clock)' in body, (
        'the receive whistle is not measured against its own carry')


def test_the_day_origin_reaches_the_workday_and_moves_the_whistle():
    """A dock that opens before the pickers is a real shift pattern, and `--recv-day-origin`
    is where it goes. Two halves, because either alone would pass on a dead knob:

      * the runner builds the `WorkDay` WITH the origin (a source check — the value has to
        get there);
      * a shifted origin produces a different remaining-day (a behaviour check — getting
        there has to matter).

    `WorkDay.remaining` is not what the runner calls, so the arithmetic is reproduced the way
    the runner does it: `end_of(index_of(t)) - t`.
    """
    import ast as _ast

    from Warehouse.kernel.timeline import WorkDay
    from Optimization.simdriver import strategy_runner as sr

    body = _ast.unparse(_ast.parse(inspect.getsource(sr._run_strategy_worker_impl)))
    assert "origin=_recv_spec['day_origin']" in body, (
        'the receiving day is built without its origin, so --recv-day-origin is dead config')

    flat = WorkDay(length=3600.0, origin=0.0)
    early = WorkDay(length=3600.0, origin=1800.0)

    def remaining(day, t):
        return day.end_of(day.index_of(t)) - t

    # At the same instant the two docks have different amounts of day left, which is the
    # whole point of the knob.
    assert remaining(flat, 1000.0) == 2600.0
    assert remaining(early, 1000.0) == 800.0
    assert remaining(flat, 1000.0) != remaining(early, 1000.0)
    # ...and the shift is a shift, not a shortening: both days are still 3600 long.
    assert remaining(early, 1800.0) == 3600.0


def test_batch_resume_is_refused_when_a_dock_is_configured():
    """The dock's standing contents live in the worker and are in no checkpoint. Resuming at
    batch N discards them — and unlike the pick carry, that merchandise is still credited to
    the inventory position, so the SKU never re-orders to replace it. The run would report
    labour it did not do AND inventory it does not have."""
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits._plan_strategy_start)
    assert 'receiving' in src and 'roll_over or receiving' in src
    assert 'raise RuntimeError' in src
