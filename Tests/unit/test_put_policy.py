"""test_put_policy.py — the queue's own precedence, and how it composes with the window.

`k_cap` narrows how far the placement pool may reach past the head. A put POLICY is the
other half: it replaces the pool's opinion with the floor's own. "Finish one SKU before
starting the next" and "clear the bulk first" are real rules that no placement objective
expresses, and they belong to the queue.

The two compose rather than override: a policy proposes an order, `k_cap` then bounds how
far that order may depart from arrival order. That composition is the thing worth testing —
each half alone is nearly trivial.

Run:  python -m pytest Tests/unit/test_put_policy.py -q
"""
from __future__ import annotations

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import PutawayItem
from Warehouse.inventory.put_policy import INHERIT, PUT_POLICIES, key_for
from Warehouse.inventory.put_queue import ANY, PutQueueSpec
from Warehouse.placement.Assignment_Functions import _Pool


class _Unit:
    __slots__ = ('order', 'quantity', 'unit_category')

    def __init__(self, sku, qty):
        self.order = type('O', (), {'sku': sku})()
        self.quantity, self.unit_category = qty, 'pallet'


def _items(spec):
    """spec: list of (age, sku, qty)."""
    return [PutawayItem(_Unit(sku, qty), 'reorder', age) for age, sku, qty in spec]


def _order(items, policy, k=None, pool=None):
    """Drive `_serve_order` the way the drain does: resolve the key over the items, bridge
    it back to bare units, and hand both to the manager."""
    units = [it.unit for it in items]
    by_unit = {id(it.unit): it for it in items}
    pk = key_for(policy, items)
    put_key = None if pk is None else (lambda u: pk(by_unit[id(u)]))
    drain = type('D', (), {'putaway_window': k})()
    return Inventory_Manager._serve_order(drain, pool or _MutePool(), units, k, put_key)


class _MutePool(_Pool):
    __slots__ = ()

    def take(self, unit):
        return None, None


class _ReversePool(_Pool):
    """A pool that wants the exact reverse of arrival order, so any test where the pool's
    opinion survives is immediately visible."""
    __slots__ = ('_ages',)

    def __init__(self, items):
        self._ages = {id(it.unit): it.age for it in items}

    def sort_key(self, unit):
        return float(self._ages[id(unit)])

    def take(self, unit):
        return None, None


def _skus(units):
    return [u.order.sku for u in units]


def _qtys(units):
    return [u.quantity for u in units]


# ── the registry ──────────────────────────────────────────────────────────────────

def test_inherit_means_no_opinion():
    assert key_for(INHERIT, []) is None and key_for(None, []) is None
    assert PUT_POLICIES[INHERIT] is None


def test_an_unknown_policy_raises_rather_than_inheriting():
    """A typo in a swept configuration must not produce a run that looks like a legitimate
    arm and is silently the default."""
    with pytest.raises(KeyError, match='unknown put policy'):
        key_for('fifoo', [])


def test_a_spec_validates_its_policy_at_construction():
    """Fail when the sweep is DEFINED, not several hours into it."""
    with pytest.raises(ValueError, match='unknown put policy'):
        PutQueueSpec('q', accepts=ANY, policy='nope')
    PutQueueSpec('q', accepts=ANY, policy='fifo')       # a real one is fine


def test_every_registered_policy_is_usable():
    """A registry entry nothing can resolve is an entry that will rot."""
    items = _items([(3, 1, 5), (1, 2, 9), (2, 1, 2)])
    for name in PUT_POLICIES:
        got = _order(items, name)
        assert sorted(id(u) for u in got) == sorted(id(it.unit) for it in items), name


# ── each policy does what it says ─────────────────────────────────────────────────

def test_fifo_ignores_the_pool_entirely():
    """Distinct from `k_cap=1`, which also produces arrival order: that constrains the pool,
    this replaces it. The pool below wants the exact reverse and gets no say."""
    items = _items([(10, 1, 1), (11, 2, 1), (12, 3, 1)])
    got = _order(items, 'fifo', k=None, pool=_ReversePool(items))
    assert _skus(got) == [1, 2, 3]


def test_lifo_is_newest_first():
    items = _items([(10, 1, 1), (11, 2, 1), (12, 3, 1)])
    assert _skus(_order(items, 'lifo')) == [3, 2, 1]


def test_largest_first_orders_by_quantity():
    items = _items([(10, 1, 2), (11, 2, 9), (12, 3, 5)])
    assert _qtys(_order(items, 'largest_first')) == [9, 5, 2]


def test_largest_first_breaks_ties_by_arrival():
    """Equal quantities were not ordered by the policy, so the older unit should not lose."""
    items = _items([(10, 1, 4), (11, 2, 4), (12, 3, 4)])
    assert _skus(_order(items, 'largest_first')) == [1, 2, 3]


def test_sku_batched_groups_a_sku_at_its_oldest_members_position():
    """One trip, one item, many bins. SKU 2's youngest unit still travels with its oldest,
    rather than being stranded behind a SKU that arrived later."""
    items = _items([(10, 1, 1), (11, 2, 1), (12, 1, 1), (13, 3, 1), (14, 2, 1)])
    assert _skus(_order(items, 'sku_batched')) == [1, 1, 2, 2, 3]


def test_sku_batched_is_arrival_ordered_within_a_sku():
    items = _items([(30, 7, 1), (10, 7, 1), (20, 7, 1)])
    got = _order(items, 'sku_batched')
    ages = [next(it.age for it in items if it.unit is u) for u in got]
    assert ages == [10, 20, 30]


def test_the_set_scoped_policy_cannot_be_called_per_item():
    """`sku_batched` needs the whole waiting set. Calling the raw entry must fail loudly
    rather than returning a plausible-looking constant."""
    with pytest.raises(NotImplementedError, match='key_for'):
        PUT_POLICIES['sku_batched'](_items([(1, 1, 1)])[0])


# ── the two knobs compose ─────────────────────────────────────────────────────────

def test_a_policy_is_still_bounded_by_the_window():
    """The composition claim. `largest_first` wants the biggest unit wherever it sits;
    `k_cap=2` says it may only reach one unit past the head.

    ages 10..14, quantities 1 2 3 4 9. Unbounded, the 9 goes first. With K=2 the window is
    the two oldest, so the first pick is the larger of quantities 1 and 2.
    """
    items = _items([(10, 1, 1), (11, 2, 2), (12, 3, 3), (13, 4, 4), (14, 5, 9)])
    assert _qtys(_order(items, 'largest_first', k=None)) == [9, 4, 3, 2, 1]
    got = _qtys(_order(items, 'largest_first', k=2))
    assert got[0] == 2, f'the window let the policy reach past the two oldest: {got}'
    assert got == [2, 3, 4, 9, 1]


def test_a_window_of_one_defeats_any_policy():
    items = _items([(10, 1, 1), (11, 2, 9), (12, 3, 5)])
    for name in ('fifo', 'lifo', 'largest_first', 'sku_batched'):
        assert _skus(_order(items, name, k=1)) == [1, 2, 3], name


def test_a_window_wider_than_the_queue_is_the_plain_policy_order():
    items = _items([(10, 1, 1), (11, 2, 9), (12, 3, 5)])
    for name in ('fifo', 'lifo', 'largest_first', 'sku_batched'):
        assert _order(items, name, k=99) == _order(items, name, k=None), name


def test_inherit_leaves_the_pool_in_charge():
    """The default path must be untouched: with no policy the pool's precedence stands."""
    items = _items([(10, 1, 1), (11, 2, 1), (12, 3, 1)])
    pool = _ReversePool(items)
    assert _skus(_order(items, INHERIT, k=None, pool=pool)) == [3, 2, 1]
    assert _skus(_order(items, INHERIT, k=1, pool=pool)) == [1, 2, 3]


# ── the reason sku_batched exists is not the reason it is useful ──────────────────

def test_sku_batched_restores_same_sku_adjacency():
    """The SKU-run caches in the placement pools key on `sku != last_sku`, so they only ever
    hit on adjacent same-SKU units and a FIFO window destroys that. `sku_batched` restores
    it — a side effect worth knowing about, and NOT the reason to choose the policy: the
    caches stay correct either way, they just stop paying."""
    items = _items([(i, i % 3, 1) for i in range(12)])
    got = _skus(_order(items, 'sku_batched'))
    runs = sum(1 for a, b in zip(got, got[1:]) if a != b)
    assert runs == 2, f'expected 3 contiguous SKU runs, got boundaries at {runs}: {got}'
    fifo = _skus(_order(items, 'fifo'))
    assert sum(1 for a, b in zip(fifo, fifo[1:]) if a != b) == 11, 'fifo should interleave'
