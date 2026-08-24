"""test_positive_int_flags.py — a count that is meaningless at zero fails at the parser.

`--n-batches 0` was accepted by argparse, then discarded by `if args.n_batches:` — a
truthiness guard where every neighbouring override uses `is not None` — and the run went
ahead on CONFIG's value.  So the one flag people reach for to make a quick smoke run could
silently produce a full one.

Both halves are fixed and both are pinned here: the parser rejects it, and the apply-block
guard no longer treats a legitimate value as absent.

Run:  python -m pytest Tests/unit/test_positive_int_flags.py -q
"""
from __future__ import annotations

import argparse
import inspect

import pytest

from Optimization.run_simulation import _positive_int


# ── the validator ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('text', ['1', '4', '272', '10000'])
def test_a_real_count_passes_through(text):
    assert _positive_int(text) == int(text)


@pytest.mark.parametrize('text', ['0', '-1', '-999'])
def test_zero_and_negatives_are_rejected(text):
    with pytest.raises(argparse.ArgumentTypeError, match='must be 1 or more'):
        _positive_int(text)


@pytest.mark.parametrize('text', ['abc', '', '1.5', 'None'])
def test_a_non_integer_is_rejected_with_its_own_message(text):
    """Distinct from the range message, so the error says which mistake was made."""
    with pytest.raises(argparse.ArgumentTypeError, match='not an integer'):
        _positive_int(text)


# ── wired to the flag, and the guard that swallowed it ────────────────────────────

def test_n_batches_uses_the_validator():
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    i = src.index("'--n-batches'")
    assert 'type=_positive_int' in src[i:i + 200], (
        '--n-batches accepts a plain int again; 0 would be swallowed downstream')


def test_the_apply_guard_is_not_truthiness():
    """The other half.  `if args.n_batches:` treats 0 as "not supplied" — and every other
    override in the same block already uses `is not None`."""
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert 'if args.n_batches is not None:' in src
    assert 'if args.n_batches:' not in src


def test_the_parser_rejects_it_end_to_end():
    import pathlib
    import subprocess
    import sys
    root = pathlib.Path(inspect.getfile(_positive_int)).resolve().parents[1]
    r = subprocess.run([sys.executable, '-m', 'Optimization.run_simulation',
                        '--n-batches', '0', '--help'],
                       capture_output=True, text=True, timeout=180, cwd=str(root))
    assert r.returncode != 0, '--n-batches 0 was accepted'
    assert 'must be 1 or more' in (r.stderr + r.stdout)
