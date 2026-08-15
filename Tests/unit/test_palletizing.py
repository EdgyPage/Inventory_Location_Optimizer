"""test_palletizing.py — `viable_storage_units` and the packing it drives at enqueue time.

Palletizing is the first irreversible decision in a run: it turns "SKU 42, 25 units" into a
concrete list of StorageUnits, and each unit's `unit_category` + `storage_size` fixes which
BinKey group it can ever occupy.  Get it wrong and the symptom is never an exception — it is
a bucket with no capacity, a queue that never drains, and a warehouse plan sized for the
wrong tiers.

The two rules (`Storage_Primitive.viable_storage_units`, qty=1)
---------------------------------------------------------------
  - **Singleton wins when it fits**, and a tie goes to singleton (smaller footprint).
    A singleton fits if SOME permutation of (l, w, h) satisfies w<=16, l<=16, h<=48 —
    concretely, when at most ONE dimension exceeds 16.
  - **Pallet wins only when a singleton cannot fit** — i.e. when >= 2 dimensions exceed 16,
    since no permutation can put both large dims in the height slot at once.

At quantities above one unit's capacity the packing splits: bulk onto full pallets, the
remainder into a single singleton.  That min-pallets split is what keeps a high-quantity SKU
from consuming a whole aisle of singleton bins.

    python -m pytest Tests/unit/test_palletizing.py -q

History: this file used a `check()` harness whose `fail()` body was a `print` — its 39
assertions could not fail the suite.  Tests here use real `assert`; do not re-introduce it.
"""
from __future__ import annotations

import random

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Order import Order
from Warehouse.catalog.Demand import Demand
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Storage_Primitive import (
    Pallet, Singleton, Storage_Size,
    viable_storage_units, _can_fit,
)
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

_SIZES       = Storage_Size.available_sizes_heights   # {'small': 12, 'medium': 24, ...}
_VALID_SIZES = set(_SIZES)


# ── order factory ────────────────────────────────────────────────────────────

def _carton(sku: int, length: int, width: int, height: int,
            equilibrium_qty: int = 20,
            handling: str = 'conveyable',
            category: str = 'food') -> Order:
    """A bare Order with known dimensions — no DB, no profile generation.

    `object.__new__` skips Order.__init__ because that path draws demand from the generator;
    every attribute the palletizer and the manager read is set explicitly below.
    """
    from Warehouse.catalog.Order import StorageHandleConfig
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = (handling, category)
    c.storage_handle_config = StorageHandleConfig(handling, category)
    c.lift_group            = (handling, category)
    c.length          = length
    c.width           = width
    c.height          = height
    c.weight          = 5
    c.demand          = Demand.from_rates(0.8, 2.0)
    c.equilibrium_qty = equilibrium_qty
    c.reorder_point   = max(1, equilibrium_qty // 2)
    c.lead_time_mean  = 0.0
    return c


# ── warehouse factory ─────────────────────────────────────────────────────────

def _build_warehouse() -> tuple:
    """Small warehouse with one pallet aisle per size tier plus a singleton aisle.

    One aisle per tier (rather than mixed-tier aisles) means a unit's landing tier is
    unambiguous: the bin it occupies names the tier the palletizer chose for it.
    """
    Aisle.next_aisle_id = 1        # class counter — reset or aisle ids leak between tests
    random.seed(0)                 # Warehouse_Builder draws from the module-level random
    # 10 pallet-width columns x 8 extra_large-height levels -> 480 x 384 physical units
    _W, _H = 10 * 48, 8 * 48
    cfgs = [
        AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['small'],       None),
        AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['medium'],      None),
        AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['large'],       None),
        AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['extra_large'], None),
        AisleConfig('conveyable', 'food', 'singleton', _W, _H,
                    ['small', 'medium', 'large'], [0.34, 0.33, 0.33]),
    ]
    wh_cfg = WarehouseConfig(total_aisles=5, aisle_splits=[0.2] * 5, aisle_configs=cfgs)
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    return wh, Inventory_Manager(wh)


def _bins_for(mgr, sku):
    """Every bin the manager indexes for a SKU, across both unit families."""
    return (list(mgr._sku_singleton_bins.get(sku, set()))
            + list(mgr._sku_pallet_bins.get(sku, set())))


def _total_qty(bins):
    return sum(b.storage.quantity for b in bins if b.storage is not None)


# ═════════════════════════════════════════════════════════════════════════════
# Part A: viable_storage_units in isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_a_small_order_becomes_a_single_singleton():
    """(8, 8, 6) fits a singleton on every axis, so the tie rule sends it to singleton.

    Getting the tie the other way would push every small SKU onto pallets and starve the
    singleton aisles that exist for exactly this stock.
    """
    small = _carton(1, length=8, width=8, height=6)
    assert _can_fit(small, Singleton, 1), 'a (8,8,6) carton must fit a singleton'

    units = viable_storage_units(small, quantity=1)
    assert len(units) == 1, f'expected 1 unit, got {len(units)}: {units}'
    assert isinstance(units[0], Singleton), (
        f'tie goes to singleton (smaller footprint); got {type(units[0]).__name__}')
    assert units[0].quantity == 1, f'quantity should be the requested 1, got {units[0].quantity}'


def test_two_oversized_dimensions_force_a_pallet():
    """(30, 25, 10) has TWO dims over 16, so no permutation fits a singleton.

    This is the boundary the whole rule turns on: the height slot is 48 and can absorb one
    large dimension, never two.
    """
    large = _carton(2, length=30, width=25, height=10)
    assert not _can_fit(large, Singleton, 1), (
        'a singleton must NOT fit when >= 2 dims exceed 16 (30, 25 both do)')
    assert _can_fit(large, Pallet, 1), 'a pallet must fit (30, 25, 10)'

    units = viable_storage_units(large, quantity=1)
    assert len(units) == 1, f'expected 1 unit, got {len(units)}'
    assert isinstance(units[0], Pallet), f'got {type(units[0]).__name__}, expected Pallet'
    assert units[0].storage_size in _VALID_SIZES, (
        f'storage_size={units[0].storage_size!r} is not one of {sorted(_VALID_SIZES)}')


def test_exactly_one_oversized_dimension_still_fits_a_singleton():
    """(20, 10, 8) — the near-miss case, and the one a naive `max(dims) <= 16` gets wrong.

    Only the permutation (h=20, w=8, l=10) fits: 20 goes in the 48-inch height slot and both
    remaining dims are under 16.  The other five permutations all put 20 in a 16-inch slot.
    """
    boundary = _carton(3, length=20, width=10, height=8)
    assert _can_fit(boundary, Singleton, 1), (
        'one large dim can go in the 48-inch height slot; a singleton must still fit')

    units = viable_storage_units(boundary, quantity=1)
    assert isinstance(units[0], Singleton), (
        f'singleton fits, so the tie rule applies; got {type(units[0]).__name__}')


def test_pallet_storage_size_is_the_smallest_tier_the_stack_fits():
    """`Pallet._fit` picks the orientation that MINIMISES stacked height, then the smallest
    tier that holds it.  A tier too large wastes a bin; too small is unplaceable.

    Tier ceilings: small <= 12, medium <= 24, large <= 36.
    """
    cases = [
        # (dims, qty, expected tier, why)
        ((25, 20, 10), 1, 'small',  'stacked height 10 fits the 12-inch small tier'),
        ((25, 20, 20), 1, 'medium', 'stacked height 20 needs medium (>12, <=24)'),
        # All three dims over 24, so the BEST orientation still stacks 26 -> large.
        ((30, 28, 26), 1, 'large',  'min dim 26 > 24 = medium ceiling, <= 36 = large'),
    ]
    for (l, w, h), qty, want, why in cases:
        u = viable_storage_units(_carton(4, length=l, width=w, height=h), quantity=qty)[0]
        assert isinstance(u, Pallet), f'({l},{w},{h}) should palletize; got {type(u).__name__}'
        assert u.storage_size == want, f'({l},{w},{h}) -> {u.storage_size!r}, expected {want!r} ({why})'


def test_a_large_quantity_splits_into_bulk_pallets_plus_one_singleton_remainder():
    """min-pallets packing: fill whole pallets first, then AT MOST one singleton.

    50 units of a (8,8,6) carton exceed one unit's capacity either way, so the packer must
    split.  The invariant that matters downstream is conservation — the split must move
    every unit, not round any away.
    """
    units = viable_storage_units(_carton(7, length=8, width=8, height=6), quantity=50)
    assert len(units) > 1, f'qty=50 exceeds one unit; expected a split, got {len(units)} unit(s)'

    n_pallets    = sum(1 for u in units if not isinstance(u, Singleton))
    n_singletons = sum(1 for u in units if isinstance(u, Singleton))
    assert n_pallets >= 1, f'bulk must go on pallets; got {n_pallets} pallets, {n_singletons} singletons'
    assert n_singletons <= 1, (
        f'at most ONE singleton remainder; got {n_singletons} — the bulk is not being '
        f'consolidated onto pallets')

    total = sum(u.quantity for u in units)
    assert total == 50, f'packing lost or invented units: {total} != 50'


# ═════════════════════════════════════════════════════════════════════════════
# Part B: enqueue_all routes through the palletizer
# ═════════════════════════════════════════════════════════════════════════════

def test_enqueue_places_a_small_order_as_bulk_pallets_plus_remainder():
    """`enqueue_all` must use `viable_storage_units`, not a default one-unit-per-SKU scheme.

    25 units of a small carton exceed one pallet's capacity, so at least one PALLET bin must
    be used even though the SKU palletizes to a singleton at qty=1.  If enqueue bypassed the
    packer, this SKU would land entirely in singleton bins.
    """
    wh, mgr = _build_warehouse()
    stock_qty = 25
    small = _carton(10, length=8, width=8, height=6, equilibrium_qty=stock_qty)
    mgr.enqueue_all([small])                 # quantity=None -> use each order's equilibrium_qty

    bins = _bins_for(mgr, 10)
    assert bins, 'small order was not placed in any bin'
    assert _total_qty(bins) == stock_qty, (
        f'placed quantity {_total_qty(bins)} != equilibrium_qty {stock_qty}')

    pallet_bins = [b for b in bins if b.unit_type == 'pallet']
    assert len(pallet_bins) >= 1, (
        f'qty {stock_qty} exceeds max_per_pallet but no pallet bin was used '
        f'({len(bins)} bins, all singleton) — enqueue is not routing through the packer')


def test_enqueue_places_a_pallet_only_order_in_a_tier_that_accommodates_it():
    """A bin's tier must be >= the unit's tier (smallest-fit, spilling UP only).

    A medium pallet in a small bin is a physical impossibility the placement code is
    responsible for preventing; this is the assertion that would catch it.
    """
    wh, mgr = _build_warehouse()
    stock_qty = 25
    large = _carton(11, length=30, width=25, height=10, equilibrium_qty=stock_qty)
    mgr.enqueue_all([large])

    bins = _bins_for(mgr, 11)
    assert bins, 'large order was not placed in any bin'
    assert _total_qty(bins) == stock_qty, (
        f'placed quantity {_total_qty(bins)} != equilibrium_qty {stock_qty}')

    pallet_bins = [b for b in bins if b.unit_type == 'pallet']
    assert pallet_bins, (
        f'a (30,25,10) order cannot be a singleton, yet {len(bins)} bins are all singleton')

    for bin_ in pallet_bins:
        assert isinstance(bin_.storage, Pallet), (
            f'a pallet bin holds {type(bin_.storage).__name__}, not a Pallet')
        assert bin_.storage.storage_size in _VALID_SIZES, (
            f'storage_size={bin_.storage.storage_size!r} not in {sorted(_VALID_SIZES)}')
        assert _SIZES[bin_.storage_size] >= _SIZES[bin_.storage.storage_size], (
            f'bin tier {bin_.storage_size!r} ({_SIZES[bin_.storage_size]}in) is SMALLER than '
            f'the pallet tier {bin_.storage.storage_size!r} '
            f'({_SIZES[bin_.storage.storage_size]}in) it holds')


# ═════════════════════════════════════════════════════════════════════════════
# Part C: initial stock is not flagged as a reorder
# ═════════════════════════════════════════════════════════════════════════════

def test_initial_stock_conserves_quantity_and_carries_no_reorder_flag():
    """Four orders spanning both unit families and a 10x quantity range.

    `_is_reorder` is what `check_reorders` uses to tell a restock from initial stock; if it
    leaked onto initial stock, the reorder accounting would double-count the entire opening
    inventory.  Quantity conservation is checked per SKU because a packing bug that drops
    the remainder unit is otherwise invisible — the SKU is still "placed".
    """
    wh, mgr = _build_warehouse()
    orders = [
        _carton(20, 8,  8,  6,  equilibrium_qty=10),
        _carton(21, 8,  8,  6,  equilibrium_qty=50),
        _carton(22, 30, 25, 10, equilibrium_qty=7),
        _carton(23, 30, 25, 10, equilibrium_qty=99),
    ]
    mgr.enqueue_all(orders)

    for c in orders:
        bins = _bins_for(mgr, c.sku)
        # A masked-failure guard used to sit here (`if not bins: print(FAIL); continue`).
        # This warehouse has room for all four orders, so an empty result is a real defect.
        assert bins, (
            f'sku={c.sku} ({c.length}x{c.width}x{c.height}, eq={c.equilibrium_qty}) was not '
            f'placed in any bin — no compatible tier had capacity')
        assert _total_qty(bins) == c.equilibrium_qty, (
            f'sku={c.sku}: placed {_total_qty(bins)} across {len(bins)} bins, '
            f'expected equilibrium_qty {c.equilibrium_qty}')

        for bin_ in bins:
            assert bin_.storage is not None, (
                f'sku={c.sku}: bin {bin_.location} is indexed for the SKU but holds nothing')
            assert not getattr(bin_.storage.order, '_is_reorder', False), (
                f'sku={c.sku}: initial stock is flagged _is_reorder')
            assert bin_.storage.order.sku == c.sku, (
                f'bin indexed under sku={c.sku} holds sku={bin_.storage.order.sku}')


# ═════════════════════════════════════════════════════════════════════════════
# Part D: _originals is the template every future reorder is built from
# ═════════════════════════════════════════════════════════════════════════════

def test_originals_hold_the_unflagged_template_a_reorder_is_copied_from():
    """`check_reorders` reads `_originals[sku]` and calls `.reorder()` on it.

    So `_originals` must hold the ORIGINAL (unflagged) order: if a reorder copy ever landed
    there, every subsequent restock would copy a copy, and any attribute `.reorder()` does
    not carry forward would decay one generation per restock.
    """
    wh, mgr = _build_warehouse()
    c_small = _carton(30, 8,  8,  6,  equilibrium_qty=20)
    c_large = _carton(31, 30, 25, 10, equilibrium_qty=15)
    mgr.enqueue_all([c_small, c_large])

    for c in (c_small, c_large):
        assert c.sku in mgr._originals, (
            f'sku={c.sku} missing from _originals (keys={sorted(mgr._originals)}) — it can '
            f'never be restocked')
        orig = mgr._originals[c.sku]
        assert orig.sku == c.sku, f'_originals[{c.sku}] holds sku={orig.sku}'
        assert not getattr(orig, '_is_reorder', False), (
            f'_originals[{c.sku}] is a REORDER copy, not the original template')
        assert orig.equilibrium_qty == c.equilibrium_qty, (
            f'_originals[{c.sku}].equilibrium_qty={orig.equilibrium_qty}, '
            f'expected {c.equilibrium_qty}')


def test_reorder_copies_preserve_everything_the_palletizer_reads():
    """`.reorder()` must be a faithful copy plus the flag — a restock has to palletize into
    the SAME unit family, or it targets a bucket the warehouse was never sized for.

    Dimensions are the direct input to `viable_storage_units`, so they are checked both
    literally and through their consequence (the resulting unit type).
    """
    wh, mgr = _build_warehouse()
    c_small = _carton(30, 8,  8,  6,  equilibrium_qty=20)
    c_large = _carton(31, 30, 25, 10, equilibrium_qty=15)
    mgr.enqueue_all([c_small, c_large])

    for c in (c_small, c_large):
        orig = mgr._originals[c.sku]
        rc   = orig.reorder()

        assert rc._is_reorder is True, f'reorder() of sku={c.sku} did not set _is_reorder'
        assert rc.sku == c.sku, f'reorder() changed sku: {rc.sku} != {c.sku}'
        assert rc.equilibrium_qty == c.equilibrium_qty, (
            f'sku={c.sku}: reorder() eq={rc.equilibrium_qty}, expected {c.equilibrium_qty}')
        assert (rc.length, rc.width, rc.height) == (c.length, c.width, c.height), (
            f'sku={c.sku}: reorder() dims {(rc.length, rc.width, rc.height)} != '
            f'{(c.length, c.width, c.height)}')

        t_orig = type(viable_storage_units(c,  quantity=1)[0])
        t_re   = type(viable_storage_units(rc, quantity=1)[0])
        assert t_orig is t_re, (
            f'sku={c.sku}: original palletizes to {t_orig.__name__} but its reorder copy to '
            f'{t_re.__name__} — the restock targets a different BinKey family')
