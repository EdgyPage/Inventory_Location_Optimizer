"""test_aisle_vol_sum_teardown.py — the cart-volume sum comes back down.

`_aisle_vol_sum[aid]` is the expected picked-volume mass of an aisle's contents, seeded once
per arm by `init_demand_state` and read in the `rank_cartlabor` scoring expression:

    score += _cart_cost(vol_load[aid] + add)          # _TravelBalancedPool._score_of

The argmin over `score` picks both the aisle AND the bin, so this is a term that decides
placement, not one that merely reports it.

It was incremented on commit and **decremented nowhere**.  `_drop_sku_from_aisle` subtracted
the lift, demand and pick-load contributions of a SKU's last bin and silently skipped the
volume one; so did the hoisted inline twin in `_reclaim_empty_bins`.  Over a run the term
therefore only grew, and because `_cart_cost` clamps at `max(0.0, v / cap_raw - 1.0)` the
drift stayed invisible until an aisle crossed cart capacity — "inert for the big store cart,
bites for the small fulfillment cart".  No test covered either teardown path at all.

Every test below asserts the MAGNITUDE of the decrement, never merely that the value moved:
a guard that a zero satisfies is how this class of defect survives a green suite.

    python -m pytest Tests/unit/test_aisle_vol_sum_teardown.py -q
"""
from __future__ import annotations

import random
from typing import Any

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Inventory_Builder import Inventory
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Optimization.metrics.Workload import WorkloadParams

# ── fixture ───────────────────────────────────────────────────────────────────
#
# The same tiny two-aisle warehouse `test_placement_lifecycle` uses, for the same reason:
# small enough that the volume arithmetic is checkable by hand.

_W, _H = 5 * 48, 4 * 48
_AISLE_CFGS = [
    AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['small'], None),
    AisleConfig('conveyable', 'food', 'singleton', _W, _H, ['small', 'medium'], [0.5, 0.5]),
]
_WH_CFG = WarehouseConfig(total_aisles=2, aisle_splits=[0.5, 0.5], aisle_configs=_AISLE_CFGS)

_TOL = 1e-9     # floats compare with a tolerance, never ==


def _make_carton(sku: int, stock_qty: int = 30) -> Order:
    """An Order with known dimensions — no DB, no profile generation.

    `labor_cost` is set explicitly because `init_demand_state(inventory, wp)` also seeds the
    labor twin through `expected_labor`, which reads it; `object.__new__` skips the
    `__init__` that would otherwise default it.
    """
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length  = 8
    c.width   = 8
    c.height  = 6
    c.weight  = 5
    c.demand                = Demand.from_rates(0.9, 3.0)
    c.lead_time_mean        = 0.0
    c.expected_batch_demand = 0.9 * 3.0
    c.labor_cost            = 1.0
    return c.declare_stock(stock_qty, max(1, stock_qty // 2))


def _armed_manager(seed: int = 42, with_workload: bool = True):
    """A manager with affinity on and the demand/volume state seeded.

    `with_workload=False` is the flag-off pole: `init_demand_state` without a `wp` leaves
    `_sku_vol_product` empty, which is the path every non-cart arm takes.
    """
    Aisle.next_aisle_id = 1         # class counter — reset or aisle ids leak between tests
    random.seed(seed)               # Warehouse_Builder draws from the module-level random
    wh        = Warehouse_Builder().from_config(_WH_CFG).build()
    affinity  = AffinityStore(':memory:')
    mgr       = Inventory_Manager(wh, affinity=affinity)
    inventory = Inventory([_make_carton(sku=i) for i in range(1, 6)])

    random.seed(seed + 1)
    mgr.enqueue_all(inventory.orders)
    mgr.init_placement_state(affinity)
    mgr.init_demand_state(inventory, WorkloadParams() if with_workload else None)
    return mgr, inventory


def _bins_by_aisle(mgr, sku: int) -> dict[int, list]:
    """The SKU's occupied bins, grouped by the aisle that holds them."""
    out: dict[int, list] = {}
    for b in mgr.unavailable:
        if b.storage and b.storage.order.sku == sku:
            out.setdefault(b.location[0], []).append(b)
    return out


def _a_sku_with_bins(mgr, inventory) -> tuple[int, int, list]:
    """Pick a (sku, aisle, bins) triple that actually has mass to subtract."""
    for c in inventory.orders:
        for aid, bins in _bins_by_aisle(mgr, c.sku).items():
            if mgr._sku_vol_product.get(c.sku, 0.0) > 0.0:
                return c.sku, aid, bins
    raise AssertionError('no SKU carries volume mass — the fixture seeded nothing')


# ═════════════════════════════════════════════════════════════════════════════
# The seed itself — without this the two teardown tests below prove nothing
# ═════════════════════════════════════════════════════════════════════════════

def test_the_fixture_actually_seeds_volume_mass():
    """Non-vacuity guard.

    Both teardown tests assert that a sum FALLS by a known amount.  If the fixture seeded
    zeros, `0.0 - 0.0 == 0.0` would satisfy them forever.  Pin the seed instead.
    """
    mgr, inventory = _armed_manager()

    assert mgr._sku_vol_product, '_sku_vol_product is empty — init_demand_state seeded nothing'
    assert any(v > 0.0 for v in mgr._aisle_vol_sum.values()), (
        '_aisle_vol_sum is all zeros — nothing was placed, so a decrement cannot be observed')

    # f * q * volume, by hand: 0.9 * 3.0 * (8 * 8 * 6)
    expected = 0.9 * 3.0 * (8 * 8 * 6)
    for c in inventory.orders:
        assert abs(mgr._sku_vol_product[c.sku] - expected) < _TOL, (
            f'sku={c.sku}: _sku_vol_product is {mgr._sku_vol_product[c.sku]}, expected {expected}')

    # And each aisle's sum is exactly the mass of the SKUs it holds.
    for aid, sku_set in mgr._aisle_sku_sets.items():
        by_hand = sum(mgr._sku_vol_product.get(s, 0.0) for s in sku_set)
        assert abs(mgr._aisle_vol_sum[aid] - by_hand) < _TOL, (
            f'aisle={aid}: _aisle_vol_sum is {mgr._aisle_vol_sum[aid]}, members sum to {by_hand}')


# ═════════════════════════════════════════════════════════════════════════════
# The cold path — _drop_sku_from_aisle, the canonical teardown
# ═════════════════════════════════════════════════════════════════════════════

def test_drop_sku_from_aisle_subtracts_the_cart_volume():
    """Dropping a SKU's LAST bin in an aisle removes exactly its volume mass.

    Dropping an earlier bin must NOT: while the SKU still has a bin in the aisle its mass is
    still on that aisle's shelf, and subtracting early would send the term negative.
    """
    mgr, inventory = _armed_manager()
    sku, aid, bins = _a_sku_with_bins(mgr, inventory)
    mass  = mgr._sku_vol_product[sku]
    start = mgr._aisle_vol_sum[aid]

    # Every bin but the last: the SKU is still resident, so the sum must not move.
    for b in bins[:-1]:
        mgr._drop_sku_from_aisle(sku, b)
        assert abs(mgr._aisle_vol_sum[aid] - start) < _TOL, (
            f'aisle={aid}: the sum moved while sku={sku} still holds '
            f'{mgr._aisle_sku_counts[aid].get(sku)} bin(s) there')

    # The last one: exactly `mass` comes off, and the SKU leaves the member set.
    mgr._drop_sku_from_aisle(sku, bins[-1])
    assert abs(mgr._aisle_vol_sum[aid] - (start - mass)) < _TOL, (
        f'aisle={aid}: _aisle_vol_sum is {mgr._aisle_vol_sum[aid]}, '
        f'expected {start - mass} ({start} minus sku={sku} mass {mass})')
    assert sku not in mgr._aisle_sku_sets[aid]

    # The invariant the whole ledger is supposed to hold, now assertable for this aisle.
    by_hand = sum(mgr._sku_vol_product.get(s, 0.0) for s in mgr._aisle_sku_sets[aid])
    assert abs(mgr._aisle_vol_sum[aid] - by_hand) < _TOL, (
        f'aisle={aid}: sum {mgr._aisle_vol_sum[aid]} disagrees with its members {by_hand}')


# ═════════════════════════════════════════════════════════════════════════════
# The hot path — the hoisted INLINE TWIN inside _reclaim_empty_bins
# ═════════════════════════════════════════════════════════════════════════════

def test_reclaim_empty_bins_subtracts_the_cart_volume():
    """The twin must subtract what the canonical version subtracts.

    `_drop_sku_from_aisle`'s docstring says KEEP THE TWO IN SYNC.  Nothing checked it, and
    they had been out of sync on this term for the life of the feature.
    """
    mgr, inventory = _armed_manager()
    sku, aid, bins = _a_sku_with_bins(mgr, inventory)
    mass  = mgr._sku_vol_product[sku]
    start = mgr._aisle_vol_sum[aid]

    mgr._pending_reclaim = list(bins)
    mgr._reclaim_empty_bins()

    assert abs(mgr._aisle_vol_sum[aid] - (start - mass)) < _TOL, (
        f'aisle={aid}: _aisle_vol_sum is {mgr._aisle_vol_sum[aid]}, '
        f'expected {start - mass} after reclaiming all {len(bins)} of sku={sku} bins')
    assert sku not in mgr._aisle_sku_sets[aid]


def test_the_two_teardown_paths_agree():
    """Same drop, both routes, same resulting sum — the sync the docstring asks for."""
    mgr_a, inv_a = _armed_manager()
    sku, aid, bins_a = _a_sku_with_bins(mgr_a, inv_a)
    for b in bins_a:
        mgr_a._drop_sku_from_aisle(sku, b)

    mgr_b, inv_b = _armed_manager()
    sku_b, aid_b, bins_b = _a_sku_with_bins(mgr_b, inv_b)
    assert (sku_b, aid_b) == (sku, aid), 'the two fixtures diverged — seeds are not aligned'
    mgr_b._pending_reclaim = list(bins_b)
    mgr_b._reclaim_empty_bins()

    assert abs(mgr_a._aisle_vol_sum[aid] - mgr_b._aisle_vol_sum[aid_b]) < _TOL, (
        f'the twins disagree: cold path {mgr_a._aisle_vol_sum[aid]}, '
        f'hot path {mgr_b._aisle_vol_sum[aid_b]}')


# ═════════════════════════════════════════════════════════════════════════════
# The flag-off pole — every arm that is not cart-aware must be untouched
# ═════════════════════════════════════════════════════════════════════════════

def test_without_a_workload_the_teardown_is_a_no_op():
    """`init_demand_state` without a `wp` leaves `_sku_vol_product` empty.

    That is the path every non-`rank_cartlabor` arm takes, so the decrement added here must
    be a strict no-op on it — `.get(sku, 0.0)` returns 0.0 and the guard never fires.  This
    is the byte-identity claim the change rests on.
    """
    mgr, inventory = _armed_manager(with_workload=False)
    assert not mgr._sku_vol_product, (
        '_sku_vol_product should be empty without a workload — the no-op claim does not hold')

    sku  = inventory.orders[0].sku
    for aid, bins in _bins_by_aisle(mgr, sku).items():
        before = dict(mgr._aisle_vol_sum)
        for b in bins:
            mgr._drop_sku_from_aisle(sku, b)
        assert dict(mgr._aisle_vol_sum) == before, (
            '_aisle_vol_sum moved on the flag-off path, which must be byte-identical')
        break
