"""test_actor_uid_blocks.py — two crews are never the same people.

`actor_uid` is the only thing in `work_events` that says WHO did a unit of work. Nothing
enforces it: `Worker.__post_init__` checks that uid and local_id are non-negative and no
more; the DDL has no uniqueness constraint; `put_rows` bounds-checks `0 <= widx <
len(workers)`, which a collision passes trivially; and `work_events_merged` still sorts. So a
duplicated uid produces a run that looks completely healthy while any per-actor rollup
quietly merges two people.

The defect this file exists for was live and unreachable at the same time. The runner built

    _put_crews = {q.name: _put_workers for q in mgr.put_queues}

which hands every stream the SAME worker tuple, three lines under a comment saying "each
stream needs its own uid block -- which is why the roster is a dict rather than a second bare
list". Production installs `single_queue()` (one queue, named `'all'`), so the comprehension
had exactly one iteration and the bug could not fire. It would have fired on the first run
with a second stream.

Two lookups had the same shape: `_put_crews.get(_qname, _put_workers)` in the main flush and
`put_crews.get(qname, put_workers)` in the skipped-batch path. A record whose queue has no
roster is a wiring bug; falling back attributes its work to put-away actors and says nothing.

What is asserted here is the ALLOCATOR CONTRACT — that N crews chained through `next_uid`
produce disjoint, contiguous blocks — plus that the runner's builder implements it. The first
is what makes a third crew safe to add; the second is what makes the first one true of this
codebase rather than of a dataclass.

Run:  python -m pytest Tests/unit/test_actor_uid_blocks.py -q
"""
from __future__ import annotations

import ast
import inspect

import pytest

from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.operations import Crew, Mode, Role


def _crew(role, size):
    return Crew(role=role, mode=Mode.FOOT, speed=SpeedProfile(2.0, 4.0), size=size)


# ── the allocator contract ────────────────────────────────────────────────────────

@pytest.mark.parametrize('sizes', [(1, 1), (10, 1), (1, 10), (3, 4, 2), (1, 1, 1), (7, 2, 5)])
def test_chained_crews_get_disjoint_contiguous_uid_blocks(sizes):
    """The property a third crew depends on. Chained through `next_uid`, every crew's uids
    are unique across crews AND leave no gap — so `max(uid) + 1 == total headcount`, which is
    the cheap invariant a reconciliation can check on a real DB."""
    # Role is irrelevant to allocation -- it is the CHAINING that separates the blocks --
    # so the third crew here is another PUT rather than the RECEIVE that does not exist
    # yet. That is the point: the contract has to hold before the crew that needs it.
    uid, blocks = 0, []
    for size in sizes:
        c = _crew(Role.PUT, size)
        blocks.append([w.uid for w in c.workers(uid)])
        uid = c.next_uid(uid)

    flat = [u for b in blocks for u in b]
    assert len(flat) == len(set(flat)), f'uid collision across crews: {blocks}'
    assert sorted(flat) == list(range(sum(sizes))), f'blocks are not contiguous: {blocks}'
    assert uid == sum(sizes), 'the cursor did not end past the last uid'


def test_local_ids_stay_dense_within_each_crew():
    """`local_id` and `uid` are two id spaces and confusing them is the documented failure:
    `_group_events_by_picker` allocates exactly k lists and RAISES outside `[0, k)`, so a uid
    that leaked into a local slot would land inside the range and be silently accepted."""
    uid = 0
    for size in (3, 4):
        c = _crew(Role.PUT, size)
        ws = c.workers(uid)
        assert [w.local_id for w in ws] == list(range(size))
        uid = c.next_uid(uid)
    assert uid == 7


def test_a_shared_first_uid_really_does_collide():
    """Non-vacuity: proves the assertions above are testing something. This is the exact
    shape the runner had — two crews built from the same starting uid."""
    a = _crew(Role.PICK, 3).workers(0)
    b = _crew(Role.PUT, 3).workers(0)              # the bug: not chained
    assert {w.uid for w in a} == {w.uid for w in b}, (
        'two crews from the same first_uid no longer collide — the allocator changed and '
        'this file is no longer testing the thing it was written for')


# ── the runner implements it ──────────────────────────────────────────────────────

def _worker_body() -> str:
    """The batch-loop worker with docstrings stripped, so a comment that NAMES the old
    broken form (there is one, explaining why it is gone) cannot satisfy a text search."""
    from Optimization.simdriver import strategy_runner as sr
    tree = ast.parse(inspect.getsource(sr._run_strategy_worker_impl))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


def test_the_runner_advances_a_cursor_instead_of_reusing_one_tuple():
    body = _worker_body()
    assert '{q.name: _put_workers for q in' not in body, (
        'the comprehension is back: every put stream would share one uid block')
    assert '_put_crew.next_uid(_uid)' in body, 'the uid cursor is not advanced per queue'


def test_no_roster_lookup_falls_back_to_another_crew():
    """A `.get(name, default)` on the roster is how one stream's work gets stamped with
    another crew's actors. Both sites are checked — the main flush and the skipped-batch
    path, which is a second implementation of the same block."""
    from Optimization.simdriver import strategy_runner as sr
    body = _worker_body()
    assert '_put_crews.get(' not in body, 'the main flush still falls back to the put roster'
    skip = ast.unparse(ast.parse(inspect.getsource(sr.close_skipped_batch)))
    assert 'put_crews.get(' not in skip, (
        'close_skipped_batch still falls back — it is a separate implementation of the '
        'flush and has its own copy of every defect the main one has')
