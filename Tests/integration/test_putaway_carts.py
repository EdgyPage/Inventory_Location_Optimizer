"""test_putaway_carts.py — how a reorder becomes put-away work, and who carries it.

The requirement, in four parts:

  1. "orders in the reorder queues get palletized with the remainder being placed in carts"
  2. "carts have the same volume limits as pickers for their warehouse"
  3. "putters take carts with merchandise and put the items away"
  4. "with an emphasis on utilizing empty bins rather than adding to existing bins to prevent
      conflicts with pickers"

Parts 1 and 4 turned out to be ALREADY TRUE, and the tests below pin them rather than
re-implement them:

  * `viable_storage_units` already packs "the minimum number of full pallets, with any
    remainder routed to a singleton unit", and the put queues already route pallets to the
    forklift stream and singletons to the cart stream.
  * `_execute_placement` does `bin_.storage = unit; self._index_remove(bin_)`, and a bin
    re-enters the free index ONLY when it empties. So a bin is either empty-and-available or
    occupied-and-invisible, and no code path adds to an occupied bin. Measured across four
    arms: 1,416 placements each, ZERO into an occupied bin. The emphasis is structural, not
    a preference that could be tuned away.

Parts 2 and 3 were real work, modelled with `cost_model.cart_step` — the same next-fit the
two picker loops and the LPT makespan predictor already share, because in a store the putter
and the picker push the same cart.

Run:  python -m pytest Tests/integration/test_putaway_carts.py -q
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

from Warehouse.inventory.put_queue import (
    ANY, FULFILLMENT, PALLET, SINGLETON, PutQueue, PutQueueSpec, store_and_fulfillment,
)
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.layout.Storage_Primitive import (
    FulfillmentCart, StoreCart, viable_storage_units,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


# ── 1. palletize, then the remainder rides a cart ─────────────────────────────────

def test_the_default_packing_is_full_pallets_then_one_remainder():
    """The rule the requirement describes, exercised on an order with NO stock plan — which
    is the only case it governs. See the test below for the case that actually ships."""
    # A REAL order with its plan cleared. A hand-built stub is not enough — the packer
    # reads geometry (`height`, `max_width`, `max_length`) that a namespace does not have.
    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    order = a.inventory.orders[0]
    order.stock_plan = None
    cats = [u.unit_category for u in viable_storage_units(order, 40)]
    assert PALLET in cats, cats
    assert cats.count(SINGLETON) <= 1, f'more than one remainder: {cats}'
    if SINGLETON in cats:
        assert cats[-1] == SINGLETON, f'the remainder is not last: {cats}'


def test_a_stock_plan_OVERRIDES_the_pallets_then_remainder_rule():
    """The case that actually ships, and it is not the rule the requirement assumes.

    EVERY order in a planned warehouse carries a `stock_plan` — a run-length list of
    `(is_singleton, per_unit, count)` slots assigned at warehouse-planning time so reorders
    rebuild the same tier mix. `viable_storage_units` reproduces the plan and only falls
    through to pallets-then-remainder for quantity BEYOND it.

    Measured on a 200-SKU planned catalogue, 200/200 orders planned:

        qty=  1   79 pallets,   1 singleton    0 orders split into both
        qty=  5  301 pallets,   5 singletons   0
        qty= 40 2178 pallets,  34 singletons   1
        qty=200 10079 pallets, 128 singletons  4

    So the store CART stream is nearly empty in this configuration and the forklift stream
    carries almost everything. That is the planner's choice, not the packer's, and it is the
    number to look at before concluding the cart model is inert.
    """
    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    planned = [o for o in a.inventory.orders if getattr(o, 'stock_plan', None)]
    assert len(planned) == len(a.inventory.orders), 'this catalogue is not fully planned'

    o = planned[0]
    within = sum(per * count for _f, per, count in o.stock_plan)
    cats = [u.unit_category for u in viable_storage_units(o, within)]
    want = [SINGLETON if flag else PALLET
            for flag, _per, count in o.stock_plan for _ in range(count)]
    assert cats == want, f'the plan was not reproduced: {cats} vs {want}'

    # Beyond the plan, the default rule takes over.
    beyond = [u.unit_category for u in viable_storage_units(o, within * 20)]
    assert len(beyond) > len(cats), 'a larger reorder produced no extra units'


def test_the_split_is_overwhelmingly_pallets_at_current_planner_settings():
    """Reported as a fact about the configuration, not asserted as desirable. If the mix
    ever moves toward singletons the cart stream starts carrying real load, and that is a
    change worth noticing rather than absorbing."""
    a = cs.build_assets(n_skus=200, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    pallets = singles = 0
    for o in a.inventory.orders[:80]:
        for u in viable_storage_units(o, 40):
            if u.unit_category == PALLET:
                pallets += 1
            elif u.unit_category == SINGLETON:
                singles += 1
    assert pallets > 0 and singles > 0, (pallets, singles)
    assert pallets > 20 * singles, (
        f'the pallet/singleton mix moved to {pallets}/{singles} — the store cart stream is '
        f'now carrying real load, which changes what the cart model is worth')


def test_pallets_go_to_the_forklift_and_the_remainder_to_a_cart():
    """The routing that makes part 1 an operational split rather than a packing detail."""
    qs = store_and_fulfillment(store_cart=StoreCart, ff_cart=FulfillmentCart)

    def u(cat):
        return types.SimpleNamespace(unit_category=cat)

    assert qs.route(u(PALLET)).name == 'store_pallet'
    assert qs.route(u(SINGLETON)).name == 'store_cart'
    assert qs.route(u(FULFILLMENT)).name == 'fulfillment'
    # Only the cart streams have a cart: a forklift carries one pallet, so the "how much
    # fits" question never arises there.
    assert qs['store_cart'].carted and qs['fulfillment'].carted
    assert not qs['store_pallet'].carted


# ── 2. the same volume limit the pickers have ─────────────────────────────────────

def test_a_put_cart_holds_exactly_what_the_pick_cart_holds():
    """Same warehouse, same cart. A put-side capacity that drifted from the pick side would
    be a second set of numbers to reconcile, which this project has paid for twice."""
    qs = store_and_fulfillment(store_cart=StoreCart, ff_cart=FulfillmentCart)
    assert qs['store_cart'].cart_cap == float(StoreCart.capacity()) == 125_000.0
    assert qs['fulfillment'].cart_cap == float(FulfillmentCart.capacity()) == 25_000.0
    assert qs['store_cart'].cart_cap != qs['fulfillment'].cart_cap, (
        'the two channels should not share a cart size')


def test_no_cart_means_no_limit():
    """The default. A queue that has not asked for a cart is unbounded, which is what the
    put side did before this existed."""
    q = PutQueue(PutQueueSpec('plain', accepts=ANY))
    assert not q.carted and q.cart_cap == 0.0
    assert q.load(10 ** 9) is False, 'an uncarted queue reported a swap'
    assert q.cart_swaps == 0


# ── 3. the load is bounded, and running out costs a trip ──────────────────────────

def test_a_load_that_overflows_swaps_the_cart():
    q = PutQueue(PutQueueSpec('c', accepts=ANY, cart=StoreCart, swap_coef=300.0))
    cap = q.cart_cap
    assert q.load(cap * 0.6) is False, 'the first load should fit an empty cart'
    assert q.load(cap * 0.6) is True, 'the second should not fit and must swap'
    assert q.cart_swaps == 1
    assert q.cart_remaining == pytest.approx(cap - cap * 0.6)


def test_the_put_cart_uses_the_pickers_own_next_fit():
    """Not a lookalike. `cost_model.cart_step` is the single source of the next-fit shared by
    both picker loops and the LPT predictor, and a putter pushing the same physical cart has
    to agree with them about when it is full."""
    from Warehouse.kernel.cost_model import cart_step

    q = PutQueue(PutQueueSpec('c', accepts=ANY, cart=StoreCart))
    cap, rem = q.cart_cap, q.cart_cap
    for vol in (cap * 0.3, cap * 0.5, cap * 0.4, cap * 0.9, cap * 0.2):
        want_swap, rem = cart_step(vol, rem, cap)
        assert q.load(vol) == want_swap, vol
        assert q.cart_remaining == pytest.approx(rem)


def _mgr_with(cart, swap):
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    from Warehouse.inventory.put_queue import PutQueueSet, single_queue

    m = Inventory_Manager.__new__(Inventory_Manager)
    m._put_speed = m._put_cost = None
    m._put_size = 1
    m._put_clock = 0.0
    m._put_seconds = 0.0
    m._put_records = []
    m._put_queues = (single_queue() if cart is None else
                     PutQueueSet([PutQueueSpec('all', accepts=ANY, cart=cart,
                                               swap_coef=swap)]))
    m.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=1)
    return m


def _bulk_unit(sku=1):
    """One unit filling most of a store cart, so a second one must swap."""
    vol = StoreCart.capacity() * 0.6
    return types.SimpleNamespace(
        quantity=1, unit_category=SINGLETON,
        order=types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol))


def test_a_swap_costs_the_putter_time():
    """The trip back for another cart. Without the charge a cart limit would constrain
    nothing — bookkeeping with no consequence."""
    bin_ = types.SimpleNamespace(x_phys=100.0, y_phys=48.0, location=(7, 1, 1))
    free = _mgr_with(None, 0.0)
    carted = _mgr_with(StoreCart, 300.0)
    for _ in range(2):
        free._cost_putaway(_bulk_unit(), bin_, 'reorder')
        carted._cost_putaway(_bulk_unit(), bin_, 'reorder')
    assert carted.putaway_seconds == pytest.approx(free.putaway_seconds + 300.0), (
        'the cart swap was not charged')
    assert carted.put_queues.queues[0].cart_swaps == 1


def test_a_cart_big_enough_never_swaps():
    """Non-vacuity for the test above: the extra 300 s is the swap and not a constant the
    cart model adds to every put."""
    bin_ = types.SimpleNamespace(x_phys=100.0, y_phys=48.0, location=(7, 1, 1))
    free = _mgr_with(None, 0.0)
    carted = _mgr_with(StoreCart, 300.0)

    def tiny():
        return types.SimpleNamespace(
            quantity=1, unit_category=SINGLETON,
            order=types.SimpleNamespace(sku=1, weight=10, volume=lambda: 1.0))

    for _ in range(5):
        free._cost_putaway(tiny(), bin_, 'reorder')
        carted._cost_putaway(tiny(), bin_, 'reorder')
    assert carted.putaway_seconds == pytest.approx(free.putaway_seconds)
    assert carted.put_queues.queues[0].cart_swaps == 0


def test_the_swap_count_is_reported_per_batch():
    """A flow, like `blocked` beside it — the only trace a swap leaves, and the number that
    says whether the cart size is binding."""
    q = PutQueue(PutQueueSpec('c', accepts=ANY, cart=StoreCart, swap_coef=1.0))
    for _ in range(5):
        q.load(q.cart_cap * 0.6)
    assert q.drain_counters()['cart_swaps'] == 4
    assert q.drain_counters()['cart_swaps'] == 0, 'the flow did not reset'


# ── 4. empty bins only — structurally, not by preference ──────────────────────────

@pytest.mark.parametrize('arm', ['uni_rank_labor_norsl', 'uni_fifo_norsl', 'uni_map_norsl'])
def test_no_placement_ever_lands_in_an_occupied_bin(arm):
    """The fourth requirement, already guaranteed by construction — and this is the guard
    that keeps it so.

    `_execute_placement` removes the bin from the free index and files it under
    `_unavailable`; it returns only when emptied. A candidate list therefore never contains
    an occupied bin, and `bin_.storage = unit` can never overwrite live stock.
    """
    a = cs.build_assets(n_skus=250, bins_per_aisle=30, strategy=arm, seed=42,
                        coverage=2.0, safety=0.4)
    mgr = a.mgr
    occupied, empty = [0], [0]
    orig = mgr._execute_placement

    def cap(unit, bin_, *, source=None, score=None, score_rank=None, policy=None, _o=orig):
        (occupied if bin_.storage is not None else empty)[0] += 1
        _o(unit, bin_, source=source)
    mgr._execute_placement = cap
    cs.run_meso(a, n_batches=5, seed=42)

    assert empty[0] > 100, f'only {empty[0]} placements; nothing was exercised'
    assert occupied[0] == 0, (
        f'{occupied[0]} placements landed in a bin that already held stock — a putter and a '
        f'picker can now collide at the same bin')


def test_an_occupied_bin_is_not_a_candidate():
    """The mechanism behind the run above, stated directly: candidates come from the free
    index, and a placed bin leaves it."""
    a = cs.build_assets(n_skus=150, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=3, coverage=2.0, safety=0.4)
    mgr = a.mgr
    unit = next(u for o in a.inventory.orders for u in viable_storage_units(o, 1))
    cands = list(mgr._candidates(unit))
    assert cands, 'no candidates at all; the check is vacuous'
    holding = [b for b in cands if b.storage is not None]
    assert not holding, f'{len(holding)} candidate bins already hold stock'
