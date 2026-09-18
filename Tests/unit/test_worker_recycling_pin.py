"""test_worker_recycling_pin.py — the recycling pin is a DECISION, so it gets a test.

`max_tasks_per_child` is pinned at 1: one fresh process per job. The pin is not a default, it is
a hardcoded `recycle = 1` inside `_supervise` that SHADOWS the parameter, plus a CLI warning when
a larger value is asked for. It is there because the first run that actually honoured a larger
value deadlocked at the cell boundary — cell 1 finished all 244 arms, then the pool sat at zero
CPU with one live worker of eighteen and never shut down.

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
failure mode is a HANG, and `_supervise` retries on worker DEATH, so nothing notices. Resume
itself is sound (a hard mid-flight kill resumes to 272/272 from `--resume DIR` alone); what is
missing is a stall detector to turn the hang into the death the retry path already handles.

A future refactor that plumbs the parameter through — which is exactly how this bug arrived,
since `_run_whatif_matrix` silently dropped it for months and every run recycled at 1 without
anyone knowing — fails here and has to argue with the evidence first.

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

from Optimization.simdriver import supervisor


def _supervise_src() -> str:
    return inspect.getsource(supervisor._supervise)


def test_supervise_hardcodes_recycle_at_one():
    """The literal assignment, not a default — a default is overridable by a caller."""
    src = _supervise_src()
    assert re.search(r'^\s*recycle\s*=\s*1\s*$', src, re.M), (
        'supervisor._supervise no longer pins `recycle = 1`. That pin is why a cell boundary '
        'does not deadlock; removing it needs the evidence in this file\'s docstring, not a '
        'tidy-up.')


def test_the_parameter_is_accepted_and_deliberately_shadowed():
    """`max_tasks_per_child` is in the signature and must NOT reach the pool."""
    params = inspect.signature(supervisor._supervise).parameters
    assert 'max_tasks_per_child' in params, 'the caller still passes it; keep accepting it'
    src = _supervise_src()
    # every pool construction in _supervise must use `recycle`, never the parameter
    for call in re.findall(r'_run_pool\([^)]*\)', src, re.S):
        assert 'max_tasks_per_child' not in call, (
            f'_supervise forwards max_tasks_per_child to the pool again: {call!r}. '
            f'That is the exact regression the pin exists to prevent.')
        assert 'recycle' in call, f'_run_pool no longer receives the pinned value: {call!r}'


def test_the_pool_is_built_with_the_pinned_value():
    src = inspect.getsource(supervisor._run_pool)
    assert 'max_tasks_per_child=recycle' in src, (
        '_run_pool must build the executor from its `recycle` argument, so the pin in '
        '_supervise is the single place the value is decided')


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
    src = _supervise_src()
    for phrase in ('DEADLOCK', 'cell boundary'):
        assert phrase.lower() in src.lower(), (
            f'_supervise no longer records {phrase!r}; the pin has lost its justification')
    tree = ast.parse(inspect.getsource(supervisor))
    doc = next((ast.get_docstring(n) for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == '_supervise'), '') or ''
    assert len(doc) > 400, 'the pin\'s reasoning has been trimmed to a one-liner'
