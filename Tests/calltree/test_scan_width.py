"""test_scan_width.py — the width instrument, under test so it does not rot.

`scan_width` exists because the calltree records ONE frame entry however wide a scan is, and
drops C leaves entirely — so an O(A) scan written as a comprehension is invisible to the offender
table. The `_closest_abs` measurement that convicted the cluster-map cold start was written by
hand, used once, and **not kept**; only its result survived, so the next question had to rebuild
it. This file is what stops that happening to the kept version.

The two things it asserts hardest are the two traps the module is shaped around:

  * **an instance must be REFUSED.** Every pool in this codebase defines `__slots__`, so
    assigning a wrapper onto an instance raises — and a swallowed failure gives a counter reading
    0.00, which reads as "the scan never happened" rather than "not measured". That is the most
    flattering possible wrong answer, so the refusal is a test and not a docstring.
  * **`check_calls` must raise.** A width measured against the wrong entry point is worse than no
    width: it is a confident number about a different workload. A previous attempt reported 676
    calls where the ladder recorded 37,911.

Run:  python -m pytest Tests/calltree/test_scan_width.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_width import ScanWidth  # noqa: E402


class _Pool:
    """Stands in for a real pool: it defines `__slots__`, like every pool here."""
    __slots__ = ('seen',)

    def __init__(self):
        self.seen = 0

    def take(self, live):
        self.seen += len(live)
        return len(live)


def _width(args, kwargs):
    return len(args[-1])


# ── measuring ─────────────────────────────────────────────────────────────────────

def test_it_counts_calls_and_sums_input_width():
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', _width, label='take'):
        p.take([1, 2, 3])
        p.take([1, 2])
    assert sw.calls['take'] == 2
    assert sw.width['take'] == 5
    assert sw.mean('take') == 2.5


def test_the_wrapped_function_still_does_its_job():
    """A measurement that changes the answer is not a measurement."""
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', _width, label='take'):
        got = p.take([1, 2, 3, 4])
    assert got == 4, 'the return value must pass through'
    assert p.seen == 4, 'the side effect must still happen'


def test_the_original_is_restored_even_when_the_body_raises():
    sw = ScanWidth()
    original = _Pool.take
    with pytest.raises(ValueError):
        with sw.wrap(_Pool, 'take', _width, label='take'):
            raise ValueError('boom')
    assert _Pool.take is original, 'a raising block must not leave the class patched'


def test_a_width_of_None_counts_the_call_without_a_width():
    """The honest answer when the width is not recoverable from the arguments."""
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', lambda a, k: None, label='take'):
        p.take([1, 2, 3])
    assert (sw.calls['take'], sw.width['take']) == (1, 0)


def test_mean_on_a_key_with_no_calls_is_zero_not_a_crash():
    assert ScanWidth().mean('never-wrapped') == 0.0


# ── the two traps ─────────────────────────────────────────────────────────────────

def test_wrapping_an_INSTANCE_is_refused():
    """THE TRAP. `__slots__` makes the assignment raise; swallowing it reads as a zero scan."""
    sw = ScanWidth()
    p = _Pool()
    with pytest.raises(TypeError, match='module or a class'):
        with sw.wrap(p, 'take', _width):
            pass


def test_wrapping_a_module_is_allowed():
    """The other legal owner: a module-level function, which is how placement is measured."""
    import math
    sw = ScanWidth()
    with sw.wrap(math, 'hypot', lambda a, k: len(a), label='hypot'):
        math.hypot(3, 4)
    assert sw.calls['hypot'] == 1 and sw.width['hypot'] == 2
    assert math.hypot(3, 4) == 5.0, 'restored'


def test_check_calls_raises_when_the_workload_is_wrong():
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', _width, label='take'):
        for _ in range(676):
            p.take([1])
    with pytest.raises(AssertionError, match='DIFFERENT workload'):
        sw.check_calls('take', 37911)


def test_check_calls_passes_inside_tolerance():
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', _width, label='take'):
        for _ in range(98):
            p.take([1])
    sw.check_calls('take', 100)                      # 2% out, inside the 5% default
    with pytest.raises(AssertionError):
        sw.check_calls('take', 100, tol=0.01)        # 2% out, outside a 1% tolerance


def test_report_names_every_wrapped_key():
    sw = ScanWidth()
    p = _Pool()
    with sw.wrap(_Pool, 'take', _width, label='take'):
        p.take([1, 2])
    out = sw.report()
    assert 'take' in out and 'calls=' in out and 'mean=' in out
    assert ScanWidth().report().strip() == '(nothing wrapped)'
