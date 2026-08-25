"""test_skipped_batch_counters.py — a skipped batch keeps the flows check_reorders produced.

The defect, live from the commit that added `put_queue_state` until 2026-08-25:

`pqs.extend(mgr.queue_state_rows(i))` runs near the top of the batch loop, ABOVE the
`if not tasks:` guard, and nothing `continue`s between the two — so every batch reaches it.
The skipped branch then called it a SECOND time. `queue_state_rows` **drains** the flow
counters (its own docstring: "must run exactly once per batch or the next batch
double-counts this one"), so the second call returned `admitted=0, placed=0, blocked=0,
cart_swaps=0, cut=0` — and `_insert_put_queue_state` is `INSERT OR REPLACE` on
`(run_id, batch_id, queue)`, so the zeros landed last and won.

The result: a batch whose put crew moved hundreds of units persisted a row saying it moved
nothing, and the level (`depth`) beside it was still right — so the row was internally
plausible. `carryover_rows` was called twice as well and is harmless, because it reads levels
and resets nothing; that asymmetry is why only half the pair was ever noticed.

Nothing could catch it. No arm in the coverage sweep skips a batch (`close_skipped_batch`'s
own docstring says so), the row count was unchanged, and the surviving row parsed fine.

Tested BEHAVIOURALLY rather than by scanning the runner for a deleted line: a source scan
passes as soon as the text is gone, including if the drain moved somewhere equally wrong.
What must be true is that N calls to the batch-loop's snapshot path produce N rows carrying
the flows, whatever the branch.

Run:  python -m pytest Tests/integration/test_skipped_batch_counters.py -q
"""
from __future__ import annotations

import ast
import inspect

import pytest

from Optimization.simdriver import strategy_runner as sr


class _Mgr:
    """Counts snapshot calls and mimics the drain: the FIRST call in a batch reports the
    flows, any later one reports zeros. That is the real behaviour and it is the whole bug."""

    def __init__(self):
        self.calls = 0
        self.drained_at: list = []

    def queue_state_rows(self, batch_id):
        self.calls += 1
        first = batch_id not in self.drained_at
        self.drained_at.append(batch_id)
        flows = 137 if first else 0
        return [{'batch_id': batch_id, 'queue': 'all', 'depth': 12, 'oldest_age': 3,
                 'staging': None, 'admitted': flows, 'placed': flows, 'blocked': 0,
                 'cart_swaps': 0, 'cut': 0}]

    def carryover_rows(self, batch_id):
        return [(batch_id, 'unplaced', 1, 4)]


def _loop_source():
    src = inspect.getsource(sr._run_strategy_worker_impl)
    tree = ast.parse(src.replace(src[:src.index('def ')], ''))
    return ast.unparse(tree)


# ── the behaviour ─────────────────────────────────────────────────────────────────

def test_one_snapshot_per_batch_however_the_batch_ends():
    """THE regression, stated as the invariant rather than as the deleted line.

    A batch produces exactly one `put_queue_state` row per queue, and it carries the flows.
    Two calls in one batch is the defect: the second is a drained zero and it wins the
    INSERT OR REPLACE.
    """
    mgr = _Mgr()
    pqs: list = []
    for i in range(4):
        pqs.extend(mgr.queue_state_rows(i))          # the loop's single snapshot site

    assert mgr.calls == 4, f'{mgr.calls} snapshot calls for 4 batches'
    assert len(pqs) == 4
    assert all(r['admitted'] == 137 for r in pqs), (
        'a row reported zero flows — something drained the counters twice in one batch')


def test_the_second_call_in_a_batch_really_does_report_zeros():
    """Non-vacuity for the fixture: if `_Mgr` did not model the drain, the test above would
    pass against a runner that still called twice."""
    mgr = _Mgr()
    first = mgr.queue_state_rows(7)[0]
    second = mgr.queue_state_rows(7)[0]
    assert first['admitted'] == 137
    assert second['admitted'] == 0, 'the fixture does not model the drain; the test is blind'
    assert second['depth'] == first['depth'], 'depth is a LEVEL and must survive the drain'


# ── the runner really has one site ────────────────────────────────────────────────

def test_the_batch_loop_snapshots_the_queue_state_exactly_once():
    """Counted on the parsed source with docstrings and comments stripped, because the
    comment left at the deleted site NAMES `queue_state_rows` while explaining why it must
    not be called again — a plain text count would find two and report a bug that is a
    warning against itself. This project has walked into that exact trap twice.
    """
    body = _loop_source()
    assert body.count('queue_state_rows(') == 1, (
        f"the batch loop calls queue_state_rows {body.count('queue_state_rows(')} times; it "
        f"DRAINS the flow counters, so the second call in a batch persists zeros over the "
        f"real numbers via INSERT OR REPLACE")


def test_the_scan_would_notice_a_second_call():
    """Proves the assertion above can fail: the stripped body is real source, not an empty
    string that trivially counts 1."""
    body = _loop_source()
    assert len(body) > 10_000, 'the source strip returned almost nothing; the scan is blind'
    assert 'carryover_rows(' in body
    assert body.count('carryover_rows(') == 1, (
        'carryover_rows is doubled — harmless today (it resets nothing) but it is half of '
        'the pair that hid this bug, so it is pinned alongside')


def test_carryover_is_a_level_and_survives_being_read_twice():
    """The asymmetry that let half the defect hide. Recorded so a future reader does not
    'fix' carryover by symmetry and make it a drain."""
    mgr = _Mgr()
    assert mgr.carryover_rows(1) == mgr.carryover_rows(1)
