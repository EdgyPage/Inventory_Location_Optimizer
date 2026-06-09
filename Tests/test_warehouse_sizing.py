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
import tempfile
import types

from Aisle_Storage import Aisle
from Carton import Carton, StorageHandleConfig
from Demand import Demand
from generate_inventory import (
    build_inventory_with_profile, DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
    save_inventory_to_db, load_inventory_from_db, Inventory,
)
from Inventory_Management import (
    Inventory_Manager, _SIZE_RANKS, _max_qty_fitting_pallet_size,
)
from Assignment_Functions import (
    build_ranked_minimizing_assignment_fn, build_ranked_maximizing_assignment_fn,
    build_uniform_aisle_trip_min_assignment_fn,
)
from Storage_Primitive import viable_storage_units, Pallet
from Warehouse_Builder import Warehouse_Builder, WarehouseConfig, AisleConfig

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


def _plan_kw(inv, **kw):
    return Inventory_Manager.plan_warehouse(
        inv.cartons, categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=_AISLE_W, aisle_height=_AISLE_H, target_fill=_TARGET,
        rng=random.Random(1), **kw)


def test_min_bins_floor():
    print('\n-- min_bins: warehouse scaled up to a hard minimum --')
    inv  = _inventory(60, seed=5)
    base = _plan_kw(inv)                       # natural size
    target = base.total_bins * 3 + 20000       # well above natural
    plan = _plan_kw(inv, min_bins=target)
    check('total_bins >= min_bins', plan.total_bins >= target,
          f'{plan.total_bins} < {target}')
    check('every bucket still has >=1 aisle under min_bins',
          all(v > 0 for v in plan.capacity.values()))


def test_min_bins_overrides_max_bins():
    print('\n-- min_bins wins when it conflicts with max_bins --')
    inv  = _inventory(60, seed=5)
    plan = _plan_kw(inv, min_bins=30000, max_bins=5000)
    check('min_bins floor honored over a smaller max_bins',
          plan.total_bins >= 30000, f'{plan.total_bins}')


def test_composition_basis_vector():
    print('\n-- composition basis vector sets the bin-tier ratios --')
    from collections import defaultdict
    comp = {'unit': {'pallet': 0.7, 'singleton': 0.3},
            'size': {'small': 0.1, 'medium': 0.2, 'large': 0.3, 'extra_large': 0.4}}
    inv  = _inventory(60, seed=5)
    plan = _plan_kw(inv, min_bins=20000, composition=comp)
    bins = defaultdict(int)
    for (h, c, s, u), n in plan.capacity.items():
        bins['singleton' if u == 'singleton' else s] += n
    tot = sum(bins.values())
    check('min_bins respected with composition', tot >= 20000, f'{tot}')
    check('singleton ~30% of bins', abs(bins['singleton'] / tot - 0.30) < 0.05,
          f'{bins["singleton"]/tot:.1%}')
    pallet_tot = tot - bins['singleton']
    for size, exp in [('small', 0.1), ('medium', 0.2), ('large', 0.3), ('extra_large', 0.4)]:
        frac = bins[size] / pallet_tot
        check(f'{size} tier ~{exp:.0%} of pallet bins (basis vector)',
              abs(frac - exp) < 0.05, f'{frac:.1%}')


def test_sampling_respects_bucket_capacity():
    print('\n-- sampling never exceeds per-bucket capacity --')
    inv = _inventory(150, seed=13)
    plan = _plan(inv)
    demand = Inventory_Manager.bucket_requirements(plan.sampled)
    over = [(b, demand[b], plan.capacity.get(b, 0))
            for b in demand if demand[b] > plan.capacity.get(b, 0)]
    check('sampled consumption <= capacity for every bucket', not over,
          f'{over[:3]}')


def test_cross_tier_fill():
    print('\n-- cross-tier fill: flexible items fill ALL tiers --')
    from collections import defaultdict
    inv = _inventory(80, seed=5)
    eq_before = {c.sku: c.equilibrium_qty for c in inv.cartons}
    plan = _plan(inv)
    check('expected_fill never exceeds target',
          plan.expected_fill <= _TARGET + 0.02, f'{plan.expected_fill:.2%}')
    check('expected_fill reaches a high level (>=70%)',
          plan.expected_fill >= 0.70, f'{plan.expected_fill:.2%}')

    # Aggregate per-tier utilization from the assigned stock plans.
    dem = Inventory_Manager.bucket_requirements(plan.sampled)
    used = defaultdict(int); cap = defaultdict(int)
    for (h, c, s, u), n in dem.items():            used[s] += n
    for (h, c, s, u), n in plan.capacity.items():  cap[s]  += n
    # Every pallet tier should be meaningfully used (cross-tier fill working),
    # not just extra_large as with the old natural-only palletization.
    for tier in ['small', 'medium', 'large', 'extra_large']:
        frac = used[tier] / cap[tier] if cap[tier] else 0.0
        check(f'{tier} tier meaningfully filled (>=50%)', frac >= 0.50,
              f'{used[tier]}/{cap[tier]} = {frac:.0%}')

    resampled = sum(1 for c in plan.sampled
                    if c.equilibrium_qty > eq_before.get(c.sku, c.equilibrium_qty))
    check('resampling occurred (>=1 SKU grown to fill space)', resampled >= 1,
          f'{resampled} scaled')


def test_resample_scales_eq_reorder():
    print('\n-- resample grows equilibrium; reorder_point keeps its ratio; plan set --')
    # One carton, lots of capacity across all tiers it can reach → it grows.
    c = _make_carton(1, eq_qty=4)   # small footprint → reaches many tiers
    eq0, rp0 = c.equilibrium_qty, c.reorder_point
    f = rp0 / eq0
    # Give generous capacity for every tier this carton can reach.
    shc = c.storage_handle_config
    capacity = {}
    for size in ['small', 'medium', 'large', 'extra_large']:
        capacity[(shc.handling, shc.category, size, 'pallet')] = 100
    capacity[(shc.handling, shc.category, 'singleton', 'singleton')] = 100
    sampled, allow = Inventory_Manager.sample_to_capacity(
        [c], capacity, target_fill=1.0, rng=random.Random(1))
    sc = sampled[0]
    check('carton selected and grown to fill space', sampled and sc.equilibrium_qty > eq0,
          f'eq {eq0} -> {sc.equilibrium_qty}')
    expected_rp = max(1, min(sc.equilibrium_qty - 1, round(f * sc.equilibrium_qty)))
    check('reorder_point preserves original ratio',
          sc.reorder_point == expected_rp,
          f'rp={sc.reorder_point} expected {expected_rp}')
    check('stock_plan assigned (RLE slots sum to equilibrium_qty)',
          getattr(sc, 'stock_plan', None) and
          sum(per * count for _, per, count in sc.stock_plan) == sc.equilibrium_qty,
          f'plan={getattr(sc, "stock_plan", None)}')


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


def test_partial_placement_drains_incrementally():
    print('\n-- partial placement: queue drains as space frees, even if not all fit --')
    # Tiny singleton-only warehouse: exactly 6 singleton bins.
    Aisle.next_aisle_id = 1
    random.seed(0)
    w = aisle_width_for(2)   # 96 → 96//16 = 6 singleton columns
    cfg = WarehouseConfig(total_aisles=1, aisle_splits=[1.0],
        aisle_configs=[AisleConfig('conveyable', 'food', 'singleton', w, 48, ['singleton'], None)])
    wh  = Warehouse_Builder().from_config(cfg).build()
    mgr = Inventory_Manager(wh)
    n_bins = len(wh.bins)

    # A carton whose 10 units are singletons (more than the 6 bins).
    c = _make_carton(1, eq_qty=10)
    c.stock_plan = [(True, 1, 10)]       # 10 singleton units of 1 item each
    mgr.enqueue(c, quantity=10)

    placed = len(mgr.unavailable)
    check('partial: some units placed, not all (0 < placed < 10)',
          0 < placed < 10 and placed == n_bins,
          f'placed={placed} bins={n_bins} queue={mgr.queue_depth}')
    check('remaining units stay queued (10 - placed)',
          mgr.queue_depth == 10 - placed, f'queue={mgr.queue_depth}')

    # Free one bin; the next drain must place one more queued unit.
    before = mgr.queue_depth
    b = next(b for b in mgr.unavailable if b.storage is not None)
    b.storage = None
    mgr._notify_bin_emptied(b)
    mgr.check_reorders()
    check('freeing a bin lets one more queued unit place',
          mgr.queue_depth == before - 1, f'before={before} after={mgr.queue_depth}')


def test_unit_split_rescue():
    print('\n-- rescue: an oversized-tier unit splits into a free smaller tier --')
    # small-tier-only pallet warehouse (plentiful small bins; no medium/large/xl).
    Aisle.next_aisle_id = 1
    random.seed(0)
    w, h = aisle_width_for(2), aisle_height_for(4)
    cfg = WarehouseConfig(total_aisles=1, aisle_splits=[1.0],
        aisle_configs=[AisleConfig('conveyable', 'food', 'pallet', w, h, ['small'], None)])
    wh  = Warehouse_Builder().from_config(cfg).build()
    mgr = Inventory_Manager(wh)

    # A 48x48x12 carton makes a small pallet at 1 item, medium at 2, xl at 4 —
    # and fits a small pallet (small_per=1).  Force a >= medium pallet for which
    # the small-only warehouse has no bin.
    c = _make_carton(1, eq_qty=1, length=48, width=48, height=12)
    big_q, tier = None, None
    for q in range(1, 50):
        try:
            t = Pallet(c, q).storage_size
        except ValueError:
            break
        if _SIZE_RANKS[t] >= _SIZE_RANKS['medium']:
            big_q, tier = q, t
            break
    check('precondition: a higher-tier (>= medium) pallet exists, no matching bin',
          big_q is not None and _SIZE_RANKS[tier] >= _SIZE_RANKS['medium'],
          f'big_q={big_q} tier={tier}')
    c.stock_plan = [(False, big_q, 1)]
    mgr.enqueue(c, quantity=big_q)

    check('oversized-tier unit placed by splitting into the smaller tier',
          mgr.queue_depth == 0 and len(mgr.unavailable) >= 1,
          f'queue={mgr.queue_depth} placed={len(mgr.unavailable)}')
    # everything that landed is a small bin (the only tier that exists)
    tiers = {b.storage_size for b in mgr.unavailable if b.storage is not None}
    check('split units landed in the small tier', tiers <= {'small'}, f'tiers={tiers}')


def _run_batch_drain(build_fn, label: str):
    """Run a 30-batch pick+reorder loop with a ranked_assignment_fn set, asserting
    incremental drain (bounded queue) and BinKey-group integrity."""
    inv  = _inventory(80, seed=31)
    plan = _plan(inv)
    Aisle.next_aisle_id = 1
    random.seed(31)
    wh  = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(plan.sampled)
    check(f'[{label}] initial queue empty after stock', mgr.queue_depth == 0,
          f'queue={mgr.queue_depth}')

    # Wire a ranked_assignment_fn exactly like strategy_runner (null affinity).
    aff = types.SimpleNamespace(_matrix=None, _sku_to_idx={},
                                sum_lift=lambda skus: 0.0,
                                delta_lift_idxs=lambda s, idxs: 0.0)
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=1.0, pick_intercept=1.0,
                                pick_weight_coef=0.5, pick_volume_coef=0.5)
    mgr.ranked_assignment_fn = build_fn(
        aff, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        freq_by_idx={}, freq_by_sku={}, qty_by_sku={}, beta=1.0)

    rng = random.Random(7)
    skus = [c.sku for c in plan.sampled]
    depths: list[int] = []
    for _ in range(30):
        for sku in rng.sample(skus, max(1, len(skus) // 4)):
            bins = (list(mgr._sku_pallet_bins.get(sku, set())) +
                    list(mgr._sku_singleton_bins.get(sku, set())))
            for b in bins:
                if b.storage is not None:
                    qty = b.storage.quantity
                    b.storage = None
                    mgr._notify_bin_emptied(b)
                    mgr._notify_pick(sku, qty)
        mgr.check_reorders()             # uses _drain_ranked (ranked_assignment_fn set)
        depths.append(mgr.queue_depth)

    total_bins = plan.total_bins
    check(f'[{label}] queue drains incrementally (2nd-half <= 1st-half + slack)',
          mean(depths[15:]) <= mean(depths[:15]) + 0.05 * total_bins,
          f'first={mean(depths[:15]):.0f} second={mean(depths[15:]):.0f}')
    check(f'[{label}] peak queue bounded (< 20% of bins)',
          max(depths) < 0.20 * total_bins, f'peak={max(depths)} bins={total_bins}')

    # BinKey-group integrity: every placed unit sits in a bin of its own
    # (handling, category, unit_type) and a tier >= its own size (smallest-fit).
    violations = []
    for b in mgr.unavailable:
        u = b.storage
        if u is None:
            continue
        shc = u.carton.storage_handle_config
        if (b.handling_type != shc.handling or b.storage_type != shc.category
                or b.unit_type != u.unit_category):
            violations.append(('group', b.handling_type, b.storage_type, b.unit_type))
        elif u.unit_category == 'pallet' and u.storage_size is not None:
            if _SIZE_RANKS[b.storage_size] < _SIZE_RANKS[u.storage_size]:
                violations.append(('tier', b.storage_size, u.storage_size))
    check(f'[{label}] every placed unit stays within its BinKey group/tier',
          not violations, f'{violations[:3]}')


def test_planned_inventory_roundtrip_no_queue():
    print('\n-- planned inventory survives DB round-trip; workers reproduce it (no queue) --')
    # Mirrors the real multi-process flow: the main process plans (grown eq +
    # cross-tier stock plans), persists to a DB, and worker processes RELOAD from
    # that DB.  If the plan didn't survive, workers would palletize with the
    # default scheme and the queue would explode.
    inv  = _inventory(120, seed=5)
    plan = _plan(inv)
    planned = Inventory.__new__(Inventory)
    planned.cartons = plan.sampled
    db = os.path.join(tempfile.gettempdir(), 'planned_sizing_test.db')
    if os.path.exists(db):
        os.remove(db)
    try:
        save_inventory_to_db(planned, db, {'planned': True})
        reloaded = load_inventory_from_db(db)
    finally:
        if os.path.exists(db):
            os.remove(db)

    check('every planned SKU round-trips carrying a stock_plan',
          reloaded.cartons and all(getattr(c, 'stock_plan', None) for c in reloaded.cartons),
          f'{sum(1 for c in reloaded.cartons if getattr(c, "stock_plan", None))}'
          f'/{len(reloaded.cartons)}')
    eq_by_sku = {c.sku: c.equilibrium_qty for c in plan.sampled}
    check('grown equilibrium_qty preserved through the DB',
          all(c.equilibrium_qty == eq_by_sku[c.sku] for c in reloaded.cartons))

    # Worker flow: build the planned warehouse, enqueue the RELOADED cartons.
    Aisle.next_aisle_id = 1
    random.seed(5)
    wh  = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(reloaded.cartons)
    check('reloaded cartons place with NO queue (cross-tier plan reproduced)',
          mgr.queue_depth == 0, f'queue={mgr.queue_depth}')
    placed = {b.storage.carton.sku for b in mgr.unavailable if b.storage is not None}
    check('every reloaded SKU placed',
          all(c.sku in placed for c in reloaded.cartons),
          f'{len(placed)}/{len(reloaded.cartons)}')


def test_uniform_aisle_trip_min_assignment():
    print('\n-- uniform-aisle + trip-min-bin assignment fn --')
    inv  = _inventory(80, seed=5)
    plan = _plan(inv)
    Aisle.next_aisle_id = 1
    random.seed(5)
    wh  = Warehouse_Builder().from_config(plan.warehouse_cfg).build()
    mgr = Inventory_Manager(wh)
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=1.0)
    assign = build_uniform_aisle_trip_min_assignment_fn(wp, rng=random.Random(1))

    def W(b):
        return wp.x_speed * b.x_phys + wp.y_speed * b.y_phys

    # Find a unit whose candidate bins span >= 2 aisles.
    cand = None
    for c in plan.sampled:
        u = viable_storage_units(c, c.equilibrium_qty)[0]
        cu = mgr._candidates(u)
        if len({b.location[0] for b in cu}) >= 2:
            cand, unit = cu, u
            break
    check('found a unit whose candidates span >=2 aisles', cand is not None)
    if cand is None:
        return

    chosen_aisles, all_min = set(), True
    for _ in range(40):
        b = assign(unit, cand)
        aid = b.location[0]
        chosen_aisles.add(aid)
        in_aisle = [x for x in cand if x.location[0] == aid]
        if W(b) > min(W(x) for x in in_aisle) + 1e-9:
            all_min = False
    check('returns the MIN-travel-cost bin within the chosen aisle', all_min)
    check('aisle choice is non-degenerate (>=2 distinct aisles over 40 draws)',
          len(chosen_aisles) >= 2, f'{len(chosen_aisles)}')


def test_batch_assign_extremal_order():
    print('\n-- batch assign: highest priority -> lowest-W bin, no double-assign --')
    from collections import defaultdict

    class _B:
        __slots__ = ('location', 'x_phys', 'y_phys')
        def __init__(self, i):
            self.location = (1, i, 0)   # one aisle
            self.x_phys = i             # W = x_speed*x_phys (y_speed 0) = i
            self.y_phys = 0

    cands = [_B(i) for i in range(6)]
    freqs = [0.1, 0.9, 0.5, 0.7]        # sku i -> frequency
    units = [types.SimpleNamespace(carton=types.SimpleNamespace(
                sku=i, weight=1, volume=lambda: 1,
                demand=types.SimpleNamespace(frequency=f)))
             for i, f in enumerate(freqs)]
    aff = types.SimpleNamespace(_matrix=None, _sku_to_idx={})
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=0.0,        # W == x_phys
                                pick_intercept=1.0, pick_weight_coef=0.0, pick_volume_coef=0.0)
    fn = build_ranked_minimizing_assignment_fn(
        aff, wp, defaultdict(set), defaultdict(set), defaultdict(float),
        {}, {}, {}, beta=1.0)
    res = fn(units, lambda u: cands)

    by_sku = {u.carton.sku: b for u, b in res}
    # priority is pure frequency (effort constant) → highest freq gets W=0, etc.
    order = sorted(range(4), key=lambda i: freqs[i], reverse=True)   # [1,3,2,0]
    expected = {sku: rank for rank, sku in enumerate(order)}
    check('highest-priority unit gets lowest-W bin',
          all(by_sku[s].x_phys == expected[s] for s in range(4)),
          f'{[(s, by_sku[s].x_phys) for s in range(4)]}')
    ids = [id(b) for _, b in res]
    check('no bin assigned twice', len(ids) == len(set(ids)))


def _build_wh(plan, seed):
    Aisle.next_aisle_id = 1
    random.seed(seed)
    return Warehouse_Builder().from_config(plan.warehouse_cfg).build()


def test_optimal_layout_minimizes_sigma_fd():
    print('\n-- optimal layout: minimal Sigma f*D, monotone, fully placed --')
    from collections import defaultdict
    x, y = 1.0, 0.5
    inv  = _inventory(120, seed=11)
    plan = _plan(inv)
    freq = {c.sku: c.demand.frequency for c in plan.sampled}

    wh_u  = _build_wh(plan, 11)
    mgr_u = Inventory_Manager(wh_u)
    mgr_u.enqueue_all(plan.sampled)
    sig_u = mgr_u.current_sigma_fd(freq, x, y)

    wh_o  = _build_wh(plan, 11)
    mgr_o = Inventory_Manager(wh_o)
    opt   = mgr_o.place_optimal(plan.sampled, freq, x, y)
    sig_o = mgr_o.current_sigma_fd(freq, x, y)

    check('optimal Sigma f*D <= uniform', sig_o <= sig_u + 1e-6, f'opt={sig_o:.1f} uni={sig_u:.1f}')
    check('place_optimal return == realised current_sigma_fd', abs(opt - sig_o) < 1e-6,
          f'{opt:.3f} vs {sig_o:.3f}')

    # independent recompute validates current_sigma_fd
    indep = sum(freq.get(b.storage.carton.sku, 0.0) * (x * b.x_phys + y * b.y_phys)
                for b in wh_o.bins if b.storage is not None)
    check('current_sigma_fd matches independent sum', abs(indep - sig_o) < 1e-6,
          f'{indep:.3f} vs {sig_o:.3f}')

    # optimal_sigma_fd (no placement) == place_optimal value
    mgr_q = Inventory_Manager(_build_wh(plan, 11))
    q     = mgr_q.optimal_sigma_fd(plan.sampled, freq, x, y)
    check('optimal_sigma_fd (no mutation) == place_optimal', abs(q - opt) < 1e-6,
          f'{q:.3f} vs {opt:.3f}')

    # within each BinKey class, freq is non-increasing as W increases
    W = lambda b: x * b.x_phys + y * b.y_phys
    by_key = defaultdict(list)
    for b in wh_o.bins:
        if b.storage is not None:
            by_key[mgr_o._key(b)].append(b)
    mono = True
    for bins in by_key.values():
        bins.sort(key=W)
        fs = [freq.get(mgr_o._bin_sku[id(b)], 0.0) for b in bins]
        if any(fs[i] + 1e-9 < fs[i + 1] for i in range(len(fs) - 1)):
            mono = False
            break
    check('optimal: freq non-increasing as W rises (per BinKey)', mono)

    # everything placed (queue empty, all units occupy a bin)
    total_units = sum(len(viable_storage_units(c, c.equilibrium_qty)) for c in plan.sampled)
    occupied    = sum(1 for b in wh_o.bins if b.storage is not None)
    check('optimal places every unit', occupied == total_units,
          f'occupied={occupied} units={total_units}')


def test_requeue_bin():
    print('\n-- requeue_bin: frees bin, re-enqueues unit, preserves inventory position --')
    x, y = 1.0, 0.5
    plan = _plan(_inventory(120, seed=17))
    freq = {c.sku: c.demand.frequency for c in plan.sampled}
    wh   = _build_wh(plan, 17)
    mgr  = Inventory_Manager(wh)
    mgr.place_optimal(plan.sampled, freq, x, y)

    victim = next(b for b in wh.bins if b.unit_type == 'pallet' and b.storage is not None)
    sku    = mgr._bin_sku[id(victim)]
    qty    = victim.storage.quantity
    pos0   = (mgr._current_quantities.get(sku, 0) + mgr._queued_qty.get(sku, 0)
              + mgr._deferred_qty.get(sku, 0))
    onhand0, qlen0 = mgr._current_quantities.get(sku, 0), len(mgr._queue)
    mgr.pop_churn()
    mgr.requeue_bin(victim)
    rm, _ = mgr.pop_churn()

    check('requeue frees the bin', victim.storage is None and id(victim) not in mgr._unavailable)
    check('freed bin back in available index', id(victim) in mgr._bin_index_pos)
    check('unit re-enqueued', len(mgr._queue) == qlen0 + 1)
    check('on-hand decreased by qty', mgr._current_quantities.get(sku, 0) == onhand0 - qty)
    pos1 = (mgr._current_quantities.get(sku, 0) + mgr._queued_qty.get(sku, 0)
            + mgr._deferred_qty.get(sku, 0))
    check('inventory position unchanged (on-hand -> queued)', pos1 == pos0)
    check('requeue counts 1 reload move', rm == 1)


def test_capacity_reloader_variants():
    print('\n-- Capacity_Reloader: 3 named variants, per-aisle budget, pallet-only, lowers Sigma f*D --')
    from Capacity_Reloader import (promote_popular_reloader, demote_unpopular_reloader,
                                   rebalance_reloader, RELOADERS)
    from Assignment_Functions import build_ranked_minimizing_assignment_fn
    x, y = 1.0, 0.5
    inv  = _inventory(120, seed=23)
    plan = _plan(inv)
    freq = {c.sku:  c.demand.frequency for c in plan.sampled}
    neg  = {c.sku: -c.demand.frequency for c in plan.sampled}

    check('RELOADERS registry = 3 named variants',
          set(RELOADERS) == {'promote_popular', 'demote_unpopular', 'rebalance'})

    # per-variant: name, per-aisle budget respected, singletons untouched
    for make, nm in [(demote_unpopular_reloader, 'demote_unpopular'),
                     (promote_popular_reloader, 'promote_popular'),
                     (rebalance_reloader, 'rebalance')]:
        wh = _build_wh(plan, 23);  mgr = Inventory_Manager(wh)
        mgr.place_optimal(plan.sampled, neg, x, y)
        rl  = make(move_limit_pct=0.5)
        cap = rl.per_aisle_cap(wh)
        n_pallet_aisles = sum(1 for a in wh.aisles if a.unit_type == 'pallet')
        singles_before = {id(b) for b in wh.bins if b.unit_type == 'singleton' and b.storage is not None}
        mgr.pop_churn()
        rl.reload(mgr, freq, x, y)
        moves, _ = mgr.pop_churn()
        singles_after = {id(b) for b in wh.bins if b.unit_type == 'singleton' and b.storage is not None}
        check(f'{nm}: name attribute set', rl.name == nm)
        check(f'{nm}: evicts within per-aisle budget', 0 < moves <= cap * n_pallet_aisles,
              f'{moves} <= {cap}*{n_pallet_aisles}')
        check(f'{nm}: singleton bins untouched (pallet-only)', singles_before == singles_after)

    # rebalance + ranked re-drain lowers Sigma f*D from an anti-optimal layout
    wh  = _build_wh(plan, 23);  mgr = Inventory_Manager(wh, affinity=None)
    mgr.place_optimal(plan.sampled, neg, x, y)
    aff, _ = _aff_store([c.sku for c in plan.sampled], [])      # empty-lift store (co_occur=0)
    mgr._affinity = aff
    mgr.init_lift_state(aff);  mgr.init_demand_state(inv)
    fbs = {c.sku: c.demand.frequency    for c in plan.sampled}
    qbs = {c.sku: c.demand.quantity_rate for c in plan.sampled}
    wp  = type('wp', (), {'x_speed': x, 'y_speed': y, 'pick_intercept': 1.0,
                          'pick_weight_coef': 0.0, 'pick_volume_coef': 0.0})()
    mgr.ranked_assignment_fn = build_ranked_minimizing_assignment_fn(
        aff, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum, {}, fbs, qbs)
    sig0 = mgr.current_sigma_fd(freq, x, y)
    rl   = rebalance_reloader(move_limit_pct=0.5)
    for _ in range(40):
        rl.reload(mgr, freq, x, y)
        mgr.check_reorders()                                    # ranked drain re-places evicted
    sig1 = mgr.current_sigma_fd(freq, x, y)
    check('rebalance + ranked re-drain lowers Sigma f*D', sig1 < sig0, f'{sig1:.0f} < {sig0:.0f}')


def _aff_store(skus, pairs):
    """Build an in-memory AffinityStore with a hand-set symmetric CSR matrix.
    skus: ordered list; pairs: list of (sku_i, sku_j, lift)."""
    import numpy as np
    from scipy.sparse import csr_matrix
    from Affinity_Store import AffinityStore
    aff = AffinityStore(':memory:')
    idx = {s: i for i, s in enumerate(skus)}
    rows, cols, data = [], [], []
    for i, j, l in pairs:
        rows += [idx[i], idx[j]];  cols += [idx[j], idx[i]];  data += [l, l]
    aff._sku_to_idx = idx
    aff._matrix = csr_matrix((data, (rows, cols)), shape=(len(skus), len(skus)),
                             dtype=np.float32)
    return aff, idx


def test_cluster_assignment_max_min():
    print('\n-- cluster assignment: max co-locates, min scatters, W tie-break --')
    from collections import defaultdict
    from Assignment_Functions import (build_cluster_maximizing_assignment_fn,
                                      build_cluster_minimizing_assignment_fn)

    class _B:
        __slots__ = ('location', 'x_phys', 'y_phys')
        def __init__(self, aid, x):
            self.location = (aid, 0, 0);  self.x_phys = x;  self.y_phys = 0

    aff, idx = _aff_store([1, 2], [(1, 2, 5.0)])           # sku1 <-> sku2 lift 5
    wp = types.SimpleNamespace(x_speed=1.0, y_speed=0.0)
    fbi = {idx[2]: 1.0, idx[1]: 0.5}
    fbs = {1: 0.5, 2: 1.0};  qbs = {1: 1.0, 2: 1.0}
    cands = [_B(10, 9.0), _B(20, 1.0)]                     # aisle10 W=9 (has partner), aisle20 W=1
    unit = types.SimpleNamespace(carton=types.SimpleNamespace(sku=1))

    def state_with_partner():
        ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
        ss[10] = {2};  ii[10] = {idx[2]}                   # sku2 placed in aisle 10
        return ss, ii, dd

    ss, ii, dd = state_with_partner()
    b = build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    check('max_cluster co-locates with partner (aisle 10) despite higher W', b.location[0] == 10)

    ss, ii, dd = state_with_partner()
    b = build_cluster_minimizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    check('min_cluster scatters away from partner (aisle 20)', b.location[0] == 20)

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)   # no partner placed
    b = build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    check('max_cluster with no partners -> lowest-W bay (popularity tie-break)', b.location[0] == 20)


def test_affinity_sampler_correlates_and_guards():
    print('\n-- batch sampler: AffinityStore correlates co-picks, never silent uniform --')
    import random as _r
    from Workload_Builder import Batch, BatchConfig

    cartons = [_make_carton(i, 30) for i in range(1, 7)]   # conveyable/food
    for c in cartons:
        c.demand = Demand.from_rates(0.9, 4.0)             # freq 0.9 -> usually a candidate
    inv = types.SimpleNamespace(cartons=cartons)
    aff, _ = _aff_store([1, 2, 3, 4, 5, 6], [(1, 2, 8.0)]) # strong lift between sku1 & sku2
    cfg = BatchConfig(inventory_size=6, mean_fraction=0.5, std_fraction=0.0)

    def cooccur(affinity, n=400, seed=1):
        _r.seed(seed)
        both = sum(1 for _ in range(n)
                   if (lambda b: 1 in b.items and 2 in b.items)(Batch(cfg, inv, affinity=affinity)))
        return both / n

    p_aff, p_uni = cooccur(aff), cooccur(None)
    check('AffinityStore batches co-correlate sku1&2 above uniform',
          p_aff > p_uni + 0.05, f'aff={p_aff:.3f} uni={p_uni:.3f}')

    raised = False
    try:
        Batch(cfg, inv, affinity='not-an-affinity')
    except TypeError:
        raised = True
    check('Batch raises on unusable affinity (never silent uniform)', raised)


def test_cluster_skus_groups_high_lift():
    print('\n-- cluster_skus: high-lift pairs share a cluster, class-pure --')
    from affinity_cluster import cluster_skus
    cartons = [_make_carton(1, 10, category='food'),
               _make_carton(2, 10, category='food'),
               _make_carton(3, 10, category='food'),
               _make_carton(4, 10, category='clothing')]
    aff, _ = _aff_store([1, 2, 3, 4], [(1, 2, 6.0)])
    labels = cluster_skus(aff, cartons)
    check('high-lift pair shares a cluster', labels[1] == labels[2])
    check('isolated SKU is its own cluster', labels[3] != labels[1])
    check('cross-category SKU never co-clustered', labels[4] != labels[1])


def test_init_lift_state_populates_aisle_sets():
    print('\n-- init_lift_state fills aisle sku/idx sets from stock (placement sees stock) --')
    inv  = _inventory(80, seed=5)
    plan = _plan(inv)
    wh   = _build_wh(plan, 5)
    mgr  = Inventory_Manager(wh)                         # affinity=None during stock
    mgr.enqueue_all(plan.sampled)
    before = sum(1 for v in mgr._aisle_sku_sets.values() if v)
    check('aisle_sku_sets empty after affinity-free stock (the bug surface)', before == 0)

    aff, _ = _aff_store([c.sku for c in plan.sampled], [])   # sku_to_idx coverage, no pairs
    mgr._affinity = aff
    mgr.init_lift_state(aff)
    placed = {b.storage.carton.sku for b in wh.bins if b.storage is not None}
    in_sets = set().union(*mgr._aisle_sku_sets.values()) if mgr._aisle_sku_sets else set()
    check('init_lift_state fills aisle_sku_sets with every placed SKU', in_sets == placed,
          f'{len(in_sets)} vs {len(placed)}')
    check('init_lift_state fills aisle_idx_sets (cohesion sees stock)',
          sum(len(v) for v in mgr._aisle_idx_sets.values()) > 0)


def test_incremental_sigma_fd_matches_full():
    print('\n-- incremental Sigma f*D tracks full recompute through place/empty/evict --')
    x, y = 1.0, 0.5
    plan = _plan(_inventory(120, seed=29))
    freq = {c.sku: c.demand.frequency for c in plan.sampled}
    wh   = _build_wh(plan, 29)
    mgr  = Inventory_Manager(wh)
    mgr.enqueue_all(plan.sampled)
    mgr.enable_sigma_fd(freq, x, y)
    check('tracked == full after enable',
          abs(mgr.tracked_sigma_fd() - mgr.current_sigma_fd(freq, x, y)) < 1e-6)

    # pick-empty path (mirrors fast_pick phase 2: clear storage, then notify)
    victim = next(b for b in wh.bins if b.storage is not None)
    victim.storage = None
    mgr._notify_bin_emptied(victim)
    check('tracked == full after a bin-empty',
          abs(mgr.tracked_sigma_fd() - mgr.current_sigma_fd(freq, x, y)) < 1e-6)

    # eviction path: requeue a pallet (-), then re-drain re-places it (+)
    v2 = next(b for b in wh.bins if b.storage is not None and b.unit_type == 'pallet')
    mgr.requeue_bin(v2)
    check('tracked == full after eviction',
          abs(mgr.tracked_sigma_fd() - mgr.current_sigma_fd(freq, x, y)) < 1e-6)
    mgr._drain()
    check('tracked == full after re-drain',
          abs(mgr.tracked_sigma_fd() - mgr.current_sigma_fd(freq, x, y)) < 1e-6)


def test_batch_min_incremental_drain():
    print('\n-- batch-minimizing: subsectioned drain stays bounded & in-group --')
    _run_batch_drain(build_ranked_minimizing_assignment_fn, 'min')


def test_batch_max_incremental_drain():
    print('\n-- batch-maximizing: subsectioned drain stays bounded & in-group --')
    _run_batch_drain(build_ranked_maximizing_assignment_fn, 'max')


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
    test_min_bins_floor()
    test_min_bins_overrides_max_bins()
    test_composition_basis_vector()
    test_sampling_respects_bucket_capacity()
    test_cross_tier_fill()
    test_resample_scales_eq_reorder()
    test_restock_no_queue_growth()
    test_partial_placement_drains_incrementally()
    test_unit_split_rescue()
    test_planned_inventory_roundtrip_no_queue()
    test_uniform_aisle_trip_min_assignment()
    test_batch_assign_extremal_order()
    test_optimal_layout_minimizes_sigma_fd()
    test_requeue_bin()
    test_capacity_reloader_variants()
    test_cluster_assignment_max_min()
    test_affinity_sampler_correlates_and_guards()
    test_cluster_skus_groups_high_lift()
    test_init_lift_state_populates_aisle_sets()
    test_incremental_sigma_fd_matches_full()
    test_batch_min_incremental_drain()
    test_batch_max_incremental_drain()

    print(f'\n{"="*64}')
    if _FAIL == 0:
        print(f'  All {_PASS} checks passed.')
    else:
        print(f'  {_PASS} passed  {_FAIL} FAILED')
    print(f'{"="*64}\n')
    sys.exit(0 if _FAIL == 0 else 1)
