"""test_warehouse_sizing.py

Verifies automatic warehouse sizing + SKU sampling on Inventory_Manager:
  - bucket_requirements tracks the exact (handling, category, size, unit_type) tier
  - plan_warehouse gives every bucket >=1 aisle (every SKU placeable)
  - caps (max_bins / max_aisles) are never exceeded (at-or-under capacity)
  - sampling respects per-bucket capacity and fills empty space to target_fill
  - resampled SKUs get equilibrium_qty / reorder_point scaled by selection count
  - restocks do NOT cause unbounded queue growth (the reported symptom)

Usage
-----
    cd Tests
    python test_warehouse_sizing.py
"""
from __future__ import annotations

import os
import random
import sys
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Aisle_Dimensions import aisle_width_for, aisle_height_for, uniform_aisle_bins
from Aisle_Storage import Aisle
from Carton import Carton, StorageHandleConfig
from Demand import Demand
from generate_inventory import (
    build_inventory_with_profile, DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
)
from Inventory_Management import Inventory_Manager
from Storage_Primitive import viable_storage_units
from Warehouse_Builder import Warehouse_Builder

# ── harness ─────────────────────────────────────────────────────────────────

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


_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_HANDLINGS  = ['conveyable', 'non-conveyable']

# Small aisles keep the 60-bucket floor fast.
_AISLE_W = aisle_width_for(2)    # 96
_AISLE_H = aisle_height_for(2)   # 96
_TARGET  = 0.85


def _inventory(n_skus: int, seed: int = 7):
    return build_inventory_with_profile(
        num_skus=n_skus, seed=seed,
        handling_splits=[0.5, 0.5],
        category_splits=[1/6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0,
        reorder_safety_batches=2.0,
    )


def _make_carton(sku: int, eq_qty: int, length=8, width=8, height=6,
                 handling='conveyable', category='food') -> Carton:
    c = object.__new__(Carton)
    c._sku                   = sku
    c.storage_type           = (handling, category)
    c.storage_handle_config  = StorageHandleConfig(handling, category)
    c.lift_group             = (handling, category)
    c.length, c.width, c.height, c.weight = length, width, height, 2
    c.demand                 = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty        = eq_qty
    c.reorder_point          = max(1, eq_qty // 2)
    c.lead_time_mean         = 0.0
    c.supply_cv              = 0.0
    c.expected_batch_demand  = 0.8 * 4.0
    return c


def _plan(inv, **kw):
    return Inventory_Manager.plan_warehouse(
        inv.cartons,
        categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=_AISLE_W, aisle_height=_AISLE_H,
        target_fill=_TARGET, rng=random.Random(42), **kw,
    )


# ═════════════════════════════════════════════════════════════════════════════

def test_bucket_requirements_track_size_tier():
    print('\n-- bucket_requirements track size tier --')
    cartons = [
        _make_carton(1, 30, 10, 10, 10),   # pallet, some tier
        _make_carton(2, 5,  6,  6,  4),    # likely singleton
    ]
    req = Inventory_Manager.bucket_requirements(cartons)
    # keys are 4-tuples including storage_size
    check('keys are (h, c, size, unit_type) 4-tuples',
          all(len(k) == 4 for k in req), f'{list(req)[:2]}')
    # counts match manual viable_storage_units
    manual = {}
    for c in cartons:
        shc = c.storage_handle_config
        for u in viable_storage_units(c, c.equilibrium_qty):
            manual[(shc.handling, shc.category, u.storage_size, u.unit_category)] = \
                manual.get((shc.handling, shc.category, u.storage_size, u.unit_category), 0) + 1
    check('counts equal manual viable_storage_units', req == manual,
          f'{req} vs {manual}')


def test_every_bucket_has_an_aisle():
    print('\n-- every bucket has >=1 aisle (floor) --')
    # 1-SKU inventory: demand touches almost no buckets, but the floor must
    # still create all 60.
    inv = _inventory(1, seed=1)
    plan = _plan(inv)
    sizes = ['small', 'medium', 'large', 'extra_large']
    missing = []
    for h in _HANDLINGS:
        for cat in _CATEGORIES:
            for s in sizes:
                if plan.capacity.get((h, cat, s, 'pallet'), 0) <= 0:
                    missing.append((h, cat, s, 'pallet'))
            if plan.capacity.get((h, cat, 'singleton', 'singleton'), 0) <= 0:
                missing.append((h, cat, 'singleton', 'singleton'))
    check('all 60 buckets present with >=1 aisle of capacity',
          not missing, f'missing {missing[:3]}')
    # 12 (h,c) × (4 pallet + 1 singleton) = 60 distinct buckets
    check('exactly 60 distinct buckets', len(plan.capacity) == 60,
          f'got {len(plan.capacity)}')


def test_every_sku_is_placeable():
    print('\n-- every sampled SKU is placeable, queue empties --')
    inv = _inventory(80, seed=3)
    plan = _plan(inv)
    Aisle.next_aisle_id = 1
    random.seed(3)
    wh  = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(plan.sampled)
    placed = {b.storage.carton.sku for b in mgr.unavailable if b.storage}
    missing = [c.sku for c in plan.sampled if c.sku not in placed]
    check('every sampled SKU has >=1 occupied bin', not missing,
          f'{len(missing)} unplaced, e.g. {missing[:3]}')
    check('queue empty after initial stock', mgr.queue_depth == 0,
          f'queue_depth={mgr.queue_depth}')


def test_uncapped_holds_full_demand():
    print('-- uncapped: capacity >= demand per bucket --')
    inv = _inventory(80, seed=5)
    plan = _plan(inv)
    # demand of the SAMPLED set must fit in capacity
    demand = Inventory_Manager.bucket_requirements(plan.sampled)
    over = [(b, demand[b], plan.capacity.get(b, 0))
            for b in demand if demand[b] > plan.capacity.get(b, 0)]
    check('every bucket capacity >= sampled demand', not over, f'{over[:3]}')


def test_capped_under_capacity():
    print('\n-- capped: at or under max_bins / max_aisles --')
    inv = _inventory(120, seed=9)
    # First get uncapped totals to choose a cap below them.
    base = _plan(inv)
    cap_bins   = int(base.total_bins * 0.6)
    cap_aisles = int(base.total_aisles * 0.7)
    plan = _plan(inv, max_bins=cap_bins, max_aisles=cap_aisles)
    check('total_bins <= max_bins', plan.total_bins <= cap_bins,
          f'{plan.total_bins} > {cap_bins}')
    check('total_aisles <= max_aisles', plan.total_aisles <= cap_aisles,
          f'{plan.total_aisles} > {cap_aisles}')
    # floor still intact — every bucket >=1 aisle
    floor_ok = all(plan.capacity.get((h, cat, s, 'pallet'), 0) > 0
                   for h in _HANDLINGS for cat in _CATEGORIES
                   for s in ['small', 'medium', 'large', 'extra_large'])
    check('floor preserved (every pallet bucket >=1) under caps', floor_ok)


def test_below_floor_clamps():
    print('\n-- cap below 60-aisle floor clamps to floor --')
    inv = _inventory(40, seed=11)
    plan = _plan(inv, max_aisles=10)   # impossible: 60-bucket floor
    check('aisles clamp to >= 60 floor (not 10)', plan.total_aisles >= 60,
          f'{plan.total_aisles}')
    check('every bucket still has an aisle',
          all(v > 0 for v in plan.capacity.values()))


def test_sampling_respects_bucket_capacity():
    print('\n-- sampling never exceeds per-bucket capacity --')
    inv = _inventory(150, seed=13)
    plan = _plan(inv)
    demand = Inventory_Manager.bucket_requirements(plan.sampled)
    over = [(b, demand[b], plan.capacity.get(b, 0))
            for b in demand if demand[b] > plan.capacity.get(b, 0)]
    check('sampled consumption <= capacity for every bucket', not over,
          f'{over[:3]}')


def test_resample_fills_empty_space():
    print('\n-- resampling fills empty space (natural tiers) --')
    # Small inventory vs the 60-bucket floor → must resample to fill.
    inv = _inventory(20, seed=17)
    eq_before = {c.sku: c.equilibrium_qty for c in inv.cartons}
    plan = _plan(inv)
    check('expected_fill never exceeds target',
          plan.expected_fill <= _TARGET + 0.02,
          f'{plan.expected_fill:.2%}')
    resampled = sum(1 for c in plan.sampled
                    if c.equilibrium_qty > eq_before.get(c.sku, c.equilibrium_qty))
    check('resampling occurred (>=1 SKU scaled up to fill space)',
          resampled >= 1, f'{resampled} scaled')


def test_resample_scales_eq_reorder():
    print('\n-- resample scales equilibrium_qty / reorder_point by N --')
    # One carton, capacity for many copies → it gets selected N>1 times.
    c = _make_carton(1, eq_qty=4)   # small so many fit
    eq0, rp0 = c.equilibrium_qty, c.reorder_point
    # capacity: 100 pallet bins of the tier this carton uses
    fp = {}
    for u in viable_storage_units(c, eq0):
        fp[(c.storage_handle_config.handling, c.storage_handle_config.category,
            u.storage_size, u.unit_category)] = fp.get((c.storage_handle_config.handling,
            c.storage_handle_config.category, u.storage_size, u.unit_category), 0) + 1
    capacity = {b: 100 for b in fp}     # lots of room
    sampled, allow = Inventory_Manager.sample_to_capacity(
        [c], capacity, target_fill=1.0, rng=random.Random(1))
    check('carton selected and scaled', sampled and sampled[0].equilibrium_qty > eq0,
          f'eq {eq0} -> {sampled[0].equilibrium_qty}')
    n = sampled[0].equilibrium_qty // eq0
    check('reorder_point scaled by same N',
          sampled[0].reorder_point == rp0 * n,
          f'rp {rp0} -> {sampled[0].reorder_point} (N={n})')


def test_restock_no_queue_growth():
    print('\n-- HEADLINE: restocks do not grow the queue --')
    inv = _inventory(100, seed=21)
    plan = _plan(inv)
    Aisle.next_aisle_id = 1
    random.seed(21)
    wh  = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(plan.sampled)

    rng = random.Random(99)
    skus = [c.sku for c in plan.sampled]
    depths: list[int] = []

    for _ in range(30):
        # "Pick a batch": fully deplete a random 25% of SKUs by emptying their bins.
        for sku in rng.sample(skus, max(1, len(skus) // 4)):
            bins = (list(mgr._sku_pallet_bins.get(sku, set())) +
                    list(mgr._sku_singleton_bins.get(sku, set())))
            for b in bins:
                if b.storage is not None:
                    qty = b.storage.quantity
                    b.storage = None
                    mgr._notify_bin_emptied(b)
                    mgr._notify_pick(sku, qty)
        mgr.check_reorders()
        depths.append(mgr.queue_depth)

    first_half  = mean(depths[:15])
    second_half = mean(depths[15:])
    total_bins  = plan.total_bins
    check('queue does not grow (2nd-half mean <= 1st-half mean + slack)',
          second_half <= first_half + 0.05 * total_bins,
          f'first={first_half:.0f} second={second_half:.0f} of {total_bins} bins')
    check('peak queue stays bounded (< 20% of bins)',
          max(depths) < 0.20 * total_bins,
          f'peak={max(depths)}  bins={total_bins}')


if __name__ == '__main__':
    print(f'\n{"="*64}')
    print(f'  Warehouse sizing + queue behavior tests')
    print(f'{"="*64}')

    test_bucket_requirements_track_size_tier()
    test_every_bucket_has_an_aisle()
    test_every_sku_is_placeable()
    test_uncapped_holds_full_demand()
    test_capped_under_capacity()
    test_below_floor_clamps()
    test_sampling_respects_bucket_capacity()
    test_resample_fills_empty_space()
    test_resample_scales_eq_reorder()
    test_restock_no_queue_growth()

    print(f'\n{"="*64}')
    if _FAIL == 0:
        print(f'  All {_PASS} checks passed.')
    else:
        print(f'  {_PASS} passed  {_FAIL} FAILED')
    print(f'{"="*64}\n')
    sys.exit(0 if _FAIL == 0 else 1)
