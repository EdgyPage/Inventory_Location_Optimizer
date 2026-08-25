"""test_inbound_load_plan.py — packing is a property of the ARRIVAL, not of the order.

`viable_storage_units(order, quantity)` has always taken a quantity, so what a shipment packs
into has always depended on how much of it turns up at once. Nothing named that, so nothing
could express the case that motivates this module:

    an order that WOULD be palletized if it came in all at once, but instead results in
    two singleton packs due to trailer load circumstance

`Warehouse/operations/inbound.py` is that name. It wraps the packer **unchanged** -- the whole
point of the seam is that no packing behaviour moves -- and adds the record: what arrived, what
it became, and what it would have become arriving whole.

Pinned here:

  1. the motivating case is REAL, not illustrative: at volume 800 x 6, whole packs one pallet
     and split-in-two packs two singletons;
  2. the counterfactual is signed -- splitting can REMOVE units as well as add them, so a
     caller that assumes "split is worse" is wrong and the type must let it find out;
  3. merchandise is conserved across a split, which is the invariant a trailer model will
     lean on;
  4. the seam is REACHABLE from a real arrival through `inbound_split`, not just from a
     parameter no caller passes;
  5. absent a split policy, an arrival is byte-identical to calling the packer directly --
     the no-op guarantee this repo requires of every new feature.

Run:  python -m pytest Tests/unit/test_inbound_load_plan.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.operations import inbound


# ── fixtures ──────────────────────────────────────────────────────────────────────

def _order(sku: int = 1, length: int = 10, width: int = 10, height: int = 8,
           eq_qty: int = 20, rp: int = 10) -> Order:
    """An Order with hand-set dimensions -- no DB, no profile generation.

    The default 10 x 10 x 8 = 800 is not arbitrary: it is the volume at which the motivating
    case exists. `small_slot_vol` is 3072, so six of these (4800) overflow the singleton slot
    and pack a pallet, while three (2400) fit one and pack a singleton.
    """
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length, c.width, c.height = length, width, height
    c.weight = 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty       = eq_qty
    c.reorder_point         = rp
    c.lead_time_mean        = 0.0
    c.supply_cv             = 0.0
    c.expected_batch_demand = 3.2
    return c


def _warehouse():
    """Two aisle families, enough to hold both a pallet and a singleton."""
    Aisle.next_aisle_id = 1     # class counter -- reset or aisle ids leak between tests
    random.seed(0)              # Warehouse_Builder draws from the module-level random
    w, h = aisle_width_for(4), aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Warehouse_Builder().from_config(cfg).build()


def _mix(units):
    out: dict = {}
    for u in units:
        out[u.unit_category] = out.get(u.unit_category, 0) + 1
    return out


# ── 1. the motivating case is real ────────────────────────────────────────────────

def test_pallet_whole_becomes_two_singletons_when_split():
    """THE case, exactly as stated: one pallet whole, two singleton packs split in two.

    Asserted on the tier mix rather than the unit count, because "two singletons instead of a
    pallet" and "two units instead of one" are different claims and only the first is the
    one that motivates the feature -- a split that produced two pallets would be a different
    (and much less interesting) story about the same arithmetic.
    """
    o = _order()
    assert o.volume() == 800

    whole = inbound.receive(o, 6)
    assert whole.tier_mix == {inbound.PALLET: 1}
    assert not whole.was_split

    split = inbound.receive_all(o, [3, 3])
    assert [p.tier_mix for p in split] == [{inbound.SINGLETON: 1}, {inbound.SINGLETON: 1}]
    assert all(p.was_split for p in split)
    assert [(p.index, p.of) for p in split] == [(0, 2), (1, 2)]


def test_the_split_is_what_changed_it_not_the_quantity():
    """Control: the same six units arriving whole are a pallet, so the tier change is caused
    by the SPLIT and not by anything about six.

    Without this, `test_pallet_whole_becomes_two_singletons_when_split` is equally consistent
    with the packer simply disliking that order -- and the counterfactual is the whole product
    of the module, so it has to be the thing under test rather than the thing assumed.
    """
    o = _order()
    split = inbound.receive_all(o, [3, 3])
    for p in split:
        assert p.whole_qty == 6
        assert p.unsplit().tier_mix == {inbound.PALLET: 1}
    assert inbound.shipment_penalty(split) == 1      # 2 singletons for 1 pallet


# ── 2. the counterfactual is signed ───────────────────────────────────────────────

def test_splitting_never_helps_an_unplanned_order():
    """Default packing is fills-pallets-then-a-remainder, and that is monotone: cutting a
    shipment up can only ever produce the same units or more.

    Worth pinning as the CONTROL for the next test. Without it, "splitting can help" reads as
    a property of splitting; with it, the effect is located where it actually lives -- in the
    stock plan -- and a future change that made the default packer non-monotone would be
    caught here rather than quietly widening the claim.
    """
    for height in (1, 2, 3, 4, 6, 8, 12):
        o = _order(length=10, width=10, height=height)
        for qty in range(2, 200):
            plans = inbound.receive_all(o, [qty // 2, qty - qty // 2])
            assert inbound.shipment_penalty(plans) >= 0, (
                f'vol={o.volume()} qty={qty} packs into FEWER units when split -- the '
                f'default packer is no longer monotone and the control below is void')


def test_splitting_a_PLANNED_order_can_remove_units():
    """A planned SKU packs FEWER units split than whole, and that is the case that stops
    `split_penalty` from being quietly reinterpreted as a count of regret.

    `stock_plan` overrides the packer entirely: the plan is a run-length list of
    `(is_singleton, per, count)` slots, filled from the front until the arrival is exhausted.
    A whole arrival that runs off the end of the pallet slots spills into the plan's singleton
    tail; each HALF of the same arrival stops before that tail, so both halves stay on
    pallets. Measured on the real catalogue this is not marginal -- one planned SKU went from
    34 pallets + 26 singletons whole to 40 pallets and no singletons split.

    So a downstream model that clamped the penalty at zero would be discarding a real effect,
    and the signed return is load-bearing.
    """
    o = _order(length=10, width=10, height=4)
    #: The sku-21 shape: a long pallet run followed by a singleton tail.
    o.stock_plan = [(False, 4, 34), (True, 1, 26)]

    whole = inbound.receive(o, 200)
    split = inbound.receive_all(o, [100, 100])
    assert whole.tier_mix == {inbound.PALLET: 38, inbound.SINGLETON: 26}
    assert [p.tier_mix for p in split] == [{inbound.PALLET: 25}, {inbound.PALLET: 25}]
    assert sum(p.unit_count for p in split) < whole.unit_count
    assert inbound.shipment_penalty(split) == -14

    # ...and the SAME order splits the other way at a quantity that fits the pallet run:
    # the direction is a property of the arrival, not of the SKU.
    assert inbound.shipment_penalty(inbound.receive_all(o, [50, 50])) == 1


def test_an_unsplit_delivery_compares_against_itself():
    """`unsplit()` is answerable on a delivery that was never split, so a caller can compare
    unconditionally instead of branching on `was_split`."""
    o = _order()
    p = inbound.receive(o, 6)
    assert p.whole_qty == p.received == 6
    assert p.split_penalty() == 0
    assert inbound.shipment_penalty([p]) == 0


# ── 3. merchandise is conserved ───────────────────────────────────────────────────

@pytest.mark.parametrize('deliveries', [[3, 3], [1, 5], [2, 2, 2], [6], [4, 1, 1]])
def test_a_split_moves_the_same_merchandise(deliveries):
    """However a shipment is cut up, the same number of ITEMS arrive.

    The invariant a trailer model leans on: it may reshape the packing freely, but it must
    not create or destroy merchandise. Checked across several cuts because the packing tiers
    differ between them and the conservation must not.
    """
    o = _order()
    plans = inbound.receive_all(o, deliveries)
    assert sum(p.received for p in plans) == 6
    assert sum(p.packed_qty for p in plans) == 6
    assert all(p.whole_qty == 6 for p in plans)


def test_zero_and_empty_deliveries_are_dropped_not_counted():
    """A trailer that arrives empty is not a delivery. `of` counts real ones, so the split
    ratio a consumer reads is not inflated by padding in the caller's list."""
    assert inbound.receive_all(_order(), []) == []
    assert inbound.shipment_penalty([]) == 0
    plans = inbound.receive_all(_order(), [3, 0, 3])
    assert [(p.index, p.of) for p in plans] == [(0, 2), (1, 2)]


# ── 4. the seam is reachable from a real arrival ──────────────────────────────────

def _received_units(mgr):
    return [item.unit for item in mgr._stock_queue]


def test_inbound_split_reshapes_a_real_reorder_arrival():
    """The end-to-end claim: a lead-queue arrival routed through a split policy lands in the
    stock queue as the SPLIT packing.

    Exercised through `check_reorders` rather than by calling `_release_to_stock` directly,
    because the parameter existing is not the same as the parameter being reachable -- and
    `_release_arrivals`, the only real caller, does not pass one. `inbound_split` is what
    closes that gap, and this is the test that would fail if it were removed.
    """
    mgr = Inventory_Manager(_warehouse())
    o = _order(sku=101)
    mgr._originals[101] = o
    mgr._current_quantities[101] = 0

    # Halve every arrival -- the trailer circumstance, standing in for a load planner.
    mgr.inbound_split = lambda sku, qty: [qty // 2, qty - qty // 2]

    plans = mgr._release_to_stock(101, 6)
    assert _mix(_received_units(mgr)) == {inbound.SINGLETON: 2}
    assert [p.tier_mix for p in plans] == [{inbound.SINGLETON: 1}, {inbound.SINGLETON: 1}]
    assert inbound.shipment_penalty(plans) == 1


def test_the_plans_are_returned_and_not_retained():
    """The manager keeps NO record of what it received, and that is deliberate.

    An earlier version stashed the plans behind a `drain_inbound()` so they would not be
    write-only -- and then nothing drained it, which made it write-only state with an extra
    method AND a leak: every arrival of the whole run pinned a tuple of live `StorageUnit`s
    plus a cloned `Order`. There is exactly one caller, so returning is the honest shape.

    Asserted on the manager's own attribute surface rather than by measuring memory: a leak
    test that watches RSS is slow, flaky, and passes while the buffer is merely small.
    """
    mgr = Inventory_Manager(_warehouse())
    mgr._originals[101] = _order(sku=101)
    assert not hasattr(mgr, '_inbound_plans'), 'the buffer is back'
    assert not hasattr(mgr, 'drain_inbound'), 'the drain with no consumer is back'

    before = set(vars(mgr))
    for _ in range(5):
        got = mgr._release_to_stock(101, 6)
        assert got, 'the caller was handed nothing, so the plans went somewhere else'
    assert set(vars(mgr)) == before, (
        'a new attribute appeared during arrivals — something is accumulating again')


def test_the_arrival_phase_returns_what_came_off_the_trucks():
    """`_release_arrivals` hands its plans up, so a receiving crew is a CALLER of an existing
    phase rather than a rewrite of one. Two arrivals in one batch must both appear, in
    arrival order."""
    mgr = Inventory_Manager(_warehouse())
    for sku in (101, 102):
        mgr._originals[sku] = _order(sku=sku)
    mgr._lead_queue = [[101, 6, 0], [102, 6, 0]]

    plans = mgr._release_arrivals()
    assert [p.sku for p in plans] == [101, 102]
    assert mgr._release_arrivals() == [], 'an empty lead queue must return a list, not None'


def test_the_trackers_count_the_split_packing():
    """`_queued_sku_counts` and `_queued_qty` must describe what actually queued.

    A split produces a different number of UNITS for the same merchandise, and both trackers
    feed the reorder position (`_fire_reorders` reads `_queued_qty`). If the unit count were
    computed from the unsplit packing, a split arrival would under- or over-state the
    position and the SKU would be re-ordered against a shipment already on the floor.
    """
    mgr = Inventory_Manager(_warehouse())
    mgr._originals[101] = _order(sku=101)
    mgr.inbound_split = lambda sku, qty: [qty // 2, qty - qty // 2]
    mgr._release_to_stock(101, 6)

    assert mgr._queued_sku_counts[101] == 2      # two singletons, not one pallet
    assert mgr._queued_qty[101] == 6             # merchandise is conserved


# ── 5. no split policy is a no-op ─────────────────────────────────────────────────

def test_no_split_policy_reproduces_the_packer_exactly():
    """The byte-identity guarantee: with `inbound_split` unset -- every run today -- an
    arrival packs exactly as a direct `viable_storage_units` call, tier for tier and quantity
    for quantity."""
    mgr = Inventory_Manager(_warehouse())
    o = _order(sku=101)
    mgr._originals[101] = o
    assert mgr.inbound_split is None, 'the default must be no split, or every run changes'

    mgr._release_to_stock(101, 6)
    got = _received_units(mgr)
    want = viable_storage_units(o.reorder(), 6)
    assert [u.unit_category for u in got] == [u.unit_category for u in want]
    assert [u.quantity for u in got] == [u.quantity for u in want]


def test_a_policy_returning_none_is_the_same_as_no_policy():
    """A load planner that declines to split THIS shipment falls back to the whole delivery
    rather than to an empty one -- the difference between "no opinion" and "nothing arrived",
    which a falsy return would collapse."""
    mgr = Inventory_Manager(_warehouse())
    mgr._originals[101] = _order(sku=101)
    mgr.inbound_split = lambda sku, qty: None
    mgr._release_to_stock(101, 6)
    assert _mix(_received_units(mgr)) == {inbound.PALLET: 1}


def test_the_packer_itself_is_untouched():
    """`inbound` wraps `viable_storage_units`; it must not have grown a private copy.

    The user's constraint was "leave the current machinery", and the failure mode is silent:
    a second packing implementation would agree on the cases anyone tested and diverge on the
    rest. Checked by identity of the produced tiers across a sweep, which a re-implementation
    would have to match exactly to pass -- at which point it is not a divergence.
    """
    o = _order()
    for qty in range(1, 60):
        assert ([u.unit_category for u in inbound.receive(o, qty).units]
                == [u.unit_category for u in viable_storage_units(o, qty)])
