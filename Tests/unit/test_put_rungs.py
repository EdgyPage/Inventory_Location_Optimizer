"""test_put_rungs.py — each ADR-0003 rung, answered on its own.

Before ticket 17 the five rungs were written inline in one 180-line `while waiting:` loop,
and reaching rung 3 in a test meant building a warehouse whose medium tier is full, whose
small tier is free, whose SKU has no room in its own bins, and then a queue, a budget and a
deadline to get the loop to walk that far. **The rung was only observable through everything
in front of it.**

Each rung is now `(unit, item, queue) -> RungResult`, so it can be asked directly: given
this unit, what would you do? That is what this file does. `test_empty_first_topup.py` still
owns the ORDER (which rung fires when, and the rework a mis-ordered chain causes); this file
owns each rung's own answer and the driver's bookkeeping.

Its fixtures are imported rather than rebuilt: the two-tier warehouse there is already
exactly the configuration ADR-0003 legislates over — no empty bin fits, but a rescue could
still fire — and a second copy of it would be a second thing to keep true.

Run:  python -m pytest Tests/unit/test_put_rungs.py -q
"""
from __future__ import annotations

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import PutawayItem
from Warehouse.inventory.put_rungs import (
    DECLINED, DEFAULT_PUT_CHAIN, PUT_RUNGS, RungResult, chain_for,
)
from Warehouse.layout.Storage_Primitive import Pallet

from Tests.unit.test_empty_first_topup import _medium_bins, _order, _stocked, _warehouse


def _item(order, qty: int, source: str = 'reorder') -> PutawayItem:
    return PutawayItem(Pallet(order, qty), source)


def _queue(mgr):
    return mgr.put_queues.queues[0]


# ── the result type ───────────────────────────────────────────────────────────────

def test_declined_is_falsy_and_work_is_truthy():
    """The chain's only control-flow question, and it is DERIVED, not a fourth field.

    A rung that reported work and also reported declining is not a state the chain has; a
    flag would let one exist, and the loop would then have two disagreeing answers to walk on.
    """
    assert not DECLINED
    assert not RungResult()
    assert RungResult(bins=1)
    assert RungResult(units=(object(),))
    assert RungResult(bins=2, units=(object(),))


def test_a_result_is_immutable():
    """The chain hands one answer to the driver; a driver that could edit it in place would
    be a second writer of the same fact, which is the shape ticket 02 spent a whole arc on."""
    with pytest.raises(Exception):
        RungResult().bins = 5


# ── the chain resolver ────────────────────────────────────────────────────────────

def test_the_default_chain_is_adr_0003s_order():
    assert DEFAULT_PUT_CHAIN == ('empty_bin', 'own_bins', 'repack', 'singleton')
    assert chain_for(None) == DEFAULT_PUT_CHAIN
    assert set(DEFAULT_PUT_CHAIN) <= set(PUT_RUNGS)


def test_an_unknown_rung_is_refused_not_silently_skipped():
    """`put_policy.key_for`'s rule, one question over: a typo must refuse at build time, or
    it is a rung that never fires and a chain that quietly does something else."""
    with pytest.raises(ValueError) as ei:
        chain_for(('empty_bin', 'home_bin'))
    assert 'home_bin' in str(ei.value)
    assert 'empty_bin' in str(ei.value), 'the refusal should name what it does know'


def test_an_empty_chain_is_refused():
    """"No policy" and "place nothing" are different requests, and an empty tuple reads as
    the first while meaning the second."""
    with pytest.raises(ValueError) as ei:
        chain_for(())
    assert 'pending' in str(ei.value)


def test_every_registered_rung_exists_on_the_manager():
    """A symbol table that resolves NAMES cannot catch a wrong RELATIONSHIP, so this also
    checks each one is CALLABLE with the rung signature rather than merely present."""
    import inspect
    for name, meth in PUT_RUNGS.items():
        fn = getattr(Inventory_Manager, meth, None)
        assert fn is not None, f'rung {name!r} names {meth!r}, which does not exist'
        params = list(inspect.signature(fn).parameters)
        assert params == ['self', 'unit', 'item', 'queue'], (
            f'rung {name!r} has signature {params}; the chain calls (unit, item, queue)')


def test_the_manager_binds_the_default_chain_and_a_custom_one_resolves():
    wh = _warehouse()
    mgr = Inventory_Manager(wh)
    assert mgr.put_chain == DEFAULT_PUT_CHAIN
    assert len(mgr._put_chain) == len(DEFAULT_PUT_CHAIN)

    mgr.put_chain = ('empty_bin', 'own_bins')
    assert mgr.put_chain == ('empty_bin', 'own_bins')
    assert len(mgr._put_chain) == 2
    with pytest.raises(ValueError):
        mgr.put_chain = ('empty_bin', 'nope')
    assert mgr.put_chain == ('empty_bin', 'own_bins'), 'a refused chain replaced the live one'


# ── rung 0: the empty bin ─────────────────────────────────────────────────────────

def test_empty_bin_places_one_and_books_its_own_count():
    """It goes through `_execute_placement`, which drops the queued count itself because it
    is also reached from callers that never had a queue item. The driver must not drop it
    twice, and `counts_booked` is how it knows."""
    wh, mgr, order, _bins = _stocked([])          # four medium bins, all free
    item = _item(order, 8)
    mgr._queued_sku_counts[order.sku] = 1

    res = mgr._rung_empty_bin(item.unit, item, _queue(mgr))
    assert res, 'an empty medium bin was free and the rung declined'
    assert res.bins == 1
    assert res.units == ()
    assert res.counts_booked is True
    assert mgr._queued_sku_counts.get(order.sku) is None, (
        '_execute_placement should have dropped the count, which is what counts_booked says')


def test_empty_bin_declines_when_nothing_fits():
    """Both medium bins full and no small bin: `place_one` returns None and the chain walks."""
    wh, mgr, order, bins = _stocked([12, 12, 12, 12], small_aisle=False)
    item = _item(order, 8)
    assert mgr._rung_empty_bin(item.unit, item, _queue(mgr)) is DECLINED


# ── rung 1: the SKU's own bins ────────────────────────────────────────────────────

def test_own_bins_absorbs_fully_and_charges_every_bin_it_touched():
    """A unit absorbed into two own bins is TWO trips, so it must cost two against the
    budget — read off the `_put_topups` flow, not returned, so `_top_up_own_bins` keeps its
    one-number interface."""
    wh, mgr, order, bins = _stocked([10, 10, 12, 12], small_aisle=False)  # room: 2 + 2
    item = _item(order, 4)
    res = mgr._rung_own_bins(item.unit, item, _queue(mgr))
    assert res, 'the SKU had room in its own bins and the rung declined'
    assert res.bins == 2, f'two shelves were topped up, charged {res.bins}'
    assert res.units == (), 'a full absorb leaves nothing to push back'
    assert res.counts_booked is False, 'the driver owns the count for this rung'


def test_own_bins_partial_sends_the_remainder_back_as_one_unit():
    """The remainder takes the chain FROM THE TOP -- a smaller unit may now find an empty
    bin, which is still empty-first. One unit in, one unit out."""
    wh, mgr, order, bins = _stocked([10, 10, 12, 12], small_aisle=False)  # room 4
    item = _item(order, 9)
    res = mgr._rung_own_bins(item.unit, item, _queue(mgr))
    assert res.bins == 2
    assert len(res.units) == 1, f'the remainder must be ONE unit, got {res.units}'
    assert res.units[0].quantity == 5, 'the remainder is what the own bins could not take'
    assert type(res.units[0]) is type(item.unit), 'the remainder changed storage class'


def test_own_bins_declines_when_the_sku_has_no_room():
    wh, mgr, order, bins = _stocked([12, 12, 12, 12], small_aisle=False)
    item = _item(order, 4)
    assert mgr._rung_own_bins(item.unit, item, _queue(mgr)) is DECLINED


# ── rung 2: the repack rescue ─────────────────────────────────────────────────────

def test_repack_splits_into_the_smaller_tier_and_charges_no_bins():
    """A rescue is REWORK, not a put: it splits and pushes back, and the budget counts
    placements. Charging it would make the cap depend on how badly the warehouse is packed."""
    wh, mgr, order, bins = _stocked([12, 12, 12, 12])   # medium full, small tier free
    item = _item(order, 12)
    before = mgr._recv_repacks

    res = mgr._rung_repack(item.unit, item, _queue(mgr))
    assert res, 'a free smaller tier was standing ready and the rescue declined'
    assert res.bins == 0, 'a repack must not charge the placement budget'
    assert len(res.units) == 2, f'12 items into small (6/bin) is two units, got {res.units}'
    assert sum(u.quantity for u in res.units) == 12, 'the rescue lost or invented items'
    assert mgr._recv_repacks == before + 1, 'the rework went unpriced'


def test_repack_declines_with_no_smaller_tier_available():
    wh, mgr, order, bins = _stocked([12, 12, 12, 12], small_aisle=False)
    item = _item(order, 12)
    assert mgr._rung_repack(item.unit, item, _queue(mgr)) is DECLINED


# ── rung 3: the singleton rescue ──────────────────────────────────────────────────

def test_singleton_declines_for_a_fulfillment_unit():
    """A fulfillment unit has no singleton fallback; it stays ff. Asserted on the rung
    rather than on a loop that needs three earlier rungs to decline first."""
    from Warehouse.kernel.regime import FULFILLMENT

    wh, mgr, order, bins = _stocked([12, 12, 12, 12])
    item = _item(order, 12)

    class _FF:
        unit_category = FULFILLMENT
        storage_size = 'small'
        quantity = 12
        order = item.unit.order

    assert mgr._rung_singleton(_FF(), item, _queue(mgr)) is DECLINED


def test_singleton_declines_when_no_singleton_bin_exists():
    """The fixture warehouse is pallet-only, so the rescue has no landing ground."""
    wh, mgr, order, bins = _stocked([12, 12, 12, 12])
    item = _item(order, 12)
    assert mgr._rung_singleton(item.unit, item, _queue(mgr)) is DECLINED


# ── the driver's bookkeeping ──────────────────────────────────────────────────────

def test_the_queued_count_agrees_with_what_is_actually_queued():
    """The invariant `_queued_sku_counts` exists for, across all three rung outcomes.

    Checking the delta itself would mean catching the count mid-drain; checking the
    INVARIANT it serves is both stronger and observable: after a drain, the recorded count
    for a SKU must equal the units of that SKU still sitting on a put queue. A wrong delta
    in any rung breaks it, and the two forms the driver keeps are exactly what makes it hold
    -- leaving the queue POPS the key at zero, a split ADDS with a default of 1, and those
    are not the same expression.
    """
    def _queued_units(mgr, sku):
        return sum(1 for q in mgr.put_queues for it in q.items
                   if it.unit.order.sku == sku)

    # absorbed into its own bins: one unit in, none back
    wh, mgr, order, bins = _stocked([10, 10, 12, 12], small_aisle=False)
    mgr._queued_qty[order.sku] = 4
    mgr._queued_sku_counts[order.sku] = 1
    _queue(mgr).items.append(_item(order, 4))
    mgr._stock()
    assert _queued_units(mgr, order.sku) == 0, 'the fixture did not absorb the unit'
    assert order.sku not in mgr._queued_sku_counts, (
        f'absorbed unit left {mgr._queued_sku_counts} behind; the key must be POPPED, which '
        f'is the form `_execute_placement` uses and a plain -1 would not reproduce')

    # split by the repack rescue, then placed: one in, two back, both land
    wh, mgr, order, bins = _stocked([12, 12, 12, 12])
    mgr._queued_qty[order.sku] = 12
    mgr._queued_sku_counts[order.sku] = 1
    _queue(mgr).items.append(_item(order, 12))
    mgr._stock()
    assert mgr._recv_repacks == 1, 'the fixture did not exercise the repack rescue'
    assert mgr._queued_sku_counts.get(order.sku, 0) == _queued_units(mgr, order.sku), (
        f'count {mgr._queued_sku_counts.get(order.sku, 0)} against '
        f'{_queued_units(mgr, order.sku)} unit(s) actually queued')

    # held: nothing fits anywhere, so the one unit stays and the count stays at one
    wh, mgr, order, bins = _stocked([12, 12, 12, 12], small_aisle=False)
    mgr._queued_qty[order.sku] = 4
    mgr._queued_sku_counts[order.sku] = 1
    _queue(mgr).items.append(_item(order, 4))
    mgr._stock()
    assert _queued_units(mgr, order.sku) == 1, 'the fixture placed a unit it should not have'
    assert mgr._queued_sku_counts.get(order.sku, 0) == 1, (
        'a held item must not be booked as if it left the queue')


def test_a_chain_that_declines_everywhere_holds_the_item_rather_than_dropping_it():
    """No expiry: a unit no bin can ever hold retries forever, and `queue_depth` is the only
    signal it is happening. Dropping it would be merchandise vanishing with nothing raising.
    """
    wh, mgr, order, bins = _stocked([12, 12, 12, 12], small_aisle=False)
    mgr._queued_qty[order.sku] = 4
    mgr._queued_sku_counts[order.sku] = 1
    _queue(mgr).items.append(_item(order, 4))
    mgr._stock()
    held = [it for q in mgr.put_queues for it in q.items]
    assert len(held) == 1, f'the unit was not held: {held}'
    assert held[0].unit.quantity == 4, 'the held unit changed on the way through'
