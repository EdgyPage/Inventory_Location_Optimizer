"""test_putaway_item_consumers.py — a standing put-away queue must not crash its readers.

`_stock_queue` stopped holding bare `StorageUnit`s when `PutawayItem(unit, source)` was
introduced to carry provenance. `PutawayItem` is `__slots__ = ('unit', 'source')` with a
raising `__setattr__` and **no `__getattr__`**, so `item.order` is an `AttributeError` — and
five consumers were still reading the item as though it were the unit.

**This was reachable, not hypothetical.** `_stock_per_unit` ends with
`self._stock_queue = pending` (`Inventory_Management.py:939`): a unit that finds no free bin
stays queued, and the next batch's reorder-queue snapshot in `strategy_runner` walks that
queue. It has been invisible only because the queue is empty in a warehouse with room — and
the staging-limited queues this feature adds make a standing queue the normal case.

Run:  python -m pytest Tests/unit/test_putaway_item_consumers.py -q
"""
from __future__ import annotations

import ast
import types
from collections import deque

import pytest

from Warehouse.inventory.inventory_common import PutawayItem


def _unit(sku=7, qty=3, size='small', cat='pallet',
          handling='conveyable', category='food'):
    return types.SimpleNamespace(
        quantity=qty, storage_size=size, unit_category=cat,
        order=types.SimpleNamespace(
            sku=sku,
            storage_handle_config=types.SimpleNamespace(
                handling=handling, category=category)))


# ── the wrapper really does refuse the unit's attributes ──────────────────────────

@pytest.mark.parametrize('attr', ['order', 'unit_category', 'storage_size', 'quantity'])
def test_the_item_does_not_forward_to_its_unit(attr):
    """If someone adds a `__getattr__` later, these tests stop meaning anything — so pin
    that the wrapper is opaque, which is what makes every consumer's `.unit` mandatory."""
    with pytest.raises(AttributeError):
        getattr(PutawayItem(_unit(), 'reorder'), attr)


# ── the runner's snapshot: the crash, and the fix ─────────────────────────────────

def _rq_block(stock_queue, lead_queue=()):
    """The reorder-queue snapshot from `strategy_runner`, verbatim in shape."""
    rq = {}
    for sku, qty, rem in lead_queue:
        k = ('lead', sku, rem, None, None)
        rq[k] = rq.get(k, 0) + qty
    for it in stock_queue:
        u = it.unit
        k = ('stock', u.order.sku, 0, u.unit_category, u.storage_size)
        rq[k] = rq.get(k, 0) + u.quantity
    return rq


def test_a_standing_queue_snapshots_without_raising():
    q = deque([PutawayItem(_unit(sku=7, qty=3), 'reorder'),
               PutawayItem(_unit(sku=9, qty=2), 'reslot')])
    rq = _rq_block(q)
    assert rq[('stock', 7, 0, 'pallet', 'small')] == 3
    assert rq[('stock', 9, 0, 'pallet', 'small')] == 2


def test_the_old_expression_is_the_one_that_raised():
    """Non-vacuity: the test above passes trivially if the queue were bare units. This
    pins that the OLD code raised on the same input."""
    q = deque([PutawayItem(_unit(), 'reorder')])
    with pytest.raises(AttributeError, match='no attribute .order.'):
        for _u in q:
            _ = ('stock', _u.order.sku, 0, _u.unit_category, _u.storage_size)


def test_two_items_of_one_sku_accumulate():
    q = deque([PutawayItem(_unit(sku=7, qty=3), 'reorder'),
               PutawayItem(_unit(sku=7, qty=4), 'intake')])
    assert _rq_block(q)[('stock', 7, 0, 'pallet', 'small')] == 7


# ── the real consumer, on the real source ─────────────────────────────────────────

def test_the_runner_reads_the_unit_not_the_item():
    import inspect

    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    assert '_u = _it.unit' in src, 'the runner no longer unwraps the PutawayItem'
    assert '_u.order.sku' in src
    assert 'for _u in mgr._stock_queue' not in src, (
        'the runner iterates the queue binding the ITEM to a name it then uses as a unit')


def test_the_diagnostics_bucket_helper_accepts_either():
    """`_unit_bucket` is called with a bin-side unit AND with a queued item; it takes both
    so the three queue-walking call sites read naturally."""
    from Diagnostics.bucket_fill import _unit_bucket
    u = _unit()
    assert _unit_bucket(u) == _unit_bucket(PutawayItem(u, 'reorder'))
    assert _unit_bucket(u) == ('conveyable', 'food', 'small', 'pallet')


def test_every_queue_walker_in_the_repo_unwraps():
    """A ratchet. Any new `for x in ..._stock_queue` that then reads `x.order` is the same
    bug again, and it will not surface until a warehouse is full."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for sub in ('Optimization', 'Warehouse', 'Diagnostics', 'Tests'):
        for path in sorted((root / sub).rglob('*.py')):
            raw = path.read_text(encoding='utf-8')
            if '_stock_queue' not in raw:
                continue
            # Scan CODE, not prose.  The paragraph above describes the bug it forbids, and
            # a raw-text scan flags its own docstring -- which pushes people to delete the
            # explanation rather than fix the code.
            try:
                tree = ast.parse(raw)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = node.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        node.body = body[1:] or [ast.Pass()]
            src = ast.unparse(ast.fix_missing_locations(tree))
            for m in re.finditer(r'for\s+(\w+)\s+in\s+[^\n]*_stock_queue', src):
                var = m.group(1)
                tail = src[m.end():m.end() + 400]
                if re.search(rf'\b{var}\.(order|quantity|unit_category|storage_size)\b', tail):
                    offenders.append(f'{path.relative_to(root).as_posix()}: `{var}`')
    assert not offenders, (
        f'{offenders} read a queued PutawayItem as if it were a StorageUnit; use `.unit`')
