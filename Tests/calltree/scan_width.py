"""scan_width.py -- measure how WIDE a function's input is, not just how often it is called.

DRAFT for Tests/calltree/scan_width.py.

The calltree tracer cannot see this. It records one frame entry however wide a scan is, and it
drops C leaves entirely -- so an O(A) scan written as a comprehension or keyed on a C method is
invisible, while an identical lambda-keyed one is convicted. `_closest_abs` was measured at
k=1.99 by hand for exactly this reason, and the instrument that did it was NOT KEPT: only the
result survived, so the next question had to rebuild it. This is that instrument, kept.

Two traps are designed into the API rather than left to the caller:

  * PATCH THE CLASS, NEVER THE INSTANCE. Every pool in this codebase defines `__slots__`, so
    assigning a wrapper onto an instance raises AttributeError -- and swallowing that gives a
    counter reading 0.00, which looks like "the scan never happened" rather than "not measured".
    `wrap` refuses an instance.
  * A WIDTH INSTRUMENT MUST RUN THE LADDER'S OWN WORKLOAD. The first attempt at the
    `_closest_abs` measurement drove a plausible-looking neighbour entry point and reported 676
    calls where the ladder recorded 37,911, with a mean width that would have ACQUITTED the
    candidate. `check_calls` exists so a disagreement with a known call count is loud.
"""
from __future__ import annotations

import contextlib
import functools
import inspect


class ScanWidth:
    """Call counts and summed input widths for one or more wrapped functions."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.width: dict[str, int] = {}

    # ── measurement ──────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def wrap(self, owner, name: str, width_of, *, label: str | None = None):
        """Wrap `owner.name` for the duration of the block, summing `width_of(args, kwargs)`.

        `owner` is a MODULE or a CLASS. An instance is refused -- see the module docstring.
        `width_of` returns the size of the thing the call is about to scan; returning None
        counts the call without a width, which is the honest answer when the width is not
        recoverable from the arguments.
        """
        if not (inspect.ismodule(owner) or inspect.isclass(owner)):
            raise TypeError(
                f'wrap() takes a module or a class, not {type(owner).__name__}. Patching an '
                f'INSTANCE is the trap this refuses: every pool here defines __slots__, so the '
                f'assignment raises -- and a swallowed failure reads as a zero-width scan, '
                f'which looks like proof of innocence.')
        key = label or f'{getattr(owner, "__name__", owner)}.{name}'
        original = getattr(owner, name)
        self.calls.setdefault(key, 0)
        self.width.setdefault(key, 0)

        @functools.wraps(original)
        def counted(*args, **kwargs):
            self.calls[key] += 1
            w = width_of(args, kwargs)
            if w is not None:
                self.width[key] += int(w)
            return original(*args, **kwargs)

        setattr(owner, name, counted)
        try:
            yield self
        finally:
            setattr(owner, name, original)

    # ── reading ──────────────────────────────────────────────────────────────────────

    def mean(self, key: str) -> float:
        """Mean width per call -- the number that separates 'more calls' from 'wider scans'."""
        n = self.calls.get(key, 0)
        return self.width.get(key, 0) / n if n else 0.0

    def check_calls(self, key: str, expected: int, *, tol: float = 0.05) -> None:
        """Raise unless the call count matches a figure from another instrument.

        A width measured on the wrong workload is worse than none: it produces a confident
        number about a different run. Cross-check against the ladder's own count.
        """
        got = self.calls.get(key, 0)
        if expected and abs(got - expected) > tol * expected:
            raise AssertionError(
                f'{key}: {got:,} calls against an expected {expected:,}. This is measuring a '
                f'DIFFERENT workload -- fix the entry point before believing any width from it.')

    def report(self) -> str:
        out = []
        for key in sorted(self.calls):
            out.append(f'  {key:44s} calls={self.calls[key]:>10,}  '
                       f'width={self.width[key]:>13,}  mean={self.mean(key):8.2f}')
        return '\n'.join(out) or '  (nothing wrapped)'
