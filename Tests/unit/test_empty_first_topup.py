"""test_empty_first_topup.py — ADR-0003: put-away fills an empty bin first, and only then
consolidates into a bin already holding the SKU.

Under base stock every pick reorders what it took, so a line that takes part of a shelf leaves
a remnant and the top-up arrives as its own unit.  Put-away never added to an occupied bin, so
each top-up opened a second bin; against a warehouse sized near one bin per SKU the free index
ran dry and units sat on order and never on a shelf.  ADR-0003 adds ONE rung to the chain —
the SKU's own bins, fullest first — and puts it in a very specific place.  The place is the
whole decision, so it is what these tests pin.

What a failure here means:

  * **The rung fires in the wrong place.**  `_top_up_own_bins` runs after `place_one` returns
    None and BEFORE the repack / singleton rescues.  A rescue that fires while the SKU's own
    shelf had room is rework the ADR exists to stop, so every ordering test asserts
    `_recv_repacks == 0` with the rescue demonstrably armed — a free smaller tier standing
    ready and a positive `_max_qty_fitting_size` for it.  Without that guard the assertion is
    vacuous: a rescue that could not fire proves nothing about the order.
  * **The rung picks the wrong bin.**  FULLEST first, ties by `location`.  Fullest-first is
    what leaves the emptiest bin emptiest so `drain_sku`'s smallest-first pick can take it to
    zero and hand it back to the free index; the two rules are one mechanism.  The fixture is
    built so fullest-first, location-first and a reversed tie-break each choose a DIFFERENT
    bin — otherwise the test could not fail.
  * **The rung fires when it should not.**  With an empty bin always available the rung is a
    strict no-op, so a run whose free index never exhausts places exactly what it placed
    before ADR-0003.  Scoped to the rung: the drain-order half of the ADR
    (`Workload_Builder.drain_sku`) is deliberately NOT byte-identical and is tested for its
    new rule instead.
  * **The rework goes unpriced.**  A rescue is receiving work: one unload-priced act per
    RESULTING pack, on the receiving crew's clock, counted even with no dock bound.

Non-vacuity note: the item shape here (40x40x2) is chosen so the pallet size tiers actually
differ — 6 / 12 / 18 / 24 per bin for small / medium / large / extra_large.  With the item
shapes most fixtures in this suite use, every tier holds the same quantity, the repack rescue
can never split anything, and half of these assertions would hold for the wrong reason.

Run:  python -m pytest Tests/unit/test_empty_first_topup.py -q
"""
from __future__ import annotations

import random
from collections import defaultdict

import pytest

from Inbound.dock import Dock, DockSpec
from Optimization.metrics.bin_recorder import BinRecorder
from Optimization.metrics.work_events import repack_rows
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import (
    PutawayItem, _max_qty_fitting_size, own_bin_room,
)
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import Pallet, Singleton, _max_qty_fits
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig,
)
from Warehouse.operations.roles import Mode, Role
from Warehouse.operations.worker import Crew
from Warehouse.picking.Workload_Builder import drain_sku

#: Per-bin capacity of the fixture item, by pallet size tier.  Asserted in
#: `test_the_fixture_item_has_a_real_tier_ladder` so a change to the packing rules fails
#: THERE, loudly, instead of silently making every ordering fixture below degenerate.
_TIER_CAPACITY = {'small': 6, 'medium': 12, 'large': 18, 'extra_large': 24}

#: Durations are floats; nothing here compares one with `==`.
_SECONDS_TOL = 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

def _order(sku: int = 1, dims: tuple[int, int, int] = (40, 40, 2)) -> Order:
    """A minimal stocked Order with FIXED physical dimensions.

    Built with `object.__new__` — the idiom `test_putaway_budget` and
    `test_task_bin_selection_determinism` already use — because `Order.__init__` samples its
    dimensions from the global RNG, and every capacity in this file is derived from them.

    The default shape is wide and flat: it fills a small pallet bin at 6 and an extra_large
    one at 24, so the size tiers are genuinely different capacities.  That is what arms the
    repack rescue, which is what makes "the rescue did not fire" a claim with content.
    """
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length, c.width, c.height = dims
    c.weight                = 5
    c.demand                = Demand.from_rates(0.8, 4.0)
    c.lead_time_mean        = 0.0
    c.supply_cv             = 0.0
    c.expected_batch_demand = 3.2
    # The level is the run's declaration and `declare_stock` is its one mutation site
    # (ADR-0002).  Nothing here reads it — every quantity below is placed explicitly — but
    # an undeclared order raises on the first `_equilibrium_qty`, so it is declared.
    return c.declare_stock(48, 1, stock_plan=None)


def _warehouse(*, small_aisle: bool = True, columns: int = 2, levels: int = 1):
    """Aisle 1 = MEDIUM pallet bins (the SKU's own bins); aisle 2 = SMALL pallet bins.

    The two-tier layout is the point.  A unit of 7..12 items is a `medium` pallet, and
    `_candidates_raw` spills UP only — so with the medium tier full, the free small bins are
    invisible to `place_one` and visible to the repack rescue.  That is the exact
    configuration ADR-0003 legislates over: no empty bin fits, but a rescue could still fire.

    `small_aisle=False` removes the rescue's landing ground entirely, which is how the
    remainder tests observe a requeue instead of a placement.

    `Aisle.next_aisle_id` is class state and MUST be reset, or aisle ids — hence `location`,
    hence every ordering expectation here — drift with test ordering.
    """
    Aisle.next_aisle_id = 1
    # `_uniform_assignment` draws from the bare global RNG; seed it so the equivalence
    # fixtures below place identically across two builds in one process.
    random.seed(1789)
    w, h = aisle_width_for(columns), aisle_height_for(levels)
    cfgs = [AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium'], None)]
    if small_aisle:
        cfgs.append(AisleConfig('conveyable', 'food', 'pallet', w, h, ['small'], None))
    n = len(cfgs)
    cfg = WarehouseConfig(total_aisles=n, aisle_splits=[1.0 / n] * n, aisle_configs=cfgs)
    return Warehouse_Builder().from_config(cfg).build()


def _medium_bins(wh) -> list:
    return sorted((b for b in wh.bins if b.storage_size == 'medium'),
                  key=lambda b: b.location)


def _small_bins(wh) -> list:
    return sorted((b for b in wh.bins if b.storage_size == 'small'),
                  key=lambda b: b.location)


def _stocked(on_hand: list[int], *, small_aisle: bool = True,
             columns: int = 2, levels: int = 1):
    """A warehouse whose MEDIUM bins hold `on_hand[i]` items of SKU 1, in location order.

    Placed through `_execute_placement`, the manager's own commit point, so every index the
    top-up rung reads (`_sku_pallet_bins`, `_index`, `_current_quantities`) is exactly what a
    real intake would have left.  Explicit per-bin quantities rather than `enqueue`, because
    `enqueue` packs full pallets and a full bin has no room — the state this ADR is about is
    a PARTLY drawn-down shelf.

    Returns `(warehouse, manager, order, medium_bins)`.
    """
    wh    = _warehouse(small_aisle=small_aisle, columns=columns, levels=levels)
    mgr   = Inventory_Manager(wh)
    order = _order()
    for qty, bin_ in zip(on_hand, _medium_bins(wh)):
        mgr._execute_placement(Pallet(order, qty), bin_, source='intake')
    return wh, mgr, order, _medium_bins(wh)


def _offer(mgr, order: Order, qty: int, *, source: str = 'reorder') -> list:
    """Queue ONE reorder unit of `qty` items and run the drain.  Returns the top-up log.

    The log is `[(location, n), ...]` in commit order, captured by wrapping
    `_execute_topup` on the instance — the same rebinding seam `BinRecorder` uses, so the
    ordering claim is read off the production commit point rather than re-derived from the
    final quantities (which cannot distinguish "filled b then c" from "filled c then b").

    The on-order ledgers are stamped by hand because the unit is pushed straight onto the
    put queue, bypassing `_admit`: `_execute_topup` decrements `_queued_qty` and the drain
    decrements `_queued_sku_counts`, and a ledger that was never credited cannot be seen to
    be debited.
    """
    sku = order.sku
    mgr._queued_qty[sku]         = mgr._queued_qty.get(sku, 0) + qty
    mgr._queued_sku_counts[sku]  = mgr._queued_sku_counts.get(sku, 0) + 1
    mgr._stock_queue.append(PutawayItem(Pallet(order, qty), source))
    log: list = []
    original = mgr._execute_topup

    def _spy(order_, bin_, n, *, source=None, queue=None):
        log.append((bin_.location, n))
        return original(order_, bin_, n, source=source, queue=queue)

    mgr._execute_topup = _spy
    try:
        mgr._stock()
    finally:
        mgr._execute_topup = original
    return log


def _on_hand(bin_) -> int:
    return bin_.storage.quantity if bin_.storage is not None else 0


def _shelf_items(wh) -> int:
    """Every item standing in a bin, anywhere in the warehouse."""
    return sum(_on_hand(b) for b in wh.bins)


def _queued_items(mgr) -> int:
    """Every item still upstream of a bin: on a put queue, held, or on the dock."""
    q = sum(it.unit.quantity for qq in mgr.put_queues for it in qq.items)
    q += sum(it.unit.quantity for it in mgr._held)
    if mgr._dock is not None:
        q += sum(it.unit.quantity for it in mgr._dock.items)
    return q


def _fingerprint(wh, mgr) -> tuple:
    """The PHYSICAL state of a run: what is in every bin, and what is still waiting.

    Deliberately excludes the ADR's own counters.  An equivalence built on a fingerprint
    that contained `put_topups` would report a difference whenever the rung fired, which is
    what the equivalence is supposed to be measuring rather than assuming.
    """
    shelves = tuple(sorted((b.location, b.storage.order.sku, b.storage.quantity)
                           for b in wh.bins if b.storage is not None))
    waiting = tuple(sorted((it.unit.order.sku, it.unit.quantity, it.unit.storage_size)
                           for qq in mgr.put_queues for it in qq.items))
    return (shelves, waiting)


def _drive(mgr, order: Order, *, n: int = 8, seed: int = 20260907) -> list[int]:
    """`n` reorder units of 7..12 items each — every one a `medium` pallet.

    Seeded from an explicit `random.Random` (never the bare global, which the placement
    policy is using) so the same offer sequence can be replayed against two managers.
    """
    rng  = random.Random(seed)
    qtys = [rng.randint(7, 12) for _ in range(n)]
    for qty in qtys:
        _offer(mgr, order, qty)
    return qtys


def _dock(size: int = 1) -> Dock:
    return Dock(DockSpec(name='dock', size=size))


def _rescue_is_armed(mgr, order: Order) -> bool:
    """Could the repack rescue fire right now, if the own-bin rung did not exist?

    The rescue needs a free bin in a SMALLER tier than the unit's and a positive quantity
    fitting it.  Every ordering test below asserts this first: `_recv_repacks == 0` is only
    evidence about the ORDER of the chain if the next rung was ready to run.
    """
    free = mgr._index.get(('conveyable', 'food', 'small', 'pallet')) or []
    return bool(free) and _max_qty_fitting_size(order, 'small', 'pallet') > 0


# ══════════════════════════════════════════════════════════════════════════════
# The fixture's own premises
# ══════════════════════════════════════════════════════════════════════════════

def test_the_fixture_item_has_a_real_tier_ladder():
    """Every ordering fixture below assumes the four pallet tiers hold DIFFERENT amounts.

    If the packing rules ever flatten them, `place_one` and the repack rescue stop being
    distinguishable, the "no empty bin fits" premise collapses, and a dozen assertions here
    would start passing for the wrong reason.  Fail here instead.
    """
    order = _order()
    got = {size: _max_qty_fitting_size(order, size, 'pallet') for size in _TIER_CAPACITY}
    assert got == _TIER_CAPACITY, (
        f'the fixture item no longer has a tier ladder: {got} != {_TIER_CAPACITY}; '
        f're-choose the dimensions in _order() before trusting anything in this file')
    assert Pallet(order, 7).storage_size == 'medium', (
        'a 7-item unit is no longer a medium pallet, so it may now fit a free small bin '
        'and the "no empty bin fits" premise of every ordering test is gone')


# ══════════════════════════════════════════════════════════════════════════════
# own_bin_room — the ceiling is the BIN's tier, not the family's largest
# ══════════════════════════════════════════════════════════════════════════════

def test_own_bin_room_measures_against_the_bins_own_tier():
    """A small bin holds what a small bin holds, whatever a larger one could.

    The bin was built to a size and the unit standing in it has to keep fitting; measuring
    against the family's largest tier would let the rung overfill a shelf by 18 items with
    nothing raising, because nothing re-checks the fit at `_execute_topup`.
    """
    wh, mgr, order, _med = _stocked([1, 5, 5, 12])
    small = _small_bins(wh)[0]
    mgr._execute_placement(Pallet(order, 2), small, source='intake')
    assert own_bin_room(order, small) == _TIER_CAPACITY['small'] - 2, (
        f'room in a small bin holding 2 is {own_bin_room(order, small)}; the small tier '
        f'holds {_TIER_CAPACITY["small"]}, so it must be '
        f'{_TIER_CAPACITY["small"] - 2}')
    # The value that would come back if the ceiling were the family's largest tier — this is
    # what the assertion above is defending against, so it must genuinely differ.
    assert _TIER_CAPACITY['extra_large'] - 2 != _TIER_CAPACITY['small'] - 2


def test_own_bin_room_measures_a_singleton_against_the_singleton_family():
    """`_max_qty_fitting_size` falls back to the PALLET tables for an unknown category.

    A singleton bin carries no tier (`storage_size is None`), so that fallback would measure
    it against the small-pallet table and overstate its room — here by 8 items on a bin that
    can take 6.  The rung would then commit a quantity the bin cannot physically hold.
    """
    Aisle.next_aisle_id = 1
    random.seed(1789)
    w, h = aisle_width_for(2), aisle_height_for(1)
    wh = Warehouse_Builder().from_config(WarehouseConfig(
        total_aisles=1, aisle_splits=[1.0],
        aisle_configs=[AisleConfig('conveyable', 'food', 'singleton', w, h,
                                   ['singleton'], None)])).build()
    mgr   = Inventory_Manager(wh)
    # A compact item, so a singleton bin can actually hold some: 16 per singleton against
    # 24 per small pallet.  The default fixture shape does not fit a singleton at all.
    order = _order(sku=2, dims=(8, 8, 2))
    bin_  = sorted(wh.bins, key=lambda b: b.location)[0]
    mgr._execute_placement(Singleton(order, 10), bin_, source='intake')

    singleton_cap = _max_qty_fits(order, Singleton)
    pallet_fallback = _max_qty_fitting_size(order, bin_.storage_size, bin_.unit_type)
    assert pallet_fallback > singleton_cap, (
        f'the pallet fallback ({pallet_fallback}) no longer overstates a singleton '
        f'({singleton_cap}), so this test cannot distinguish the two paths')
    assert own_bin_room(order, bin_) == singleton_cap - 10, (
        f'own_bin_room said {own_bin_room(order, bin_)} for a singleton bin holding 10 of '
        f'{singleton_cap}; the pallet-table answer would have been '
        f'{pallet_fallback - 10}')


def test_own_bin_room_is_zero_for_an_empty_bin_and_for_a_full_one():
    """Zero is the answer for both, and the rung's `room <= 0: continue` depends on it: an
    empty bin belongs to `place_one`, and a full one must be skipped rather than overfilled.
    """
    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    empty = _small_bins(wh)[0]
    assert own_bin_room(order, empty) == 0, 'an empty bin has no OWN-bin room by definition'
    full = med[3]
    assert _on_hand(full) == _TIER_CAPACITY['medium']
    assert own_bin_room(order, full) == 0, (
        f'a full medium bin reported {own_bin_room(order, full)} items of room')


# ══════════════════════════════════════════════════════════════════════════════
# The rung's PLACE in the chain — ahead of the rescues
# ══════════════════════════════════════════════════════════════════════════════

def test_a_unit_that_fits_no_empty_bin_lands_in_its_own_bin_before_any_rescue():
    """THE ADR, in one test.  The rescue is armed and must not fire.

    The unit is a `medium` pallet and every medium bin is occupied, so `place_one` returns
    None.  Eight free SMALL bins stand ready and the small tier holds 6 of this item, so the
    repack rescue COULD split the unit and place it — that is what it did before ADR-0003.
    It must not, because the SKU's own shelf has room: a rescue is rework, a top-up is a put.
    """
    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    assert _rescue_is_armed(mgr, order), 'the rescue is not armed — the test cannot fail'
    assert not (mgr._index.get(('conveyable', 'food', 'medium', 'pallet')) or []), (
        'a free medium bin is left, so `place_one` will succeed and the rung never runs')

    log = _offer(mgr, order, 7)

    assert mgr._recv_repacks == 0, (
        f'{mgr._recv_repacks} rescue(s) fired while the SKU had {own_bin_room(order, med[1])}'
        f'+ items of room on its own shelf; the own-bin rung must come first')
    assert mgr._recv_repacked_packs == 0
    assert mgr._put_topups == 1, f'the own-bin rung recorded {mgr._put_topups} top-ups, not 1'
    assert log == [(med[1].location, 7)], f'top-up log was {log}'
    assert _on_hand(med[1]) == 12, f'the own bin holds {_on_hand(med[1])}, not 12'
    assert mgr.queue_depth == 0, (
        f'{mgr.queue_depth} unit(s) left queued; the unit was absorbed whole and must be '
        f'off the queue for good')
    assert all(b.storage is None for b in _small_bins(wh)), (
        'a small bin was used — the rescue fired after all, or the unit was split')


def test_the_top_up_fills_the_fullest_own_bin_first_breaking_ties_by_location():
    """FULLEST FIRST, ties by `location` — and the fixture makes all three rules disagree.

    On-hand `[1, 5, 5, 12]` over locations `(1,1,1) < (1,1,2) < (1,2,1) < (1,2,2)`:

      * fullest-with-room  -> (1,1,2)   (5 on hand, tie broken to the lower location)
      * location-first     -> (1,1,1)   (11 items of room, the emptiest bin)
      * reversed tie-break -> (1,2,1)

    Fullest-first is not a preference: it leaves the emptiest bin emptiest so `drain_sku`'s
    smallest-first pick can take it to zero and return it to the free index.  Consolidating
    into the emptiest bin instead would fragment the SKU further on every top-up.
    """
    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    location_first, fullest, tie_loser, full = med[0], med[1], med[2], med[3]
    assert own_bin_room(order, location_first) > own_bin_room(order, fullest) > 0, (
        'the fixture no longer separates fullest-first from location-first')
    assert _on_hand(fullest) == _on_hand(tie_loser), 'the fixture no longer has a tie'

    _offer(mgr, order, 7)

    assert _on_hand(fullest) == 12, (
        f'the 7 items did not land in the fullest bin with room {fullest.location}; '
        f'shelves are {[(b.location, _on_hand(b)) for b in med]}')
    assert _on_hand(location_first) == 1, (
        f'{location_first.location} took the top-up — that is location order, not '
        f'fullest-first')
    assert _on_hand(tie_loser) == 5, (
        f'{tie_loser.location} took the top-up — the tie broke to the HIGHER location')
    assert _on_hand(full) == 12, 'the already-full bin was written to'


def test_a_unit_that_does_not_fit_whole_fills_own_bins_to_capacity_and_sends_the_rest_on():
    """Twelve items into eight items of room: fill in order, requeue the remainder as ONE unit.

    On-hand `[8, 10, 10, 12]` -> rooms `[4, 2, 2, 0]`.  Fullest-first fills (1,1,2) then
    (1,2,1) then (1,1,1), which is NOT location order, so the commit log distinguishes them.
    No small aisle here, so the remainder has nowhere to be rescued to and its landing place
    is the queue — which is what makes "sends the remainder on" observable as a single unit
    of exactly the un-landed quantity, rather than as a split the rescues performed.
    """
    wh, mgr, order, med = _stocked([8, 10, 10, 12], small_aisle=False)
    rooms = [own_bin_room(order, b) for b in med]
    assert rooms == [4, 2, 2, 0], f'fixture rooms drifted: {rooms}'
    assert sum(rooms) < 12, 'the unit fits whole — nothing is sent on and the test is empty'

    log = _offer(mgr, order, 12)

    assert log == [(med[1].location, 2), (med[2].location, 2), (med[0].location, 4)], (
        f'fill order/quantities were {log}; expected fullest-first to capacity')
    assert [_on_hand(b) for b in med] == [12, 12, 12, 12], (
        f'own bins are {[_on_hand(b) for b in med]}, not all at capacity')
    assert mgr._put_topups == 3, (
        f'{mgr._put_topups} top-ups counted for a unit that filled THREE own bins; the flow '
        f'counts bins touched, not calls -- one top-up is one unit landing in one occupied '
        f'bin, which is one `bin_placement` row and one trip.  The audit divides it by '
        f'`reorder_placements`, which is also per bin, so a per-call count would understate '
        f'the own-bin share exactly when a unit was split across the most bins')
    assert mgr._recv_repacks == 0, 'a rescue fired on the remainder path'

    waiting = [it.unit for qq in mgr.put_queues for it in qq.items]
    assert len(waiting) == 1, (
        f'{len(waiting)} units left waiting; the remainder goes back as exactly ONE unit')
    assert waiting[0].quantity == 12 - sum(rooms), (
        f'the requeued remainder is {waiting[0].quantity}, not {12 - sum(rooms)}')
    assert mgr._queued_sku_counts.get(order.sku) == 1, (
        f'_queued_sku_counts is {mgr._queued_sku_counts}; one unit in, one unit out, so the '
        f'count must not move on a partial fill')


def test_the_remainder_re_enters_the_chain_and_still_prefers_an_empty_bin():
    """The remainder takes the chain FROM THE TOP, so empty-first applies to it too.

    Same fill as above, but with the small aisle present.  The 4-item remainder is a `small`
    pallet, and a small bin is empty, so it lands there — a new bin, not a rescue and not the
    queue.  A remainder that skipped straight to the rescues would be a second, quieter
    violation of the same ADR.
    """
    wh, mgr, order, med = _stocked([8, 10, 10, 12])
    _offer(mgr, order, 12)

    assert [_on_hand(b) for b in med] == [12, 12, 12, 12]
    used = [b for b in _small_bins(wh) if b.storage is not None]
    assert len(used) == 1, (
        f'{len(used)} small bins were used; the remainder is one unit and takes one bin')
    assert _on_hand(used[0]) == 4, f'the remainder bin holds {_on_hand(used[0])}, not 4'
    assert mgr._recv_repacks == 0, 'the remainder was rescued instead of placed'
    assert mgr.queue_depth == 0, 'the remainder is still queued'


def test_a_top_up_changes_none_of_the_bookkeeping_that_is_about_a_bin_being_TAKEN():
    """The fifth mutation site updates the merchandise ledgers and nothing else.

    `_execute_topup` is the only bin write that adds to an ALREADY occupied bin, so every
    dict that answers "which bins does this SKU hold" was already correct before it ran:

      * the free index and `_unavailable` — the bin left the index when it was first taken;
      * `_sku_pallet_bins` — the SKU gained no bin;
      * Sigma f*D — it counts a SKU's bin OCCUPANCY, and a delta here would charge the same
        bin twice, silently biasing every optimal-map target that reads it;
      * `space_timeline.fill` — it means "a free bin became occupied", which is not news
        about a bin that was already occupied.

    None of those would raise.  Each is checked against an ordinary placement in the same
    test, so a build that simply stopped maintaining them fails on the second half.
    """
    import types

    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    mgr.enable_sigma_fd({order.sku: 1.0}, x_speed=4.0, y_speed=2.0)
    fills: list = []
    mgr.space_timeline = types.SimpleNamespace(fill=fills.append)

    before = (len(mgr._sku_pallet_bins[order.sku]), mgr.free_bin_depth(),
              len(mgr._unavailable), mgr.tracked_sigma_fd())
    _offer(mgr, order, 7)
    after = (len(mgr._sku_pallet_bins[order.sku]), mgr.free_bin_depth(),
             len(mgr._unavailable), mgr.tracked_sigma_fd())

    assert mgr._put_topups == 1, 'the rung did not fire — nothing is being asserted'
    assert after == before, (
        f'a top-up moved the bin bookkeeping: {before} -> {after} '
        f'(sku bins, free index, occupied bins, sigma f*D)')
    assert fills == [], (
        f'the top-up reported {len(fills)} bin(s) filling to the space timeline; the bin '
        f'was already occupied')

    # Non-vacuity: the SAME four numbers must move for a placement into an EMPTY bin.
    _offer(mgr, order, 4)      # a `small` pallet — it fits a free small bin
    moved = (len(mgr._sku_pallet_bins[order.sku]), mgr.free_bin_depth(),
             len(mgr._unavailable), mgr.tracked_sigma_fd())
    assert moved != after, 'an ordinary placement moved none of them either — nothing is wired'
    assert len(fills) == 1, f'the ordinary placement reported {len(fills)} fills, not 1'


def test_a_top_up_is_charged_as_one_put_PER_BIN_TOUCHED():
    """Three bins topped up is three trips, priced on what landed in EACH of them.

    A putter walks to a bin, puts some down, and walks to the next one — so the travel term
    is paid three times, and the handling term is paid on 2, 2 and 4 items rather than on
    the 12 the unit was cut from.  Charging the whole unit once would make a fragmented
    warehouse look as cheap as a tidy one, which is the comparison this ADR exists to move.
    `_reorder_placements` — the churn counter the runner reports per batch — counts the same
    three, for the same reason.
    """
    wh, mgr, order, med = _stocked([8, 10, 10, 12], small_aisle=False)
    mgr.enable_putaway_timing(SpeedProfile(2.0, 4.0))
    before_placements = mgr._reorder_placements
    mgr.drain_putaway_records()          # clear the intake charges; start from a clean clock

    _offer(mgr, order, 12)

    assert mgr._reorder_placements - before_placements == 3, (
        f'{mgr._reorder_placements - before_placements} placements counted for a top-up '
        f'that touched 3 bins')
    records = mgr.drain_putaway_records()
    assert len(records) == 3, f'{len(records)} put records for 3 bins touched'
    # record shape: (t_start, dur, sku, qty, aisle_id, x, y, source, worker, queue)
    assert [r[3] for r in records] == [2, 2, 4], (
        f'put quantities were {[r[3] for r in records]}; each trip carries what landed in '
        f'THAT bin, not the whole unit')
    assert all(r[7] == 'reorder' for r in records), (
        f'put sources were {[r[7] for r in records]}; the top-up must keep the origin the '
        f'queue declared')
    assert mgr.putaway_seconds > 0.0, 'three trips cost no time'


def test_the_rung_skips_a_bin_that_is_already_full():
    """A full own bin has zero room and must be passed over, not written to.

    `_execute_topup` does not re-check the fit — it commits what it is handed — so the
    `room <= 0: continue` in the rung is the only thing standing between a full shelf and an
    overfilled one.
    """
    wh, mgr, order, med = _stocked([12, 12, 3, 12])
    log = _offer(mgr, order, 8)
    assert log == [(med[2].location, 8)], (
        f'top-up log {log}: the only bin with room is {med[2].location}')
    assert [_on_hand(b) for b in med] == [12, 12, 11, 12], (
        f'shelves are {[_on_hand(b) for b in med]}; a full bin was written to')


# ══════════════════════════════════════════════════════════════════════════════
# Equivalence — the rung is a strict no-op while an empty bin still fits
# ══════════════════════════════════════════════════════════════════════════════

def test_the_own_bin_rung_never_fires_while_an_empty_bin_still_fits(monkeypatch):
    """A run whose free index never exhausts places EXACTLY what it placed before ADR-0003.

    The control is the pre-ADR chain, reproduced by stubbing the rung to return 0 — which is
    precisely what the fallback did before it existed: fall through to the rescues.  Both
    runs get the same eight offers and the same seed, and must end on the same shelves.

    Scoped to the RUNG.  The ADR's other half — `drain_sku`'s smallest-first pick — is
    deliberately not byte-identical to what it replaced, and is pinned by its own tests below
    rather than by an equivalence that would have to fail.
    """
    # 16 medium bins, 4 of them stocked: the offers can never exhaust the free index.
    wh_a, mgr_a, order_a, _ = _stocked([1, 5, 5, 12], columns=4, levels=2)
    qtys = _drive(mgr_a, order_a)

    assert mgr_a._put_topups == 0, (
        f'the own-bin rung fired {mgr_a._put_topups} time(s) with empty bins available; it '
        f'is supposed to be unreachable while `place_one` can succeed')
    assert mgr_a._reorder_placements > len(qtys), (
        'the fixture placed nothing — an equivalence over an idle run proves nothing')
    assert mgr_a.free_bin_depth() > 0, (
        'the free index ran dry after all, so this is not the never-exhausts case')
    assert mgr_a.queue_depth == 0, 'a unit failed to place in a warehouse with free bins'

    monkeypatch.setattr(Inventory_Manager, '_top_up_own_bins',
                        lambda self, unit, source=None, queue=None: 0)
    wh_b, mgr_b, order_b, _ = _stocked([1, 5, 5, 12], columns=4, levels=2)
    assert _drive(mgr_b, order_b) == qtys, 'the two runs were offered different work'

    assert _fingerprint(wh_a, mgr_a) == _fingerprint(wh_b, mgr_b), (
        'the run with the own-bin rung landed somewhere different from the pre-ADR path, '
        'in a warehouse that never ran out of empty bins')


def test_the_equivalence_fixture_can_actually_detect_the_rung(monkeypatch):
    """The non-vacuity twin of the test above: same driver, a warehouse that DOES run dry.

    Without this, `test_the_own_bin_rung_never_fires...` would pass just as happily against a
    build where the rung was deleted, or where the fingerprint compared nothing.  Here the
    rung must fire, and stubbing it out must move the answer.
    """
    wh_a, mgr_a, order_a, _ = _stocked([1, 5, 5, 12], small_aisle=False)
    _drive(mgr_a, order_a)
    assert mgr_a._put_topups > 0, (
        'the cramped fixture never fired the rung, so it cannot witness a difference')
    assert mgr_a.free_bin_depth() == 0, 'the free index did not run dry'

    monkeypatch.setattr(Inventory_Manager, '_top_up_own_bins',
                        lambda self, unit, source=None, queue=None: 0)
    wh_b, mgr_b, order_b, _ = _stocked([1, 5, 5, 12], small_aisle=False)
    _drive(mgr_b, order_b)

    assert _fingerprint(wh_a, mgr_a) != _fingerprint(wh_b, mgr_b), (
        'disabling the own-bin rung changed nothing in a warehouse with a dry free index — '
        'the fingerprint cannot see the rung, so the equivalence test above is vacuous')


# ══════════════════════════════════════════════════════════════════════════════
# The rework — a rescue is receiving work, priced per resulting pack
# ══════════════════════════════════════════════════════════════════════════════

def test_a_forced_rescue_is_charged_to_the_receiving_crew_per_resulting_pack():
    """Every own bin full, so the rung takes nothing and the repack rescue fires.

    Twelve items split into two small packs of six.  The receiving crew is charged ONE
    unload-priced act per RESULTING pack — not per act, and not per item — because somebody
    physically breaks the unit down and that is dock work at the dock's own price.  No travel
    term: the merchandise does not go anywhere to be repacked.
    """
    wh, mgr, order, med = _stocked([12, 12, 12, 12])
    dock = _dock()
    mgr.enable_receiving(dock)
    assert _rescue_is_armed(mgr, order)

    _offer(mgr, order, 12)

    assert mgr._put_topups == 0, (
        f'{mgr._put_topups} top-up(s) fired; with every own bin full this must be the RESCUE '
        f'path or the test is measuring the wrong rung')
    assert mgr._recv_repacks == 1, f'{mgr._recv_repacks} rescue acts, expected 1'
    assert mgr._recv_repacked_packs == 2, (
        f'{mgr._recv_repacked_packs} resulting packs; 12 items at 6 per small bin is 2')

    one_pack = dock.unload_seconds(order.weight, order.volume(), 6)
    assert one_pack > 0.0, 'the dock priced a pack at zero seconds'
    assert mgr.receiving_seconds == pytest.approx(2 * one_pack, abs=_SECONDS_TOL), (
        f'receiving accrued {mgr.receiving_seconds}s for 2 packs priced at {one_pack}s each')
    assert dock.seconds == pytest.approx(mgr.receiving_seconds, abs=_SECONDS_TOL), (
        "the dock's own clock disagrees with the manager's receiving total")

    records = mgr.drain_repack_records()
    assert len(records) == 2, f'{len(records)} repack records for 2 packs'
    assert [(r[2], r[3]) for r in records] == [(order.sku, 6), (order.sku, 6)], (
        f'repack records carry {[(r[2], r[3]) for r in records]}')
    assert all(r[1] == pytest.approx(one_pack, abs=_SECONDS_TOL) for r in records)
    assert mgr.drain_repack_records() == [], 'the repack stream was not drained'
    assert mgr.drain_receiving_records() == [], (
        'a repack was written into the UNLOAD stream; the two are separate event types and '
        '`put_rows` writes one type per call')


def test_the_rework_is_counted_even_with_no_dock_bound():
    """A dockless run can still repack, and the staffing record expects ZERO of them.

    Counting only when a crew happens to be bound is how a rescue nobody priced becomes a
    rescue nobody knows about.  The flows count; the labour is simply not attributable.
    """
    wh, mgr, order, med = _stocked([12, 12, 12, 12])
    assert mgr._dock is None, 'the fixture bound a dock; this test is about not having one'

    _offer(mgr, order, 12)

    assert (mgr._recv_repacks, mgr._recv_repacked_packs) == (1, 2), (
        f'a dockless run counted {(mgr._recv_repacks, mgr._recv_repacked_packs)} rework')
    assert mgr.receiving_seconds == pytest.approx(0.0, abs=_SECONDS_TOL), (
        'seconds were accrued with nobody to accrue them to')
    assert mgr.drain_repack_records() == [], 'records exist with no dock to hold them'


def test_snapshot_putaway_rework_resets_the_flows_and_free_bin_depth_does_not():
    """Three FLOWS and one LEVEL, and the difference is not cosmetic.

    The flows reset because they are drained exactly once per batch and summing them across
    batches is the right thing to do; a level that reset would report the free index as zero
    on every batch after the first (see the `cut`-is-a-level trap, which is this one inverted).
    """
    wh, mgr, order, med = _stocked([12, 12, 12, 12])
    free_before = mgr.free_bin_depth()
    _offer(mgr, order, 12)

    assert mgr.free_bin_depth() == free_before - 2, (
        f'free index went {free_before} -> {mgr.free_bin_depth()}; the rescue took 2 bins')
    assert mgr.snapshot_putaway_rework() == (0, 1, 2), 'the first snapshot must report the batch'
    assert mgr.snapshot_putaway_rework() == (0, 0, 0), (
        'the flows survived a snapshot; a second call in the same batch would double-count '
        'them and the runner makes exactly one')
    assert mgr.free_bin_depth() == free_before - 2, (
        'free_bin_depth reset — it is a level, and a level is not a flow')


def test_repack_rows_write_the_repack_event_type_under_the_receive_role():
    """A repack IS receiving work, so `role='receive'`; only `event_type` separates it.

    That split is the whole point: "what did receiving cost" sums the role and gets unloads
    AND rework, while "how much rework was there" filters the event type.  Folding repacks
    into the `receive` event type makes the second question unanswerable; giving them their
    own role makes the first one wrong.
    """
    wh, mgr, order, med = _stocked([12, 12, 12, 12])
    mgr.enable_receiving(_dock())
    _offer(mgr, order, 12)
    records = mgr.drain_repack_records()
    assert records, 'no repack records to write'

    crew    = Crew(Role.RECEIVE, Mode.FOOT, SpeedProfile(1.0, 1.0), size=1)
    workers = crew.workers(first_uid=9)
    rows    = repack_rows(records, batch_id=3, batch_start=100.0, crew=workers)

    # `_WORK_EVENT_COLS` order: batch, seq, t_abs, t_local, shift, uid, local, role, mode,
    # event_type, aisle, sku, qty, duration, source.
    assert len(rows) == len(records)
    assert {r[9] for r in rows} == {'repack'}, (
        f'event types written: {sorted({r[9] for r in rows})}')
    assert {r[7] for r in rows} == {'receive'}, (
        f'roles written: {sorted({r[7] for r in rows})}; a repack is done by the receiving '
        f'crew and must carry their role')
    assert {r[5] for r in rows} == {9}, 'the pre-offset uid was not honoured'
    assert all(r[10] is None for r in rows), 'a dock has no aisle'
    assert all(r[12] == 6 for r in rows), f'quantities written: {[r[12] for r in rows]}'
    assert all(r[2] >= 100.0 for r in rows), 'rows are stamped before the batch started'


# ══════════════════════════════════════════════════════════════════════════════
# The spatial log — a top-up lands in an OCCUPIED bin
# ══════════════════════════════════════════════════════════════════════════════

def test_the_recorder_stamps_a_top_up_as_landing_in_an_occupied_bin():
    """`bin_placement.bin_state` is what makes the log replayable across ADR-0003.

    Every other PLACE row replaces a whole `storage`; this one ADDS to an existing unit's
    quantity.  A replayer folding a top-up row as a replacement would overwrite the bin with
    the delta and lose everything that was already on the shelf — and the row count would
    still reconcile.
    """
    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    recorder = BinRecorder(run_id=1)
    recorder.attach(mgr)
    recorder.begin_batch(4)

    _offer(mgr, order, 7)

    placements, _evictions = recorder.drain()
    assert len(placements) == 1, (
        f'{len(placements)} placement rows for one top-up: {placements}')
    row = placements[0]
    assert row.bin_state == 'occupied', (
        f"the top-up row is stamped bin_state={row.bin_state!r}; it landed in a bin that "
        f"already held the SKU")
    assert (row.aisle_id, row.bayX, row.bayY) == med[1].location
    assert row.qty == 7, f'the row records {row.qty} items, not the 7 that landed'
    assert row.cause == 'reorder', f'cause is {row.cause!r} inside the batch loop'
    assert row.score is None, (
        'the row carries a score — no policy chose this bin, the SKU\'s own on-hand did')
    assert recorder.units_placed == 7, (
        f'the conservation ledger counted {recorder.units_placed} units, not 7')


def test_an_ordinary_placement_is_still_stamped_empty():
    """The other half of the same column: a normal put-away lands in an EMPTY bin.

    Asserted beside the top-up so a build that stamped every row `'occupied'` — which would
    satisfy the test above on its own — fails here.
    """
    wh, mgr, order, med = _stocked([8, 10, 10, 12])
    recorder = BinRecorder(run_id=1)
    recorder.attach(mgr)
    recorder.begin_batch(0)

    _offer(mgr, order, 12)   # 8 items top up three own bins, 4 spill into an empty small bin

    placements, _ = recorder.drain()
    states = [r.bin_state for r in placements]
    assert states.count('occupied') == 3, f'bin states written: {states}'
    assert states.count('empty') == 1, f'bin states written: {states}'
    assert sorted(r.seq for r in placements) == list(range(len(placements))), (
        f'PLACE rows do not share one monotonic sequence: {[r.seq for r in placements]}; '
        f'two counters would collide on (run_id, batch_id, seq)')


# ══════════════════════════════════════════════════════════════════════════════
# drain_sku — the consolidation engine's other half
# ══════════════════════════════════════════════════════════════════════════════

def _drain_fixture():
    """Four bins of one SKU across BOTH tiers, with on-hand deliberately anti-correlated
    with `location` so smallest-first and location-first cannot agree.

    Returns `(singleton_bins, pallet_bins, by_location)`.
    """
    Aisle.next_aisle_id = 1
    random.seed(1789)
    w, h = aisle_width_for(2), aisle_height_for(1)
    wh = Warehouse_Builder().from_config(WarehouseConfig(
        total_aisles=2, aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['small'], None),
        ])).build()
    mgr   = Inventory_Manager(wh)
    order = _order(sku=3, dims=(8, 8, 2))
    singles = sorted((b for b in wh.bins if b.unit_type == 'singleton'),
                     key=lambda b: b.location)[:2]
    pallets = sorted((b for b in wh.bins if b.unit_type == 'pallet'),
                     key=lambda b: b.location)[:2]
    # location order:  s0 < s1  <  p0 < p1      (aisle 1 before aisle 2)
    # on-hand:         9    7        5    3     — strictly DECREASING along location
    for bin_, qty in ((singles[0], 9), (singles[1], 7)):
        mgr._execute_placement(Singleton(order, qty), bin_, source='intake')
    for bin_, qty in ((pallets[0], 5), (pallets[1], 3)):
        mgr._execute_placement(Pallet(order, qty), bin_, source='intake')
    return singles, pallets, [*singles, *pallets]


def test_drain_sku_takes_the_smallest_bin_first_across_both_tiers():
    """One ordering over both tiers, smallest on-hand first — the retired rule was
    singleton-before-pallet, ties by location, and this fixture makes them disagree on every
    bin.

    Smallest-first is what clears remnants so a bin returns to the free index, which is the
    only reason the empty-first top-up converges instead of fragmenting a SKU further.
    """
    singles, pallets, by_location = _drain_fixture()
    quantities = [_on_hand(b) for b in by_location]
    assert quantities == sorted(quantities, reverse=True), (
        f'the fixture no longer anti-correlates on-hand with location: {quantities}')

    out: defaultdict = defaultdict(int)
    remaining = drain_sku(singles, pallets, 9, out)

    assert remaining == 0, f'{remaining} items of demand unmet from 24 on hand'
    assert dict(out) == {pallets[1]: 3, pallets[0]: 5, singles[1]: 1}, (
        f'drained {[(b.location, n) for b, n in out.items()]}; smallest-first takes '
        f'{pallets[1].location} (3) then {pallets[0].location} (5) then one from '
        f'{singles[1].location}')
    assert singles[0] not in out, (
        f'the fullest bin {singles[0].location} was drained; under the retired '
        f'singleton-first rule it would have gone FIRST')


def test_drain_sku_breaks_a_quantity_tie_by_location():
    """`(on-hand, location)` — the tie-break is what keeps the choice a pure function of
    warehouse state rather than of whatever order the manager's identity-hashed sets yield.
    """
    singles, pallets, _ = _drain_fixture()
    # Make the two pallet bins tie at 3; the lower location must go first.
    pallets[0].storage.quantity = 3
    out: defaultdict = defaultdict(int)
    remaining = drain_sku(singles, pallets, 3, out)
    assert remaining == 0
    assert dict(out) == {pallets[0]: 3}, (
        f'the tie broke to {[(b.location, n) for b, n in out.items()]}, not the lower '
        f'location {pallets[0].location}')


def test_drain_sku_is_invariant_to_the_order_the_index_yields():
    """The manager's indexes are `set[Aisle.Bin]`, which iterate in MEMORY-ADDRESS order.

    Under spawn + ASLR that differs per process, so a rule that inherited it made two
    identical-seed runs drain different bins.  Twelve orders — the canonical one, its exact
    reverse, and ten seeded shuffles — must all produce one answer.
    """
    singles, pallets, by_location = _drain_fixture()
    canonical = sorted(by_location, key=lambda b: (_on_hand(b), b.location))
    orders = [list(by_location), list(reversed(by_location)), canonical,
              list(reversed(canonical))]
    rng = random.Random(99)
    for _ in range(8):
        shuffled = list(by_location)
        rng.shuffle(shuffled)
        orders.append(shuffled)

    answers = set()
    for order_ in orders:
        out: defaultdict = defaultdict(int)
        drain_sku([], order_, 11, out)
        answers.add(tuple(sorted((b.location, n) for b, n in out.items())))
    assert len(answers) == 1, (
        f'{len(answers)} distinct drains across {len(orders)} iteration orders: {answers}')
    expected = tuple(sorted([(pallets[1].location, 3), (pallets[0].location, 5),
                             (singles[1].location, 3)]))
    assert answers.pop() == expected, (
        f'the one answer is not smallest-first; expected {expected}')


def test_drain_sku_reports_the_demand_no_bin_could_satisfy():
    """The return value is PRE-SIMULATION shortfall: demand no bin could reach, which is a
    different failure from a picker running out of time and has a different fix."""
    singles, pallets, _ = _drain_fixture()
    out: defaultdict = defaultdict(int)
    on_hand = sum(_on_hand(b) for b in (*singles, *pallets))
    remaining = drain_sku(singles, pallets, on_hand + 7, out)
    assert remaining == 7, f'shortfall reported {remaining}, expected 7'
    assert sum(out.values()) == on_hand, 'the drain left items on the shelf it could have taken'


# ══════════════════════════════════════════════════════════════════════════════
# Conservation — nothing is created or destroyed by either path
# ══════════════════════════════════════════════════════════════════════════════

def test_a_top_up_conserves_every_item():
    """Items offered == items on shelves + items still upstream of a bin.

    The own-bin rung is the fifth bin-mutation site and the only one that ADDS to an occupied
    bin, so it is the one place a `+=` could double-count or a remainder could be dropped —
    and the run-time conservation ledger is bin-only, which cannot see a unit lost before it
    reaches a bin.
    """
    wh, mgr, order, med = _stocked([8, 10, 10, 12], small_aisle=False)
    start = _shelf_items(wh) + _queued_items(mgr)
    _offer(mgr, order, 12)
    assert mgr._put_topups >= 1, 'the rung did not fire — this conserves the wrong path'
    end = _shelf_items(wh) + _queued_items(mgr)
    assert end == start + 12, (
        f'{start} + 12 offered != {end} accounted for '
        f'(shelves {_shelf_items(wh)}, waiting {_queued_items(mgr)})')
    assert mgr._current_quantities[order.sku] == _shelf_items(wh), (
        f"the manager's incremental on-hand ({mgr._current_quantities[order.sku]}) drifted "
        f'from the shelves ({_shelf_items(wh)})')


def test_a_rescue_conserves_every_item():
    """The same ledger across the other branch: a rescue SPLITS a unit, so the unit count
    changes and the item count must not."""
    wh, mgr, order, med = _stocked([12, 12, 12, 12])
    mgr.enable_receiving(_dock())
    start = _shelf_items(wh) + _queued_items(mgr)
    _offer(mgr, order, 12)
    assert mgr._recv_repacks == 1, 'the rescue did not fire — this conserves the wrong path'
    end = _shelf_items(wh) + _queued_items(mgr)
    assert end == start + 12, (
        f'{start} + 12 offered != {end} accounted for '
        f'(shelves {_shelf_items(wh)}, waiting {_queued_items(mgr)})')
    assert mgr._current_quantities[order.sku] == _shelf_items(wh), (
        f"the manager's incremental on-hand ({mgr._current_quantities[order.sku]}) drifted "
        f'from the shelves ({_shelf_items(wh)})')


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))
