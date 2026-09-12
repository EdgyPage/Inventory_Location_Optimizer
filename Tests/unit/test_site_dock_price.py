"""test_site_dock_price.py — the site dock's per-regime unload PRICE LIST (site-dock 27).

"Decide the site dock's unload price" settled that **the unload price is a statement about
the MERCHANDISE, not about the crew**: a fulfillment tote genuinely is quicker to move than
a store pallet, so one site dock serving two channels holds a price LIST keyed by the
unloaded unit's own regime rather than one blended constant.  That decision buys its keep
only if two structural claims hold, and this file is the place they are pinned:

  * **The list is not a mode the single-channel path runs with a one-entry map.**  An
    uncoupled dock carries `cost` and `costs is None`; a site dock carries `costs` and
    `cost is None`.  A dock holds exactly one of the two, never both and never neither, and
    `unload_seconds` on an uncoupled dock returns before it so much as looks at the `unit=`
    it was handed.  That is what keeps every archived run BYTE-IDENTICAL rather than merely
    equivalent — the tests below assert the ignoring directly, by handing a single-price
    dock a FULFILLMENT unit and requiring the store answer back.
  * **Each unit is priced at ITS OWN regime's constant.**  The two durations are computed
    here a second time, independently, straight out of `Inbound.unload.unload_cost` with
    the two `UnloadCost`s — so the assertion is against the cost model rather than against
    the dock repeating itself.  The two units are built with IDENTICAL geometry and
    quantity, so the only thing that can move the answer is the price; a shape difference
    would make the direction assertion pass for the wrong reason.

The refusals are the other half.  A regime with no entry, a price list with no entries, a
dock handed both shapes, a site dock asked to price nothing in particular, and an uncoupled
dock asked "which regime's price" are all questions with no correct answer, and each of
them would otherwise be answered plausibly — at another channel's rate, at the kernel's
0.5 s default, or at whichever of the two prices happened to win.  Every one of them raises,
and the message is matched, because a refusal nothing tests is a refusal an upstream
`except Exception` quietly eats.

Last, the records seam site-dock 21 needed: `take_records()` hands the batch's rows over
WITHOUT restarting the crew's clock, and `drain_records()` is exactly `take_records()` plus
the reset.  The reset has one owner — on a coupled run it belongs to the coordinator's
`note_records`, which fires only once BOTH leaves have stamped — so a partition that reset
early would rebase the other leaf's `t0` against a clock already back at zero.  Both halves
are asserted by watching the clock, not by reading the code.

Nothing here is random: every Order is hand-built with `object.__new__` (the idiom
`test_empty_first_topup` uses) precisely because `Order.__init__` samples its dimensions
from the global RNG, and every duration below is derived from those dimensions.

Run:  python -m pytest Tests/unit/test_site_dock_price.py -q
"""
from __future__ import annotations

import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.unload import UnloadCost, unload_cost
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.kernel.regime import FULFILLMENT, STORE, regime_of
from Warehouse.layout.Storage_Primitive import FulfillmentBin, Pallet

#: Durations are floats; nothing in this file compares one with `==`.
_SECONDS_TOL = 1e-9

#: The two prices, in the DIRECTION the measured pair sits in (site-dock 17 derived
#: C = 7.6 s for store and C = 5.1 s for fulfillment from each run's own pick config).
#: Round numbers rather than those two: the decision is about which price a unit is charged
#: at, and a literal that looked like a measurement would invite someone to trust it as one.
_STORE_COST = UnloadCost(intercept=9.0, per_item=0.75)
_FF_COST = UnloadCost(intercept=3.0, per_item=0.25)

#: The shape BOTH fixture units take.  Small enough to fit a `FulfillmentBin`'s 16x16
#: footprint and its tallest 18-unit tier, so the store pallet and the fulfillment bin can
#: carry the SAME geometry and the same quantity — which is what makes "the duration differs"
#: a statement about the price rather than about the merchandise.
_DIMS = (4, 4, 4)
_WEIGHT = 5.0
_QTY = 3


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

def _order(sku: int, *, regime: str) -> Order:
    """A minimal Order of `regime`, with FIXED physical dimensions.

    `object.__new__` rather than the constructor: `Order.__init__` draws its dimensions
    from the global RNG, and the whole point of the pair below is that the two orders are
    dimensionally identical.  The regime is carried by `storage_handle_config`, which is
    the field `regime_of` reads for an Order (and for a unit through `unit.order`).
    """
    o = object.__new__(Order)
    o._sku = sku
    handle = (FULFILLMENT, FULFILLMENT) if regime == FULFILLMENT else ('conveyable', 'food')
    o.storage_type = handle
    o.storage_handle_config = StorageHandleConfig(*handle)
    o.lift_group = handle
    o.length, o.width, o.height = _DIMS
    o.weight = _WEIGHT
    return o


def _units() -> tuple:
    """One real store `Pallet` and one real fulfillment `FulfillmentBin`, same shape.

    Returned together because every test below that uses one uses the other: the pair IS
    the fixture, and building them apart would let their geometry drift.
    """
    store_unit = Pallet(_order(1, regime=STORE), _QTY)
    ff_unit = FulfillmentBin(_order(2, regime=FULFILLMENT), _QTY)
    return store_unit, ff_unit


def _site_dock(size: int = 1) -> Dock:
    """A SITE dock: a price LIST, no single price."""
    return Dock(DockSpec(name='site_dock', size=size),
                costs={STORE: _STORE_COST, FULFILLMENT: _FF_COST})


def _leaf_dock(size: int = 1) -> Dock:
    """An UNCOUPLED leaf dock: one price, exactly as every archived run built it."""
    return Dock(DockSpec(name='dock', size=size), cost=_STORE_COST)


def _triple(unit) -> tuple:
    """The `(weight, volume, quantity)` every charge site passes — per ITEM, then the pack's
    own quantity, which is the granularity `unload_cost`'s docstring legislates."""
    return unit.order.weight, unit.order.volume(), unit.quantity


# ══════════════════════════════════════════════════════════════════════════════
# 0. Non-vacuity: the fixture can tell the two regimes and the two prices apart
# ══════════════════════════════════════════════════════════════════════════════

def test_the_fixture_pair_differs_only_in_regime():
    """THE GUARD EVERY DIRECTION ASSERTION BELOW RESTS ON.

    Same weight, same volume, same quantity, different regime.  Without this, "the
    fulfillment unload is quicker" could be a fact about a smaller box, and the price list
    could be wired to nothing at all while every test still passed.
    """
    store_unit, ff_unit = _units()
    assert regime_of(store_unit) == STORE, f'store fixture read as {regime_of(store_unit)!r}'
    assert regime_of(ff_unit) == FULFILLMENT, (
        f'fulfillment fixture read as {regime_of(ff_unit)!r}; a FulfillmentBin is the one '
        f'unit_category regime_of answers on directly')
    assert _triple(store_unit) == _triple(ff_unit), (
        f'the two fixture units carry different merchandise '
        f'({_triple(store_unit)} vs {_triple(ff_unit)}), so a duration difference below '
        f'would not be evidence about the price')
    assert _STORE_COST != _FF_COST, 'the two declared prices are the same object of value'


# ══════════════════════════════════════════════════════════════════════════════
# 1. The uncoupled dock is unchanged, structurally
# ══════════════════════════════════════════════════════════════════════════════

def test_a_single_price_dock_holds_no_list_and_prices_as_it_always_did():
    """`cost` set, `costs` None — the archived shape, and the number is the cost model's."""
    dock = _leaf_dock()
    store_unit, _ = _units()
    assert dock.costs is None, 'an uncoupled dock constructed a price list'
    assert dock.cost is _STORE_COST
    want = unload_cost(*_triple(store_unit), _STORE_COST)
    got = dock.unload_seconds(*_triple(store_unit))
    assert abs(got - want) < _SECONDS_TOL, (
        f'the leaf dock charged {got!r} where `unload_cost` says {want!r}')


def test_a_single_price_dock_ignores_the_unit_it_is_handed():
    """THE BYTE-IDENTICAL CLAIM, asserted rather than argued.

    `receiving.py` passes `unit=` at every charge site whether the dock is a site dock or
    not.  An uncoupled dock must return before it resolves a regime — so handing it a
    FULFILLMENT unit has to produce the STORE answer, unchanged to the last bit, or the
    archive's receiving seconds moved the day the keyword arrived.
    """
    dock = _leaf_dock()
    store_unit, ff_unit = _units()
    plain = dock.unload_seconds(*_triple(store_unit))
    with_store = dock.unload_seconds(*_triple(store_unit), unit=store_unit)
    with_ff = dock.unload_seconds(*_triple(ff_unit), unit=ff_unit)
    assert plain == with_store == with_ff, (
        f'a single-price dock consulted the unit it was handed: {plain!r} with no unit, '
        f'{with_store!r} with a store unit, {with_ff!r} with a FULFILLMENT unit — these '
        f'must be one number, and the comparison is exact because the claim is '
        f'byte-identity, not equivalence')


# ══════════════════════════════════════════════════════════════════════════════
# 2. The site dock prices each unit at its OWN regime's constant
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_dock_prices_each_unit_at_its_own_regimes_constant():
    """THE DECISION ITSELF.  Both expectations come from `unload_cost` directly, so this
    compares the dock against the cost model rather than against itself."""
    dock = _site_dock()
    store_unit, ff_unit = _units()
    want_store = unload_cost(*_triple(store_unit), _STORE_COST)
    want_ff = unload_cost(*_triple(ff_unit), _FF_COST)
    got_store = dock.unload_seconds(*_triple(store_unit), unit=store_unit)
    got_ff = dock.unload_seconds(*_triple(ff_unit), unit=ff_unit)
    assert abs(got_store - want_store) < _SECONDS_TOL, (
        f'the store pallet was charged {got_store!r}, not its own regime\'s '
        f'{want_store!r} (fulfillment\'s would be {want_ff!r})')
    assert abs(got_ff - want_ff) < _SECONDS_TOL, (
        f'the fulfillment bin was charged {got_ff!r}, not its own regime\'s {want_ff!r} '
        f'(store\'s would be {want_store!r})')


def test_the_two_durations_differ_in_the_direction_the_two_prices_imply():
    """A blend, or one channel's price winning, makes these two EQUAL — which is exactly
    what rejected answers 1 and 2 would have shipped, and exactly what the retired
    `C_store == C_ful` clause would have asserted."""
    dock = _site_dock()
    store_unit, ff_unit = _units()
    got_store = dock.unload_seconds(*_triple(store_unit), unit=store_unit)
    got_ff = dock.unload_seconds(*_triple(ff_unit), unit=ff_unit)
    assert _STORE_COST.intercept > _FF_COST.intercept, (
        'the fixture no longer prices store above fulfillment; the direction below is '
        'read off the declared costs, so it has to be declared')
    assert got_ff < got_store - _SECONDS_TOL, (
        f'the cheaper regime did not come out cheaper: fulfillment {got_ff!r} vs store '
        f'{got_store!r}; a per-regime list that resolves to one constant is a blend '
        f'wearing a list\'s shape')


def test_a_site_dock_holds_no_single_price_at_all():
    """The other half of "exactly one of the two", and it is what the `cost is None` branch
    in `unload_seconds` dispatches on.  A class default applied here would answer "no price
    for this regime" with the kernel's 0.5 s instead of refusing."""
    dock = _site_dock()
    assert dock.cost is None, (
        f'a site dock carries a single price as well as a list ({dock.cost!r}); the list '
        f'would then never be consulted')
    assert dock.costs is not None and set(dock.costs) == {STORE, FULFILLMENT}


def test_cost_for_returns_the_declared_object_for_each_regime():
    """The resolver the charge site uses, read directly — so a failure names the LOOKUP
    rather than the arithmetic that consumed it."""
    dock = _site_dock()
    assert dock.cost_for(STORE) is _STORE_COST
    assert dock.cost_for(FULFILLMENT) is _FF_COST


def test_the_price_list_is_copied_at_construction():
    """A caller's dict must not be able to re-price a running dock.  The crew's identity
    does not drift mid-run (`DockSpec` is frozen for the same reason), and a price is the
    part of it a sweep hashes."""
    handed = {STORE: _STORE_COST, FULFILLMENT: _FF_COST}
    dock = Dock(DockSpec(name='site_dock'), costs=handed)
    handed[STORE] = _FF_COST
    assert dock.cost_for(STORE) is _STORE_COST, (
        'mutating the dict handed to the constructor re-priced the dock')


# ══════════════════════════════════════════════════════════════════════════════
# 3. The five refusals
# ══════════════════════════════════════════════════════════════════════════════

def test_a_dock_handed_both_shapes_refuses():
    """One of the two would silently win, and which one decides every receiving second on
    the run."""
    with pytest.raises(ValueError, match='both one price and a per-regime price'):
        Dock(DockSpec(name='site_dock'), cost=_STORE_COST,
             costs={STORE: _STORE_COST, FULFILLMENT: _FF_COST})


def test_an_empty_price_list_refuses_at_construction():
    """At CONSTRUCTION, not at the first unload: an empty list prices nothing, so the
    refusal would otherwise land after the doors were filled and the crew charged."""
    with pytest.raises(ValueError, match='empty price list'):
        Dock(DockSpec(name='site_dock'), costs={})


def test_a_site_dock_asked_for_a_price_with_no_unit_refuses():
    """"How long does an unload take" has no site-wide answer under a list — and the
    caller holds the unit at every charge site, so there is nothing to fall back TO."""
    dock = _site_dock()
    store_unit, _ = _units()
    with pytest.raises(ValueError, match='no unit to resolve the regime from'):
        dock.unload_seconds(*_triple(store_unit))
    with pytest.raises(ValueError, match='no unit to resolve the regime from'):
        dock.unload_seconds(*_triple(store_unit), unit=None)


def test_an_uncoupled_dock_refuses_to_answer_which_regimes_price():
    """It must NOT answer with its one price.  An answer would make the caller's regime
    resolution look consulted when it was ignored — the single-price dock has no opinion
    about which regime, which is a different thing from having one opinion for all of them.
    """
    dock = _leaf_dock()
    with pytest.raises(ValueError, match='holds ONE unload price'):
        dock.cost_for(STORE)


def test_a_site_dock_refuses_a_regime_it_has_no_entry_for():
    """Pricing an undeclared regime at another regime's rate would charge a store pallet at
    a tote price with nothing saying so."""
    dock = _site_dock()
    with pytest.raises(ValueError, match='no unload price for the .nosuch. regime'):
        dock.cost_for('nosuch')


def test_a_site_dock_refuses_a_unit_whose_regime_it_does_not_price():
    """The same refusal reached through the CHARGE SITE, which is where a real run meets
    it: a one-entry list and a fulfillment unit is a mis-built site dock, not a free
    fallback to the entry that happens to exist."""
    dock = Dock(DockSpec(name='site_dock'), costs={STORE: _STORE_COST})
    _, ff_unit = _units()
    with pytest.raises(ValueError, match='no unload price for the .fulfillment. regime'):
        dock.unload_seconds(*_triple(ff_unit), unit=ff_unit)


# ══════════════════════════════════════════════════════════════════════════════
# 4. take_records / drain_records — the reset has exactly one owner
# ══════════════════════════════════════════════════════════════════════════════

def _charge_two(dock: Dock) -> float:
    """Book two unloads and append their rows, the way the drain does.  Returns the crew's
    finish, which is what the reset is observed through."""
    store_unit, _ = _units()
    for _ in range(2):
        dur = dock.unload_seconds(*_triple(store_unit))
        t0, w = dock.charge(dur)
        dock.records.append((t0, dur, store_unit.order.sku, store_unit.quantity, w))
    return dock.finish


def test_take_records_hands_the_rows_over_and_leaves_the_clock_where_it_was():
    """The coordinator needs the rows WITHOUT the reset: on a coupled run it partitions
    them between two leaves and the reset belongs to `SiteReceiving.note_records`, which
    fires once both leaves have stamped."""
    dock = _leaf_dock()
    finish = _charge_two(dock)
    assert finish > 0.0, 'the fixture charged nothing, so a reset could not be observed'
    rows = dock.take_records()
    assert len(rows) == 2, f'take_records handed back {len(rows)} row(s), expected 2'
    assert dock.records == [], 'take_records left the batch\'s rows behind'
    assert abs(dock.finish - finish) < _SECONDS_TOL, (
        f'take_records restarted the crew\'s clock ({finish!r} -> {dock.finish!r}); the '
        f'reset has one owner and this is not it')


def test_drain_records_is_take_records_plus_the_reset():
    """Both halves, and the reset is load-bearing: without it the batch-local clock
    accumulates across the arm while the runner still adds the epoch, and every row after
    batch 0 is stamped late by every preceding batch's receiving seconds."""
    dock = _leaf_dock()
    finish = _charge_two(dock)
    assert finish > 0.0, 'the fixture charged nothing, so a reset could not be observed'
    rows = dock.drain_records()
    assert len(rows) == 2, f'drain_records handed back {len(rows)} row(s), expected 2'
    assert dock.records == [], 'drain_records left the batch\'s rows behind'
    assert abs(dock.finish) < _SECONDS_TOL, (
        f'drain_records left the crew\'s clock at {dock.finish!r}; a drain IS a batch '
        f'boundary and the clock restarts at 0')


def test_the_two_hand_back_the_same_rows_for_the_same_batch():
    """Same rows, same order — the ONLY difference between the two is the clock.  Two docks
    charged identically, so a difference can only come from the drain."""
    a, b = _leaf_dock(), _leaf_dock()
    _charge_two(a)
    _charge_two(b)
    assert a.take_records() == b.drain_records(), (
        'take_records and drain_records handed back different rows for an identically '
        'charged batch; they must differ in the clock alone')


def test_a_drain_after_a_take_is_still_a_reset_and_hands_back_nothing():
    """The coupled sequence, in order: the coordinator TAKES, then something resets.  The
    second call must not resurrect rows and must still restart the clock."""
    dock = _leaf_dock()
    finish = _charge_two(dock)
    dock.take_records()
    assert abs(dock.finish - finish) < _SECONDS_TOL
    assert dock.drain_records() == [], 'a second drain in one batch produced rows'
    assert abs(dock.finish) < _SECONDS_TOL, (
        f'the clock was left at {dock.finish!r} after a drain following a take')


if __name__ == '__main__':                                        # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, '-v']))
