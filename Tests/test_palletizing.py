"""
test_palletizing.py — Verify that initial orders go through the palletizing
function (viable_storage_units) and that the correct StorageUnit type and
storage_size are assigned to each placed bin.

Two key rules (from Storage_Primitive.viable_storage_units with qty=1):
  - Singleton wins when it fits (tie goes to singleton — smaller footprint)
    ->Singleton fits if at least one permutation of dims has w≤16, l≤16, h≤48
    ->Concretely: fits when at most one dim exceeds 16
  - Pallet wins only when singleton CANNOT fit
    ->Singleton fails when ≥ 2 dims exceed 16 (no permutation puts both large
       dims in the height slot simultaneously)

Usage
-----
    cd Tests
    python test_palletizing.py
"""
from __future__ import annotations

import os
import random
import sys
import itertools

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'Warehouse'))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'Optimization'))

from Aisle_Storage import Aisle
from Carton import Carton
from Demand import Demand
from Inventory_Management import Inventory_Manager
from Storage_Primitive import (
    Pallet, Singleton, Storage_Size,
    viable_storage_units, _can_fit,
)
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

# ── output helpers ────────────────────────────────────────────────────────────
_passed = 0
_failed = 0

def ok(name: str) -> None:
    global _passed; _passed += 1
    print(f'  PASS  {name}')

def fail(name: str, detail: str = '') -> None:
    global _failed; _failed += 1
    print(f'  FAIL  {name}')
    if detail:
        print(f'        {detail}')

def check(name: str, cond: bool, detail: str = '') -> None:
    ok(name) if cond else fail(name, detail)

def section(title: str) -> None:
    print(f'\n-- {title} --')

# ── carton factory ────────────────────────────────────────────────────────────

def _carton(sku: int, length: int, width: int, height: int,
            stock_qty: int = 20,
            handling: str = 'conveyable',
            category: str = 'food') -> Carton:
    c              = object.__new__(Carton)
    c._sku         = sku
    c.storage_type = (handling, category)
    c.lift_group   = (handling, category)
    c.length       = length
    c.width        = width
    c.height       = height
    c.weight       = 5
    c.demand       = Demand.from_rates(0.8, 2.0)
    c.stock_qty    = stock_qty
    return c

# ── warehouse factory ─────────────────────────────────────────────────────────

_SIZES  = Storage_Size.available_sizes_heights  # {'small':12, 'medium':24, ...}
_VALID_SIZES = set(_SIZES.keys())

def _build_warehouse() -> tuple:
    """Small warehouse with both pallet aisles (all sizes) and singleton aisles."""
    Aisle.next_aisle_id = 1
    random.seed(0)
    cfgs = [
        AisleConfig('conveyable', 'food', 'pallet',    10, 8, ['small'],      None),
        AisleConfig('conveyable', 'food', 'pallet',    10, 8, ['medium'],     None),
        AisleConfig('conveyable', 'food', 'pallet',    10, 8, ['large'],      None),
        AisleConfig('conveyable', 'food', 'pallet',    10, 8, ['extra_large'],None),
        AisleConfig('conveyable', 'food', 'singleton', 10, 8,
                    ['small', 'medium', 'large'], [0.34, 0.33, 0.33]),
    ]
    wh_cfg = WarehouseConfig(
        total_aisles  = 5,
        aisle_splits  = [0.2] * 5,
        aisle_configs = cfgs,
    )
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh)
    return wh, mgr

# ─────────────────────────────────────────────────────────────────────────────
# Part A: viable_storage_units direct assertions
# ─────────────────────────────────────────────────────────────────────────────

def _singleton_fits(carton: Carton, qty: int = 1) -> bool:
    return _can_fit(carton, Singleton, qty)

def _pallet_fits(carton: Carton, qty: int = 1) -> bool:
    return _can_fit(carton, Pallet, qty)

def test_viable_storage_units_direct() -> None:
    section('Part A: viable_storage_units direct logic')

    # ── A1: small carton — singleton fits, tie ->singleton wins ───────────────
    small = _carton(1, length=8, width=8, height=6)
    units = viable_storage_units(small, quantity=1)
    check('A1a  small carton (8,8,6): singleton fits',
          _singleton_fits(small))
    check('A1b  small carton: viable_storage_units returns 1 unit',
          len(units) == 1)
    check('A1c  small carton: returned unit is Singleton (tie goes to singleton)',
          isinstance(units[0], Singleton),
          f'got {type(units[0]).__name__}')
    check('A1d  small carton: unit.quantity == 1 before stock_qty override',
          units[0].quantity == 1)

    # ── A2: large carton (2 dims > 16) — singleton impossible ->pallet ────────
    large = _carton(2, length=30, width=25, height=10)
    check('A2a  large carton (30,25,10): singleton does NOT fit',
          not _singleton_fits(large),
          'expected singleton to fail when >=2 dims > 16')
    check('A2b  large carton: pallet fits',
          _pallet_fits(large))
    units2 = viable_storage_units(large, quantity=1)
    check('A2c  large carton: returns 1 unit',
          len(units2) == 1)
    check('A2d  large carton: returned unit is Pallet',
          isinstance(units2[0], Pallet),
          f'got {type(units2[0]).__name__}')
    check('A2e  large carton: Pallet has valid storage_size',
          units2[0].storage_size in _VALID_SIZES,
          f'storage_size={units2[0].storage_size}')

    # ── A3: boundary — exactly one dim > 16 ->singleton still fits ───────────
    boundary = _carton(3, length=20, width=10, height=8)
    # permutation (8, 10, 20): h=8, w=10<=16, l=20 >16 — fails
    # permutation (8, 20, 10): w=20 >16 — fails
    # permutation (10, 8, 20): l=20 >16 — fails
    # permutation (10, 20, 8): w=20 >16 — fails
    # permutation (20, 8, 10): h=20, w=8<=16, l=10<=16 — FITS
    check('A3a  boundary carton (20,10,8): singleton still fits (one large dim)',
          _singleton_fits(boundary))
    units3 = viable_storage_units(boundary, quantity=1)
    check('A3b  boundary carton: returns Singleton (singleton fits ->tie ->singleton wins)',
          isinstance(units3[0], Singleton),
          f'got {type(units3[0]).__name__}')

    # ── A4: pallet storage_size tiers ─────────────────────────────────────────
    # stacked height = carton_height * qty; must fit in named size tier
    # height=10 ->stacked=10 ->fits 'small' (<=12)
    c_small = _carton(4, length=25, width=20, height=10)
    u_small = viable_storage_units(c_small, quantity=1)
    check('A4a  pallet storage_size=small for stacked_height=10',
          isinstance(u_small[0], Pallet) and u_small[0].storage_size == 'small',
          f'got type={type(u_small[0]).__name__} size={getattr(u_small[0], "storage_size", None)}')

    # height=20 ->stacked=20 ->fits 'medium' (<=24)
    c_medium = _carton(5, length=25, width=20, height=20)
    u_medium = viable_storage_units(c_medium, quantity=1)
    check('A4b  pallet storage_size=medium for stacked_height=20',
          isinstance(u_medium[0], Pallet) and u_medium[0].storage_size == 'medium',
          f'got type={type(u_medium[0]).__name__} size={getattr(u_medium[0], "storage_size", None)}')

    # All 3 dims > 24 forces 'large': Pallet._fit picks smallest fitting tier by
    # choosing the best orientation — min stacked_h = min(dims) > 24 = medium_max
    # (30, 28, 26): min=26 > 24, 26 <= 36=large_max -> storage_size='large'
    c_large = _carton(6, length=30, width=28, height=26)
    u_large = viable_storage_units(c_large, quantity=1)
    check('A4c  pallet storage_size=large when all dims > 24 (min stacked_h=26 > 24)',
          isinstance(u_large[0], Pallet) and u_large[0].storage_size == 'large',
          f'got type={type(u_large[0]).__name__} size={getattr(u_large[0], "storage_size", None)}')

    # ── A5: multi-unit reorder qty — large qty may force multiple pallet units ─
    # Small carton, qty=50: max_qty_for_singleton limited by stacking
    # With (8,8,6): stack along height axis: max = 48//6 = 8 ->needs 7 singleton units for 50
    # Pallet: dims 8,8,6 all ≤ 48 ->stack height = 6*qty ≤ 48 ->max_qty = 8 too
    # Both produce same unit count and volume ->singleton wins (tie)
    small_multi = _carton(7, length=8, width=8, height=6)
    units_multi = viable_storage_units(small_multi, quantity=50)
    check('A5a  small carton qty=50: palletizing produces multiple units',
          len(units_multi) > 1,
          f'got {len(units_multi)} units')
    check('A5b  small carton qty=50: all units are Singleton (ties go to singleton)',
          all(isinstance(u, Singleton) for u in units_multi),
          f'types={[type(u).__name__ for u in units_multi]}')
    check('A5c  small carton qty=50: total quantity across all units == 50',
          sum(u.quantity for u in units_multi) == 50,
          f'got {sum(u.quantity for u in units_multi)}')


# ─────────────────────────────────────────────────────────────────────────────
# Part B: enqueue_all routes through palletizing ->bin reflects StorageUnit type
# ─────────────────────────────────────────────────────────────────────────────

def test_enqueue_routes_through_palletizer() -> None:
    section('Part B: enqueue_all routes through viable_storage_units')
    random.seed(42)
    wh, mgr = _build_warehouse()

    stock_qty = 25
    small_carton = _carton(10, length=8,  width=8,  height=6,  stock_qty=stock_qty)
    large_carton = _carton(11, length=30, width=25, height=10, stock_qty=stock_qty)

    mgr.enqueue_all([small_carton, large_carton], quantity=1)

    # ── B1: small carton lands in a singleton bin ──────────────────────────────
    small_bins = (mgr._sku_singleton_bins.get(10, []) +
                  mgr._sku_pallet_bins.get(10, []))
    check('B1a  small carton (8,8,6) placed in at least one bin',
          len(small_bins) > 0)
    if small_bins:
        bin_ = small_bins[0]
        check('B1b  small carton: bin_.storage is Singleton',
              isinstance(bin_.storage, Singleton),
              f'got {type(bin_.storage).__name__}')
        check('B1c  small carton: bin_.unit_type == "singleton"',
              bin_.unit_type == 'singleton',
              f'got {bin_.unit_type}')
        check('B1d  small carton: unit.quantity overridden to stock_qty after drain',
              bin_.storage.quantity == stock_qty,
              f'expected {stock_qty}  got {bin_.storage.quantity}')
        check('B1e  small carton: bin in _sku_singleton_bins (not pallet)',
              bin_ in mgr._sku_singleton_bins.get(10, []),
              'expected in _sku_singleton_bins')

    # ── B2: large carton lands in a pallet bin ────────────────────────────────
    large_bins = (mgr._sku_singleton_bins.get(11, []) +
                  mgr._sku_pallet_bins.get(11, []))
    check('B2a  large carton (30,25,10) placed in at least one bin',
          len(large_bins) > 0)
    if large_bins:
        bin_ = large_bins[0]
        check('B2b  large carton: bin_.storage is Pallet',
              isinstance(bin_.storage, Pallet),
              f'got {type(bin_.storage).__name__}')
        check('B2c  large carton: bin_.unit_type == "pallet"',
              bin_.unit_type == 'pallet',
              f'got {bin_.unit_type}')
        check('B2d  large carton: unit.quantity overridden to stock_qty after drain',
              bin_.storage.quantity == stock_qty,
              f'expected {stock_qty}  got {bin_.storage.quantity}')
        check('B2e  large carton: bin in _sku_pallet_bins (not singleton)',
              bin_ in mgr._sku_pallet_bins.get(11, []),
              'expected in _sku_pallet_bins')
        check('B2f  large carton: Pallet has valid storage_size',
              bin_.storage.storage_size in _VALID_SIZES,
              f'storage_size={bin_.storage.storage_size}')
        check('B2g  large carton: bin storage_size accommodates pallet storage_size',
              _SIZES.get(bin_.storage_size, 0) >= _SIZES.get(bin_.storage.storage_size, 0),
              f'bin_size={bin_.storage_size}  pallet_size={bin_.storage.storage_size}')


# ─────────────────────────────────────────────────────────────────────────────
# Part C: stock_qty override applied correctly, _is_reorder absent
# ─────────────────────────────────────────────────────────────────────────────

def test_stock_qty_override_and_no_reorder_flag() -> None:
    section('Part C: stock_qty override and absence of _is_reorder on initial stock')
    random.seed(0)
    wh, mgr = _build_warehouse()

    cartons = [
        _carton(20, 8, 8, 6,   stock_qty=10),
        _carton(21, 8, 8, 6,   stock_qty=50),
        _carton(22, 30, 25, 10, stock_qty=7),
        _carton(23, 30, 25, 10, stock_qty=99),
    ]
    mgr.enqueue_all(cartons, quantity=1)

    for c in cartons:
        bins_for = (mgr._sku_singleton_bins.get(c.sku, []) +
                    mgr._sku_pallet_bins.get(c.sku, []))
        if not bins_for:
            fail(f'C-{c.sku}  carton sku={c.sku} was not placed (no compatible bin?)')
            continue

        for bin_ in bins_for:
            u = bin_.storage
            if u is None:
                continue
            check(f'C-{c.sku}  unit.quantity == stock_qty ({c.stock_qty})',
                  u.quantity == c.stock_qty,
                  f'expected {c.stock_qty}  got {u.quantity}')
            check(f'C-{c.sku}  _is_reorder absent on initial carton',
                  not getattr(u.carton, '_is_reorder', False),
                  f'_is_reorder={getattr(u.carton, "_is_reorder", False)}')
            check(f'C-{c.sku}  carton reference preserved (same sku)',
                  u.carton.sku == c.sku,
                  f'expected sku={c.sku}  got {u.carton.sku}')


# ─────────────────────────────────────────────────────────────────────────────
# Part D: _originals stores non-reorder carton (needed for future reorders)
# ─────────────────────────────────────────────────────────────────────────────

def test_originals_stored_correctly() -> None:
    section('Part D: _originals set for reorder use')
    random.seed(0)
    wh, mgr = _build_warehouse()

    c_small = _carton(30, 8, 8, 6,   stock_qty=20)
    c_large = _carton(31, 30, 25, 10, stock_qty=15)
    mgr.enqueue_all([c_small, c_large], quantity=1)

    for c in [c_small, c_large]:
        check(f'D-{c.sku}  _originals contains sku={c.sku}',
              c.sku in mgr._originals,
              f'keys={list(mgr._originals.keys())}')
        if c.sku in mgr._originals:
            orig = mgr._originals[c.sku]
            check(f'D-{c.sku}  _originals[{c.sku}] has correct sku',
                  orig.sku == c.sku)
            check(f'D-{c.sku}  _originals[{c.sku}] is NOT flagged _is_reorder',
                  not getattr(orig, '_is_reorder', False))
            check(f'D-{c.sku}  _originals[{c.sku}].stock_qty == {c.stock_qty}',
                  orig.stock_qty == c.stock_qty,
                  f'got {orig.stock_qty}')

            # Verify reorder() works correctly from _originals
            rc = orig.reorder()
            check(f'D-{c.sku}  reorder() produces _is_reorder=True',
                  rc._is_reorder is True)
            check(f'D-{c.sku}  reorder() preserves sku',
                  rc.sku == c.sku)
            check(f'D-{c.sku}  reorder() preserves stock_qty',
                  rc.stock_qty == c.stock_qty)
            check(f'D-{c.sku}  reorder() preserves dimensions',
                  (rc.length, rc.width, rc.height) == (c.length, c.width, c.height))

            # Verify reorder() palletizes the same way as the original
            units_orig   = viable_storage_units(c,  quantity=1)
            units_reorder= viable_storage_units(rc, quantity=1)
            check(f'D-{c.sku}  reorder palletizes to same StorageUnit type as original',
                  type(units_orig[0]) == type(units_reorder[0]),
                  f'orig={type(units_orig[0]).__name__}  '
                  f'reorder={type(units_reorder[0]).__name__}')


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '=' * 60)
    print('  Palletizing Function Tests')
    print('=' * 60)

    test_viable_storage_units_direct()
    test_enqueue_routes_through_palletizer()
    test_stock_qty_override_and_no_reorder_flag()
    test_originals_stored_correctly()

    print('\n' + '=' * 60)
    total = _passed + _failed
    if _failed == 0:
        print(f'  All {total} checks passed.')
    else:
        print(f'  {_passed} passed  {_failed} failed  ({total} total)')
    print('=' * 60 + '\n')

    sys.exit(0 if _failed == 0 else 1)
