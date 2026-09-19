"""test_worker_recycling_pin.py — the recycling pin is a DECISION, so it gets a test.

`max_tasks_per_child` is pinned at 1: one fresh process per job. The pin is not a default, it is
a hardcoded `recycle = 1` inside `supervisor._sim_executor` -- the ONE place the sim pool's
executor is built since the flat work pool (2026-09-19) -- plus a CLI warning when a larger
value is asked for. It is there because the first run that actually honoured a larger value
deadlocked at the cell boundary — cell 1 finished all 244 arms, then the pool sat at zero CPU
with one live worker of eighteen and never shut down.

## Why this file, and not a deadlock reproduction

The plan for this work asked for a test that reproduces the deadlock so the pin could be
re-evaluated on evidence. It is not written here, deliberately:

  * reproducing it needs a REAL two-cell run at ~18 workers — minutes to hours, not a test tier;
  * the failure mode is a HANG, and a test that hangs is worse than no test. Under a watchdog it
    becomes a timeout, and a timeout on a multi-minute fixture is the most flake-prone shape
    there is.

So what is tested is the thing that actually protects the decision: **the pin cannot be removed
by ACCIDENT.** Removing it on purpose is a different act and these tests are meant to be edited
when that happens — with the safeguard in place, not before. The blocker is detection: the
failure mode is a HANG, and the pool retries on worker DEATH, so nothing notices. Resume
itself is sound (a hard mid-flight kill resumes to 272/272 from `--resume DIR` alone); what is
missing is a stall detector to turn the hang into the death the retry path already handles.

A future refactor that plumbs the parameter through — which is exactly how this bug arrived,
since `_run_whatif_matrix` silently dropped it for months and every run recycled at 1 without
anyone knowing — fails here and has to argue with the evidence first.  The pin escaped this
file once already: the 2026-09-19 pool draft hardcoded the value in a SECOND executor factory
that nothing checked.  The pool class itself therefore builds no executor of its own
(`executor_factory` is required), so there is exactly one sim factory to pin.

## And the saving it would buy is now in doubt

The pin's own docstring prices recycling at "~10 s of spawn per job against a job that runs for
minutes", and notes that "workers reload their assets per job anyway". The 2026-09-18 wall split
sharpened that second clause: the per-arm startup term is **25 s → 81 s per wave and rising
(k=0.56)** — it scales with the catalogue, so it is catalogue LOADING, not interpreter spawn.
Recycling a worker does not avoid a reload the worker performs per job regardless. Anyone
reopening this should measure the spawn/load split first; the prize may be the smaller half.

Run:  python -m pytest Tests/unit/test_worker_recycling_pin.py -q
"""
from __future__ import annotations

import ast
import inspect
import re

from Optimization.simdriver import supervisor, workpool


def _factory_src() -> str:
    return inspect.getsource(supervisor._sim_executor)


def test_the_sim_executor_hardcodes_recycle_at_one():
    """The literal assignment, not a default — a default is overridable by a caller."""
    src = _factory_src()
    assert re.search(r'^\s*recycle\s*=\s*1\s*$', src, re.M), (
        'supervisor._sim_executor no longer pins `recycle = 1`. That pin is why a cell boundary '
        'does not deadlock; removing it needs the evidence in this file\'s docstring, not a '
        'tidy-up.')


def test_the_pool_is_built_with_the_pinned_value():
    src = _factory_src()
    assert 'max_tasks_per_child=recycle' in src, (
        '_sim_executor must build the executor from its `recycle` literal, so the pin is the '
        'single place the value is decided')
    assert 'max_tasks_per_child' not in inspect.signature(supervisor._sim_executor).parameters, (
        'the factory takes no recycling argument: a caller cannot plumb the CLI value through')


def test_the_analysis_executor_is_deliberately_unpinned():
    """The other factory: analysis jobs are seconds long and share loaded contexts across
    co-scheduled graphs, so recycling would cost a context reload per graph.  Pinned as
    NOT pinned, so a tidy-up that copies the sim pin here has to read why."""
    from Optimization import run_analysis
    src = inspect.getsource(run_analysis._analysis_executor)
    assert 'max_tasks_per_child' not in src
    assert 'context' in src.lower(), 'the reason the analysis pool recycles is gone from its source'


def test_the_pool_class_builds_no_executor_of_its_own():
    """The pin escaped once into a second factory nobody checked.  `WorkPool` takes its
    executor from the caller and names `max_tasks_per_child` nowhere."""
    src = inspect.getsource(workpool)
    assert 'max_tasks_per_child' not in src, (
        'workpool.py builds an executor with a recycling value of its own -- the pin now lives '
        'in two places and this file checks one')
    params = inspect.signature(workpool.WorkPool.__init__).parameters
    assert params['executor_factory'].default is inspect.Parameter.empty, (
        'executor_factory must be REQUIRED, or a default factory becomes the second pin')


def test_the_cli_refuses_a_larger_value_loudly():
    """A silently ignored flag is how this became invisible for months."""
    import pathlib
    src = pathlib.Path(inspect.getsourcefile(supervisor)).parent.parent / 'run_simulation.py'
    text = src.read_text(encoding='utf-8')
    assert 'max_tasks_per_child != 1' in text, 'run_simulation no longer checks the flag'
    guard = text.split('max_tasks_per_child != 1', 1)[1][:600]
    assert 'ignored' in guard and 'deadlock' in guard, (
        'the warning must say the flag is IGNORED and why — a warning that does not name the '
        'deadlock trains the next reader to raise the value again')


def test_the_reason_survives_in_the_source():
    """NON-VACUITY of the tests above: they pin mechanism, this pins the argument.

    A pin whose reason is deleted is a pin the next reader removes.
    """
    src = _factory_src()
    for phrase in ('DEADLOCK', 'cell boundary'):
        assert phrase.lower() in src.lower(), (
            f'_sim_executor no longer records {phrase!r}; the pin has lost its justification')
    tree = ast.parse(inspect.getsource(supervisor))
    doc = next((ast.get_docstring(n) for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == '_sim_executor'), '') or ''
    assert len(doc) > 400, 'the pin\'s reasoning has been trimmed to a one-liner'
