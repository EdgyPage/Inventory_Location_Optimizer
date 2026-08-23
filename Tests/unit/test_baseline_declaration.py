"""test_baseline_declaration.py — the comparison baseline is selected, not counted.

`EvalContext.base` was `strategies[0]`. That is correct only because the run harness
happens to assemble the list baseline-first — a property no chart module can see and none
of them states, while about ninety prose strings in the same package assert "vs FIFO" in
titles and axis labels. If those two ever disagreed, every number on the page would change
and no filename would.

Pinned here:

  1. the declared arm is found by MATCHING, in any position;
  2. matching is case-insensitive, because the strategy dicts spell it `FIFO` and the cost
     stage spells it `fifo`;
  3. the positional fallback still exists — `_focus_filter` runs before the baseline is
     taken, so under `focus='opt'` the FIFO arm may genuinely be absent — and it is LOUD.
     A quiet fallback is the failure mode, not the fallback itself;
  4. `cost/rollup.BASE_RULE` is the same declaration, so the labor family and the
     compute-cost family cannot end up quoting against different arms.

Run:  python -m pytest Tests/unit/test_baseline_declaration.py -q
"""
from __future__ import annotations

import logging

from Optimization.Performance_Evaluations.core import baseline


class _Log:
    """Collects what would have been logged, so a test can assert the fallback is loud."""
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


def _arms(*specs):
    return [{'key': k, 'assignment': a} for k, a in specs]


def test_the_declared_arm_is_found_wherever_it_sits():
    arms = _arms(('uni_rank_labor', 'Rank_labor'), ('uni_compact', 'Compact'),
                 ('uni_fifo', 'FIFO'))
    log = _Log()
    assert baseline.resolve(arms, log)['key'] == 'uni_fifo'
    assert not log.warnings, 'finding the declared arm is not an event'


def test_the_match_is_case_insensitive():
    """The strategy dicts carry `FIFO`; `cost/rollup` works in `fifo`."""
    assert baseline.BASELINE.select(_arms(('a', 'FIFO')))['key'] == 'a'
    assert baseline.BASELINE.select(_arms(('a', 'fifo')))['key'] == 'a'
    assert baseline.BASELINE.select(_arms(('a', 'FiFo')))['key'] == 'a'


def test_the_positional_fallback_still_answers_and_says_so():
    """Under focus='opt' the arm list is filtered before the baseline is taken, so a run
    with no FIFO arm is legitimate — and must not be silent."""
    arms = _arms(('opt_compact', 'Compact'), ('opt_rank_labor', 'Rank_labor'))
    log = _Log()
    got = baseline.resolve(arms, log)
    assert got['key'] == 'opt_compact'
    assert len(log.warnings) == 1
    msg = log.warnings[0]
    assert 'opt_compact' in msg and 'fifo' in msg
    assert 'focus' in msg, 'the message must name the situation that causes this'


def test_an_empty_strategy_list_resolves_to_nothing_rather_than_raising():
    assert baseline.resolve([], _Log()) is None


def test_a_strategy_list_with_no_assignment_key_falls_back_loudly():
    """Synthetic lists in tests and notebooks carry no `assignment`."""
    log = _Log()
    assert baseline.resolve([{'key': 'a'}, {'key': 'b'}], log)['key'] == 'a'
    assert log.warnings


def test_the_cost_family_shares_the_one_declaration():
    from Optimization.Performance_Evaluations.cost.rollup import BASE_RULE
    assert BASE_RULE == baseline.BASELINE.value


def test_the_context_takes_its_baseline_from_the_declaration():
    """Source-level, because building a real EvalContext needs a run tree."""
    import inspect
    from Optimization.Performance_Evaluations.core.context import EvalContext
    src = inspect.getsource(EvalContext.__init__)
    assert '_baseline.resolve' in src
    assert 'self.base       = self.strategies[0]' not in src, \
        'the positional answer is back, and it is silent again'


def test_the_chart_label_comes_from_the_same_declaration():
    """`mark_baseline` and `baseline_handle` default to this string on every figure."""
    from Optimization.Performance_Evaluations.common import chartkit
    assert baseline.BASELINE.label == 'FIFO baseline'
    for fn in (chartkit.mark_baseline, chartkit.baseline_handle):
        default = inspect_default(fn, 'label')
        assert default == baseline.BASELINE.label, \
            f'{fn.__name__} defaults to {default!r}, not the declared label'


def inspect_default(fn, name):
    import inspect
    return inspect.signature(fn).parameters[name].default


def test_logging_a_real_logger_does_not_raise():
    """`resolve` is called from inside a worker with a real Logger, not a stub."""
    baseline.resolve(_arms(('a', 'Compact')), logging.getLogger('test-baseline'),
                     where='cfg/all')
