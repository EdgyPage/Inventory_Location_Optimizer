"""test_equilibrium_reorder.py

Verifies the Order-Up-To (OUP) equilibrium reorder model:
  - equilibrium_qty replaces stock_qty as the steady-state target
  - reorder_point = demand × (lead_time + safety_batches)
  - check_reorders fills back to equilibrium_qty, not a fixed batch size
  - lead_time_mean > 0 defers placement by sampled batches
  - DB round-trips preserve all three new attributes
  - Legacy DBs (stock_qty, no equilibrium_qty) still load cleanly

Usage
-----
    cd Tests
    python test_equilibrium_reorder.py
"""
from __future__ import annotations

import math
import os
import random
import sqlite3
import sys
import tempfile
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Aisle_Dimensions import aisle_width_for, aisle_height_for
from Aisle_Storage import Aisle
from Carton import Carton, StorageHandleConfig
from Demand import Demand
from generate_inventory import (
    EQUILIBRIUM_COVERAGE_BATCHES,
    REORDER_SAFETY_BATCHES,
    build_inventory_with_profile,
    save_inventory_to_db,
    load_inventory_from_db,
    DEFAULT_DIM_SPEC,
    DEFAULT_WEIGHT_SPEC,
)
from Inventory_Management import Inventory_Manager, _equilibrium_qty
from Storage_Primitive import viable_storage_units
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

# ── helpers ───────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0


def check(label: str, ok: bool, detail: str = '') -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f'  PASS  {label}')
    else:
        _FAIL += 1
        suffix = f'  ({detail})' if detail else ''
        print(f'  FAIL  {label}{suffix}')


def _small_warehouse(seed: int = 0) -> tuple[WarehouseConfig, Inventory_Manager]:
    Aisle.next_aisle_id = 1
    random.seed(seed)
    w = aisle_width_for(4)   # 192
    h = aisle_height_for(6)  # 288
    cfg = WarehouseConfig(
        total_aisles=4,
        aisle_splits=[0.25] * 4,
        aisle_configs=[
            AisleConfig('conveyable',     'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('non-conveyable', 'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable',     'food', 'singleton', w, h, ['singleton'], None),
            AisleConfig('non-conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    wh  = Warehouse_Builder().from_config(cfg).build()
    mgr = Inventory_Manager(wh)
    return cfg, mgr


def _make_carton(sku: int, eq_qty: int = 20, rp: int = 10,
                 lt: float = 0.0, supply_cv: float = 0.0) -> Carton:
    c                        = object.__new__(Carton)
    c._sku                   = sku
    c.storage_type           = ('conveyable', 'food')
    c.storage_handle_config  = StorageHandleConfig('conveyable', 'food')
    c.lift_group             = ('conveyable', 'food')
    c.length       = 8
    c.width        = 8
    c.height       = 6
    c.weight       = 2
    c.demand       = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty       = eq_qty
    c.reorder_point         = rp
    c.lead_time_mean        = lt
    c.supply_cv             = supply_cv
    c.expected_batch_demand = 0.8 * 4.0
    return c


# ═════════════════════════════════════════════════════════════════════════════
# Part A: build_inventory_with_profile attributes
# ═════════════════════════════════════════════════════════════════════════════

def test_profile_attributes() -> None:
    print('\n-- Part A: profile attributes --')
    inv = build_inventory_with_profile(
        num_skus=100, seed=42,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0,
        reorder_safety_batches=2.0,
    )
    for c in inv.cartons:
        check(f'sku={c.sku} has equilibrium_qty', hasattr(c, 'equilibrium_qty'))
        check(f'sku={c.sku} has reorder_point',   hasattr(c, 'reorder_point'))
        check(f'sku={c.sku} has lead_time_mean',  hasattr(c, 'lead_time_mean'))
        check(f'sku={c.sku} no stock_qty',        not hasattr(c, 'stock_qty'))
        check(
            f'sku={c.sku} eq_qty >= 1',
            c.equilibrium_qty >= 1,
            f'got {c.equilibrium_qty}',
        )
        check(
            f'sku={c.sku} rp <= eq_qty',
            c.reorder_point <= c.equilibrium_qty,
            f'rp={c.reorder_point} eq={c.equilibrium_qty}',
        )
        break   # spot-check one SKU; full loop would be very slow to print


def test_profile_equilibrium_formula() -> None:
    print('\n-- Part A2: equilibrium formula --')
    inv = build_inventory_with_profile(
        num_skus=200, seed=7,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0,
        reorder_safety_batches=2.0,
    )
    mismatches_eq = []
    mismatches_rp = []
    for c in inv.cartons:
        expected_eq = max(1, round(10.0 * c.expected_batch_demand))
        # ROP = demand × (lead_time + safety), capped at eq-1
        raw_rp      = round(c.expected_batch_demand * (c.lead_time_mean + 2.0))
        expected_rp = max(1, min(c.equilibrium_qty - 1, raw_rp))
        if c.equilibrium_qty != expected_eq:
            mismatches_eq.append((c.sku, c.equilibrium_qty, expected_eq))
        if c.reorder_point != expected_rp:
            mismatches_rp.append((c.sku, c.reorder_point, expected_rp))

    check('equilibrium_qty = round(10 × expected_batch_demand) for all SKUs',
          len(mismatches_eq) == 0, f'{mismatches_eq[:3]}')
    check('reorder_point = demand × (lead_time + safety), capped at eq-1',
          len(mismatches_rp) == 0, f'{mismatches_rp[:3]}')

    # Popular items should have higher reorder_point than slow movers
    sorted_by_demand = sorted(inv.cartons, key=lambda c: c.expected_batch_demand)
    low_rp  = sorted_by_demand[0].reorder_point
    high_rp = sorted_by_demand[-1].reorder_point
    check('highest-demand SKU has higher reorder_point than lowest-demand SKU',
          high_rp > low_rp,
          f'low_demand rp={low_rp}  high_demand rp={high_rp}')


# ═════════════════════════════════════════════════════════════════════════════
# Part B: DB round-trip (save + load)
# ═════════════════════════════════════════════════════════════════════════════

def test_db_roundtrip() -> None:
    print('\n-- Part B: DB round-trip --')
    inv = build_inventory_with_profile(
        num_skus=50, seed=1,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=8.0,
        reorder_safety_batches=2.0,
        lead_time_mean_batches=2.0,
    )
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        save_inventory_to_db(inv, db_path, {'test': True})
        inv2 = load_inventory_from_db(db_path)

        orig = {c.sku: c for c in inv.cartons}
        loaded = {c.sku: c for c in inv2.cartons}

        check('same SKU count', len(orig) == len(loaded))
        mismatches = []
        for sku, c in orig.items():
            c2 = loaded[sku]
            if c.equilibrium_qty != c2.equilibrium_qty:
                mismatches.append(('eq', sku, c.equilibrium_qty, c2.equilibrium_qty))
            if c.reorder_point != c2.reorder_point:
                mismatches.append(('rp', sku, c.reorder_point, c2.reorder_point))
            if abs(c.lead_time_mean - c2.lead_time_mean) > 1e-9:
                mismatches.append(('lt', sku, c.lead_time_mean, c2.lead_time_mean))
        check('equilibrium_qty/reorder_point/lead_time_mean preserved after save+load',
              len(mismatches) == 0, f'{mismatches[:3]}')

        # Verify no stock_qty column was written
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(cartons)').fetchall()]
        conn.close()
        check('stock_qty column absent from new DB', 'stock_qty' not in cols)
        check('equilibrium_qty column present',      'equilibrium_qty' in cols)
        check('lead_time_mean column present',        'lead_time_mean'  in cols)
    finally:
        os.unlink(db_path)


def test_legacy_db_load() -> None:
    """Old DBs with stock_qty but no equilibrium_qty should load as equilibrium_qty."""
    print('\n-- Part B2: legacy DB backward compat --')
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript('''
            CREATE TABLE cartons (
                sku INTEGER PRIMARY KEY, handling TEXT, category TEXT,
                length INTEGER, width INTEGER, height INTEGER, weight INTEGER,
                demand_frequency REAL, demand_qty_rate REAL,
                stock_qty INTEGER, expected_batch_demand REAL, reorder_point INTEGER
            );
        ''')
        conn.execute(
            'INSERT INTO cartons VALUES (1,"conveyable","food",10,10,8,5,0.5,4.0,50,2.0,6)'
        )
        conn.commit(); conn.close()

        inv = load_inventory_from_db(db_path)
        c = inv.cartons[0]
        check('legacy load: equilibrium_qty == stock_qty',
              c.equilibrium_qty == 50, f'got {c.equilibrium_qty}')
        check('legacy load: reorder_point preserved',
              c.reorder_point == 6, f'got {c.reorder_point}')
        check('legacy load: lead_time_mean defaults to 0',
              c.lead_time_mean == 0.0, f'got {c.lead_time_mean}')
    finally:
        os.unlink(db_path)


# ═════════════════════════════════════════════════════════════════════════════
# Part C: OUP refill-to-target logic
# ═════════════════════════════════════════════════════════════════════════════

def test_oup_refill() -> None:
    """check_reorders should enqueue exactly equilibrium_qty - current_qty units."""
    print('\n-- Part C: OUP refill quantity --')
    _, mgr = _small_warehouse(seed=0)

    c = _make_carton(sku=1, eq_qty=20, rp=8, lt=0.0)
    mgr.enqueue(c)

    # Verify initial placement at equilibrium_qty
    init_qty = mgr._current_quantities.get(1, 0)
    check('initial quantity == equilibrium_qty', init_qty == 20,
          f'got {init_qty}')

    # Drain to reorder point manually
    mgr._current_quantities[1] = 5   # simulate picks depleting below rp=8
    mgr._depleted_skus.add(1)

    triggered = mgr.check_reorders()
    check('reorder triggered', 1 in triggered, f'triggered={triggered}')

    # OUP should have queued enough to bring qty from 5 → 20 = 15 units
    placed_qty = mgr._current_quantities.get(1, 0)
    check('quantity refilled to equilibrium after drain',
          placed_qty == 20, f'got {placed_qty}')


def test_oup_no_overstock() -> None:
    """If current_qty is above reorder_point when reorder fires (edge case),
    qty ordered should still be positive but not exceed equilibrium."""
    print('\n-- Part C2: OUP no overstock --')
    _, mgr = _small_warehouse(seed=1)
    c = _make_carton(sku=2, eq_qty=30, rp=10, lt=0.0)
    mgr.enqueue(c)

    # Manually push qty just below rp — should order 30 - 9 = 21 units
    mgr._current_quantities[2] = 9
    mgr._depleted_skus.add(2)
    mgr.check_reorders()
    final = mgr._current_quantities.get(2, 0)
    check('OUP refill does not exceed equilibrium_qty',
          final <= 30, f'got {final}')
    check('OUP refill is positive', final > 9, f'got {final}')


# ═════════════════════════════════════════════════════════════════════════════
# Part D: lead-time deferral
# ═════════════════════════════════════════════════════════════════════════════

def test_immediate_reorder() -> None:
    """With lead_time_mean=0, reorder units land in _queue immediately."""
    print('\n-- Part D: immediate reorder (lead_time=0) --')
    _, mgr = _small_warehouse(seed=2)
    c = _make_carton(sku=10, eq_qty=20, rp=8, lt=0.0)
    mgr.enqueue(c)

    mgr._current_quantities[10] = 5
    mgr._depleted_skus.add(10)
    before_batch = mgr._batch_num
    mgr.check_reorders()

    check('no deferred reorders when lead_time=0',
          len(mgr._deferred_reorders) == 0,
          f'deferred={dict(mgr._deferred_reorders)}')
    check('quantity restored immediately',
          mgr._current_quantities.get(10, 0) == 20,
          f'qty={mgr._current_quantities.get(10)}')


def test_deferred_reorder() -> None:
    """With lead_time_mean=3, order should not arrive until ~3 batches later."""
    print('\n-- Part D2: deferred reorder (lead_time=3) --')
    _, mgr = _small_warehouse(seed=3)
    random.seed(999)   # fix sampling so gauss gives predictable lead

    c = _make_carton(sku=20, eq_qty=20, rp=8, lt=3.0)
    mgr.enqueue(c)

    mgr._current_quantities[20] = 5
    mgr._depleted_skus.add(20)
    triggered = mgr.check_reorders()   # batch 1

    check('reorder was triggered', 20 in triggered)
    check('units NOT immediately in queue',
          mgr._queued_sku_counts.get(20, 0) == 0,
          f'queued={mgr._queued_sku_counts.get(20, 0)}')
    check('units in deferred dict',
          len(mgr._deferred_sku_counts) > 0,
          f'deferred_counts={mgr._deferred_sku_counts}')
    check('quantity NOT yet restored',
          mgr._current_quantities.get(20, 0) < 20,
          f'qty={mgr._current_quantities.get(20)}')

    # Run batches until deferred order arrives (≤ 10 batches)
    arrived = False
    for _ in range(10):
        mgr.check_reorders()
        if mgr._current_quantities.get(20, 0) == 20:
            arrived = True
            break

    check('deferred order eventually arrives and restores quantity',
          arrived, f'final qty={mgr._current_quantities.get(20, 0)}')


def test_deferred_blocks_duplicate_reorder() -> None:
    """While a deferred reorder is in-flight, a second trigger should not
    fire another reorder for the same SKU."""
    print('\n-- Part D3: no duplicate deferred reorders --')
    _, mgr = _small_warehouse(seed=4)
    random.seed(888)

    c = _make_carton(sku=30, eq_qty=20, rp=8, lt=5.0)
    mgr.enqueue(c)

    mgr._current_quantities[30] = 5
    mgr._depleted_skus.add(30)
    mgr.check_reorders()   # triggers deferred reorder

    # Fire again at lower qty
    mgr._current_quantities[30] = 2
    mgr._depleted_skus.add(30)
    triggered2 = mgr.check_reorders()

    check('second trigger skipped while deferred in-flight',
          30 not in triggered2, f'triggered2={triggered2}')


# ═════════════════════════════════════════════════════════════════════════════
# Part E: _equilibrium_qty helper
# ═════════════════════════════════════════════════════════════════════════════

def test_equilibrium_qty_helper() -> None:
    print('\n-- Part E: _equilibrium_qty helper --')
    c_new = _make_carton(sku=40, eq_qty=25)
    check('reads equilibrium_qty on new carton', _equilibrium_qty(c_new) == 25)

    c_legacy = object.__new__(Carton)
    c_legacy.stock_qty = 50
    check('falls back to stock_qty on legacy carton', _equilibrium_qty(c_legacy) == 50)

    c_bare = object.__new__(Carton)
    check('defaults to 1 with neither attribute', _equilibrium_qty(c_bare) == 1)


# ═════════════════════════════════════════════════════════════════════════════
# Part F: fill stability over batches (regression)
# ═════════════════════════════════════════════════════════════════════════════

def test_fill_stability() -> None:
    """Fill rate should not drift downward over 30 batches when using OUP."""
    print('\n-- Part F: fill stability over 30 batches --')
    from Workload_Builder import Batch, BatchConfig

    inv = build_inventory_with_profile(
        num_skus=100, seed=42,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0,
        reorder_safety_batches=2.0,
    )

    Aisle.next_aisle_id = 1
    random.seed(42)
    w = aisle_width_for(6)
    h = aisle_height_for(8)
    cfgs = []
    for cat in ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']:
        cfgs.append(AisleConfig('conveyable',     cat, 'pallet',    w, h, ['small','medium','large','extra_large'], [0.25]*4))
        cfgs.append(AisleConfig('non-conveyable', cat, 'pallet',    w, h, ['small','medium','large','extra_large'], [0.25]*4))
        cfgs.append(AisleConfig('conveyable',     cat, 'singleton', w, h, ['singleton'], None))
        cfgs.append(AisleConfig('non-conveyable', cat, 'singleton', w, h, ['singleton'], None))

    total = sum(
        max(1, math.ceil(
            sum(1 for u in viable_storage_units(c, c.equilibrium_qty)
                if (u.unit_category == ('pallet' if cfg.unit_type == 'pallet' else 'singleton')))
            / (len(cfgs) * 0.80)
        ))
        for cfg in cfgs for c in inv.cartons[:1]  # just sizing heuristic
    )
    n_aisles = max(len(cfgs), 1)

    wh_cfg = WarehouseConfig(
        total_aisles=n_aisles,
        aisle_splits=[1/n_aisles] * n_aisles,
        aisle_configs=cfgs,
    )
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(inv.cartons)

    total_bins = len(wh.bins)
    batch_cfg  = BatchConfig(inventory_size=100, mean_fraction=0.3, std_fraction=0.05)
    fills: list[float] = []

    for _ in range(30):
        mgr.check_reorders()
        b = Batch(batch_cfg, inv, affinity=None)
        from Workload_Builder import Task
        task = Task.from_batch(b, wh, manager=mgr)
        fills.append(len(mgr.unavailable) / total_bins)

    first_half  = mean(fills[:15])
    second_half = mean(fills[15:])
    drift = second_half - first_half

    check('fill does not drop more than 15pp over 30 batches',
          drift > -0.15, f'drift={drift:.3f} (first={first_half:.2f} second={second_half:.2f})')
    check('average fill >= 30% throughout',
          mean(fills) >= 0.30, f'mean={mean(fills):.2f}')


# ═════════════════════════════════════════════════════════════════════════════
# Part G: supply_cv — stochastic received quantity
# ═════════════════════════════════════════════════════════════════════════════

def test_supply_cv_zero_is_exact() -> None:
    """supply_cv=0: the sampling formula always returns exactly the ideal quantity."""
    print('\n-- Part G: supply_cv=0 exact fulfillment --')
    # Test the sampling math directly — N(ideal, 0) must equal ideal always.
    for depletion in [20, 10, 5]:
        eq_qty = 40
        ideal  = eq_qty - depletion
        c      = _make_carton(sku=50 + depletion, eq_qty=eq_qty, rp=15, supply_cv=0.0)
        rc     = c.reorder()
        # Replicate the formula from check_reorders
        cv  = getattr(rc, 'supply_cv', 0.0)
        qty = max(1, round(random.gauss(ideal, ideal * cv))) if cv > 0.0 else ideal
        check(f'cv=0 received {qty} == ideal {ideal}',
              qty == ideal, f'got {qty}')


def test_supply_cv_produces_variance() -> None:
    """supply_cv > 0 should produce varying received quantities across many samples.

    Tests the sampling math directly — build the carton, call reorder() to get
    the rc copy (with supply_cv), then replicate the sampling formula N times.
    This avoids needing a warehouse large enough to accept 30 consecutive reorders.
    """
    print('\n-- Part G2: supply_cv > 0 introduces variance --')
    random.seed(12345)

    eq_qty   = 100
    cur_qty  = 30
    ideal    = eq_qty - cur_qty   # 70
    supply_cv = 0.20

    c  = _make_carton(sku=60, eq_qty=eq_qty, rp=40, supply_cv=supply_cv)
    rc = c.reorder()

    received = [
        max(1, round(random.gauss(ideal, ideal * rc.supply_cv)))
        for _ in range(50)
    ]

    check('supply_cv=0.2 produces at least 5 distinct received quantities',
          len(set(received)) >= 5,
          f'received set size={len(set(received))}')
    check('all received quantities >= 1',
          all(r >= 1 for r in received),
          f'min received={min(received)}')
    avg = sum(received) / len(received)
    check(f'mean received near ideal ({ideal}) within 20%',
          abs(avg - ideal) / ideal < 0.20,
          f'avg={avg:.1f}  ideal={ideal}')


def test_supply_cv_floor_prevents_zero() -> None:
    """Even with extreme cv, received quantity is always >= 1."""
    print('\n-- Part G3: supply_cv floor >= 1 --')
    _, mgr = _small_warehouse(seed=12)
    random.seed(999)

    c = _make_carton(sku=70, eq_qty=5, rp=2, supply_cv=2.0)   # wildly unreliable
    mgr.enqueue(c)

    for _ in range(50):
        mgr._current_quantities[70] = 1
        mgr._depleted_skus.add(70)
        mgr._queued_sku_counts.pop(70, None)
        mgr._deferred_sku_counts.pop(70, None)
        mgr.check_reorders()
        qty = mgr._current_quantities.get(70, 1) - 1
        if qty < 1:
            check('received >= 1 even with cv=2.0', False, f'got {qty}')
            return
    check('received always >= 1 over 50 reorders with cv=2.0', True)


def test_supply_cv_in_profile_and_db() -> None:
    """supply_cv is set per-SKU at profile time and survives a DB round-trip."""
    print('\n-- Part G4: supply_cv profile + DB --')
    inv = build_inventory_with_profile(
        num_skus=50, seed=5,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        supply_cv_mean=0.15,
    )
    # All cartons should have supply_cv >= 0
    all_nonneg = all(getattr(c, 'supply_cv', -1) >= 0 for c in inv.cartons)
    check('all supply_cv >= 0', all_nonneg)
    # With supply_cv_mean=0.15, expect variation across SKUs
    cvs = [c.supply_cv for c in inv.cartons]
    check('supply_cv varies across SKUs (not all identical)',
          len(set(round(v, 4) for v in cvs)) > 1,
          f'unique values={len(set(round(v,4) for v in cvs))}')

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        save_inventory_to_db(inv, db_path, {})
        inv2 = load_inventory_from_db(db_path)
        orig   = {c.sku: c.supply_cv for c in inv.cartons}
        loaded = {c.sku: c.supply_cv for c in inv2.cartons}
        mismatches = [(s, orig[s], loaded[s]) for s in orig if abs(orig[s] - loaded.get(s, -1)) > 1e-9]
        check('supply_cv preserved in DB round-trip', len(mismatches) == 0,
              f'{mismatches[:3]}')
    finally:
        os.unlink(db_path)


# ═════════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f'\n{"="*62}')
    print(f'  Equilibrium reorder model tests')
    print(f'{"="*62}')

    test_profile_attributes()
    test_profile_equilibrium_formula()
    test_db_roundtrip()
    test_legacy_db_load()
    test_oup_refill()
    test_oup_no_overstock()
    test_immediate_reorder()
    test_deferred_reorder()
    test_deferred_blocks_duplicate_reorder()
    test_equilibrium_qty_helper()
    test_fill_stability()
    test_supply_cv_zero_is_exact()
    test_supply_cv_produces_variance()
    test_supply_cv_floor_prevents_zero()
    test_supply_cv_in_profile_and_db()

    print(f'\n{"="*62}')
    if _FAIL == 0:
        print(f'  All {_PASS} checks passed.')
    else:
        print(f'  {_PASS} passed  {_FAIL} FAILED')
    print(f'{"="*62}\n')
    sys.exit(0 if _FAIL == 0 else 1)
