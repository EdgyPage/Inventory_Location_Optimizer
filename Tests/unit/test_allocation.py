"""test_allocation.py — splitting work across N workers, with picking taken out of it.

`Pick.assign_tasks` was the one task→picker partition, and it was three things at once: a
round-robin fallback, an LPT balancer, and a pick-specific cost function that reads
`bin_.storage.order`. A second work stream — an inbound crew unloading trailers — needs the
first two and none of the third.

The extraction is only safe if the parts nobody writes down are pinned, and there are two:

  1. **The LPT tie-break is descending.** `sorted(..., key=(cost, order_key), reverse=True)`
     reverses BOTH components, so equal-cost items are visited in DESCENDING key order.
     Nothing said so, and changing it silently repartitions every run in the archive.
  2. **`assign_tasks` coerces, `partition` refuses.** The retired code read
     `!= 'lpt' → round_robin`, which turned a typo in a config into a quietly different
     experiment. The generic splitter now raises; the legacy coercion stays in
     `assign_tasks`, where the legacy contract lives.

Run:  python -m pytest Tests/unit/test_allocation.py -q
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from Warehouse.kernel.allocation import POLICIES, partition


def _items(n):
    """Duck-typed work items — the splitter must not look inside one."""
    return [SimpleNamespace(key=i, cost=float(i)) for i in range(n)]


# ── round robin ──────────────────────────────────────────────────────────────────

def test_round_robin_is_index_modulo_and_preserves_order():
    items = _items(7)
    got = partition(items, 3)
    assert [[i.key for i in b] for b in got] == [[0, 3, 6], [1, 4], [2, 5]]


def test_round_robin_needs_no_cost_function():
    """It is the default and the legacy behaviour; requiring a cost would break it."""
    assert partition(_items(4), 2)


def test_one_worker_gets_everything_whatever_the_policy():
    items = _items(5)
    for policy in POLICIES:
        got = partition(items, 1, policy=policy,
                        cost_of=lambda i: i.cost, order_key=lambda i: i.key)
        assert len(got) == 1 and len(got[0]) == 5


def test_more_workers_than_items_leaves_empty_buckets_not_missing_ones():
    got = partition(_items(2), 5)
    assert len(got) == 5
    assert sum(len(b) for b in got) == 2


def test_nothing_to_split_still_returns_one_bucket_per_worker():
    assert partition([], 3) == [[], [], []]


# ── LPT ──────────────────────────────────────────────────────────────────────────

def test_lpt_sends_the_heaviest_item_to_its_own_worker():
    items = [SimpleNamespace(key=k, cost=c) for k, c in
             ((0, 10.0), (1, 1.0), (2, 1.0), (3, 1.0))]
    got = partition(items, 2, policy='lpt',
                    cost_of=lambda i: i.cost, order_key=lambda i: i.key)
    heavy = [b for b in got if any(i.cost == 10.0 for i in b)][0]
    assert len(heavy) == 1, 'the 10-cost item should not be sharing a worker'


def test_lpt_restores_the_consumers_own_order_within_each_worker():
    """The sim processes each picker's tasks in aisle_id order, so a balanced partition
    that left them cost-sorted would change the route, not just the split."""
    items = [SimpleNamespace(key=k, cost=c) for k, c in
             ((5, 1.0), (1, 9.0), (3, 4.0), (2, 4.0), (4, 1.0))]
    for bucket in partition(items, 2, policy='lpt',
                            cost_of=lambda i: i.cost, order_key=lambda i: i.key):
        assert [i.key for i in bucket] == sorted(i.key for i in bucket)


def test_the_equal_cost_tie_break_is_descending():
    """`reverse=True` reverses BOTH tuple components, so equal costs are visited
    highest-key-first. Nothing stated this, and flipping it repartitions every archived
    run — which is exactly why it is asserted rather than assumed."""
    items = [SimpleNamespace(key=k, cost=1.0) for k in (0, 1, 2, 3)]
    got = partition(items, 2, policy='lpt',
                    cost_of=lambda i: i.cost, order_key=lambda i: i.key)
    # visited 3,2,1,0 into least-loaded (ties → lowest index): w0=[3,1], w1=[2,0]
    assert [sorted(i.key for i in b) for b in got] == [[1, 3], [0, 2]]


def test_lpt_conserves_every_item_exactly_once():
    items = _items(23)
    got = partition(items, 4, policy='lpt',
                    cost_of=lambda i: i.cost, order_key=lambda i: i.key)
    flat = [i.key for b in got for i in b]
    assert sorted(flat) == list(range(23))


def test_lpt_accepts_unhashable_items():
    """Keyed by id precisely so an item need not be hashable — a Task is not."""
    items = [{'k': i} for i in range(4)]
    got = partition(items, 2, policy='lpt',
                    cost_of=lambda d: float(d['k']), order_key=lambda d: d['k'])
    assert sum(len(b) for b in got) == 4


def test_lpt_without_a_cost_function_is_an_error_not_a_silent_round_robin():
    with pytest.raises(ValueError, match='needs both'):
        partition(_items(3), 2, policy='lpt')
    with pytest.raises(ValueError, match='needs both'):
        partition(_items(3), 2, policy='lpt', cost_of=lambda i: i.cost)


# ── an unknown policy is refused, and the legacy coercion is where it belongs ────

def test_an_unknown_policy_raises():
    with pytest.raises(ValueError, match='unknown partition policy'):
        partition(_items(3), 2, policy='shortest_first')


def test_assign_tasks_keeps_the_legacy_coercion_locally():
    """The retired code read `!= 'lpt' → round_robin`, so a typo in a config was a quietly
    different experiment. `partition` refuses; `assign_tasks` still coerces, because that
    is the contract archived runs were produced under and it belongs where it applies."""
    from Warehouse.picking.Pick import assign_tasks
    src = inspect.getsource(assign_tasks)
    assert "policy != 'lpt'" in src
    assert "policy='round_robin'" in src


def _code_of(fn) -> str:
    """A function's source with its docstring removed.

    Scanning prose for code is how the last two of these checks produced false
    positives: `assign_tasks`' own docstring explains the retired `i % num_pickers`
    it no longer contains.
    """
    import ast as _ast
    import textwrap
    tree = _ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if (body and isinstance(body[0], _ast.Expr)
            and isinstance(body[0].value, _ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return chr(10).join(_ast.unparse(n) for n in body)


def test_assign_tasks_delegates_rather_than_reimplementing():
    from Warehouse.picking.Pick import assign_tasks
    code = _code_of(assign_tasks)
    assert code.count('partition(') == 2, 'both branches must go through the splitter'
    assert 'i % n' not in code, 'the round-robin loop is back in the picking layer'
    assert 'min(range(' not in code, 'the least-loaded search is back in the picking layer'
    assert 'load[' not in code, 'the load accumulator is back in the picking layer'


# ── the kernel stays importable by anyone ────────────────────────────────────────

def test_allocation_imports_nothing_but_the_standard_library():
    """`architecture.yml` forbids `wh_kernel -> *`. That is the whole reason this module
    can be the shared home for a pick scheduler and a future inbound one at the same time."""
    import ast
    from Warehouse.kernel import allocation
    tree = ast.parse(inspect.getsource(allocation))
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.Import):
            mod = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
        if mod and mod.split('.')[0] in ('Warehouse', 'Optimization', 'Schema',
                                         'Diagnostics', 'Visualization', 'Inbound'):
            pytest.fail(f'allocation imports {mod}; the kernel may import nothing')
