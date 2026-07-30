"""Equivalence + property tests for the map/cluster_map placement fast paths.

These guard the performance rewrite that replaced the O(free-bins-per-tier) closest-pref
scan (map family) and the per-aisle affinity CSR re-slice (cohesion family) with:

  * ``_PrefPool`` — a wave-local pref-sorted pool answering closest-to-target in O(log B)
    with O(alpha) consumption (union-find alive chains), and
  * ``_affinity_row`` / ``_delta_lift_from_row`` — one CSR slice per unit reused across aisles.

Locked-in invariants:
  1. ``_closest_abs`` == brute-force ``min|p-target|``.
  2. ``_PrefPool`` queries return the same bin the linear scan would, never double-hand a bin,
     and exhaust to None.
  3. ``_delta_lift_from_row`` is bit-for-bit ``_demand_weighted_delta_lift`` (hoist is a no-op
     on results).
  4. ``_cluster_map_choose_aisle``'s lazy argmax == the reference ``max(live,(lift,-gap))``.
  5. The ``map`` ranked WAVE gives identical aisle-level placement to the per-unit SCAN across a
     full reorder+pick sim, and keeps the reorder queue bounded.

Run:  python -m pytest Tests/unit/test_placement_fastpath_equivalence.py -q
"""
import random

import pytest

from Warehouse.placement.Assignment_Functions import (
    _closest_abs, _PrefPool, _affinity_row, _delta_lift_from_row,
    _aisle_anchor_gap, _cluster_map_choose_aisle, _demand_weighted_delta_lift,
    build_optmap_fn, build_optmap_wave_fn,
)


# ── synthetic bins (only the attrs _PrefPool / build_optimal_map's pref reads) ──
class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location = (aid,)
        self.x_phys   = x
        self.y_phys   = y


# ────────────────────────────── _closest_abs ──────────────────────────────────
def test_closest_abs_matches_bruteforce():
    rng = random.Random(1)
    for _ in range(3000):
        n = rng.randint(0, 12)
        prefs = sorted(rng.uniform(-5, 5) for _ in range(n))
        target = rng.uniform(-6, 6)
        got = _closest_abs(prefs, target)
        exp = min((abs(p - target) for p in prefs), default=float('inf'))
        assert got == exp, (prefs, target, got, exp)


# ─────────────────────────────── _PrefPool ────────────────────────────────────
def _random_pool(rng, n, tie=False):
    """A pool of n bins + its pref map.  tie=True forces many equal prefs (stress ties)."""
    bins = [_Bin(rng.randint(1, 4), rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]
    if tie:
        pref = {id(b): float(rng.randint(0, 3)) for b in bins}   # heavy collisions
    else:
        pref = {id(b): rng.uniform(-10, 10) for b in bins}
    return bins, pref


def test_prefpool_take_closest_is_min_abs():
    rng = random.Random(2)
    for trial in range(400):
        bins, pref = _random_pool(rng, rng.randint(1, 30), tie=(trial % 3 == 0))
        pool  = _PrefPool(bins, pref)
        alive = list(bins)
        for _ in range(len(bins)):
            target = rng.uniform(-12, 12)
            got = pool.take_closest(target)
            best = min(abs(pref[id(b)] - target) for b in alive)
            assert abs(pref[id(got)] - target) == best        # a true minimiser
            assert got in alive                               # still alive when handed out
            alive.remove(got)                                 # and consumed exactly once
        assert pool.take_closest(0.0) is None                 # exhausted


def test_prefpool_take_ge_semantics():
    rng = random.Random(3)
    for trial in range(400):
        bins, pref = _random_pool(rng, rng.randint(1, 30), tie=(trial % 3 == 0))
        pool  = _PrefPool(bins, pref)
        alive = list(bins)
        for _ in range(len(bins)):
            target = rng.uniform(-12, 12)
            got = pool.take_ge(target)
            eligible = [b for b in alive if pref[id(b)] >= target]
            if eligible:
                assert pref[id(got)] >= target
                assert pref[id(got)] == min(pref[id(b)] for b in eligible)   # smallest qualifying
            else:
                assert pref[id(got)] == max(pref[id(b)] for b in alive)      # least-prime fallback
            alive.remove(got)
        assert pool.take_ge(0.0) is None


def test_prefpool_min_max_and_no_double_take():
    rng = random.Random(4)
    for _ in range(300):
        bins, pref = _random_pool(rng, rng.randint(1, 25))
        pool = _PrefPool(bins, pref)
        seen = set()
        want_min = sorted(bins, key=lambda b: pref[id(b)])
        for expect in want_min:                     # take_min drains in ascending pref
            got = pool.take_min()
            assert pref[id(got)] == pref[id(expect)]
            assert id(got) not in seen
            seen.add(id(got))
        assert len(seen) == len(bins)               # every bin exactly once
        assert pool.take_min() is None and pool.take_max() is None

        pool2 = _PrefPool(bins, pref)
        for expect in reversed(want_min):           # take_max drains in descending pref
            assert pref[id(pool2.take_max())] == pref[id(expect)]


# ───────────────────── affinity row hoist (Tier 2) ─────────────────────────────
def _ref_delta_lift(aff, sku, member, freq_by_idx):
    """Independent brute-force Σ(lift-1)·f, walking the raw CSR row (NOT via the
    production helpers) — so this genuinely validates _affinity_row/_delta_lift_from_row
    rather than tautologically re-running them."""
    m = aff._matrix
    if m is None or sku not in aff._sku_to_idx:
        return 0.0
    i = aff._sku_to_idx[sku]
    s, e = int(m.indptr[i]), int(m.indptr[i + 1])
    total = 0.0
    for ci, d in zip(m.indices[s:e], m.data[s:e]):
        if int(ci) in member:
            total += (float(d) - 1.0) * freq_by_idx.get(int(ci), 0.0)
    return total


def test_delta_lift_from_row_matches_demand_weighted():
    from perf_simulation import _build_inventory, _build_affinity_store
    inv = _build_inventory(600, seed=7)
    aff = _build_affinity_store(inv, top_k=20, seed=7)
    idx_of = aff._sku_to_idx
    all_idx = list(idx_of.values())
    rng = random.Random(9)
    freq_by_idx = {i: rng.uniform(0, 5) for i in all_idx}

    skus = [c.sku for c in inv.orders if c.sku in idx_of]
    checked = 0
    for sku in skus:
        row = _affinity_row(aff, sku)
        for _ in range(4):
            k = rng.randint(0, min(40, len(all_idx)))
            member = set(rng.sample(all_idx, k))
            got = _delta_lift_from_row(row, member, freq_by_idx)
            exp = _ref_delta_lift(aff, sku, member, freq_by_idx)
            # exact when the row is the iterated (smaller) side; else summation order differs,
            # so allow float-add-reassociation slop.  Also assert the production delegate agrees.
            assert got == pytest.approx(exp, rel=1e-12, abs=1e-12), (sku, got, exp)
            assert _demand_weighted_delta_lift(aff, sku, member, freq_by_idx) == got
            checked += 1
    assert checked > 100          # the fixture actually exercised the path


# ───────────────────── cluster_map lazy choose-aisle (Tier 1) ──────────────────
def _ref_choose_aisle(by_aisle, pref, row, aisle_idx_sets, freq_by_idx, target):
    """Reference: the eager ``argmax (lift, -anchor_gap)`` the lazy version replaced."""
    live = [aid for aid, lst in by_aisle.items() if lst]
    if not live:
        return None

    def key(a):
        lift = _delta_lift_from_row(row, aisle_idx_sets[a], freq_by_idx)
        gap  = _aisle_anchor_gap(by_aisle[a], pref, target)
        return (lift, -gap)
    return max(live, key=key)


def test_cluster_map_choose_aisle_matches_reference():
    rng = random.Random(11)
    for trial in range(1500):
        n_aisles = rng.randint(1, 6)
        by_aisle, pref, aisle_idx_sets = {}, {}, {}
        for aid in range(1, n_aisles + 1):
            lst = [_Bin(aid, rng.uniform(0, 50), rng.uniform(0, 50))
                   for _ in range(rng.randint(0, 4))]     # some aisles may be empty (dead)
            for b in lst:
                # ints in a small band → force pref ties across aisles (tie-break stress)
                pref[id(b)] = float(rng.randint(0, 4))
            by_aisle[aid] = lst
            aisle_idx_sets[aid] = set(rng.sample(range(30), rng.randint(0, 8)))
        prefs_by_aisle = {aid: sorted(pref.get(id(b), 0.0) for b in lst)
                          for aid, lst in by_aisle.items()}
        # small partner row with collisions into the aisle idx-sets → non-trivial lift ties
        row = {i: float(rng.randint(1, 3)) for i in rng.sample(range(30), rng.randint(0, 10))}
        freq_by_idx = {i: float(rng.randint(0, 3)) for i in range(30)}
        target = None if trial % 5 == 0 else rng.uniform(0, 5)

        got = _cluster_map_choose_aisle(by_aisle, prefs_by_aisle, row,
                                        aisle_idx_sets, freq_by_idx, target)
        exp = _ref_choose_aisle(by_aisle, pref, row, aisle_idx_sets, freq_by_idx, target)
        assert got == exp, (trial, got, exp)


# ───────────────────── map wave == per-unit scan (end-to-end) ──────────────────
SEED, N_SKUS, BINS_PER_AISLE, N_BATCHES = 42, 1500, 100, 40


def _build_map_mgr(wh_cfg, inventory, wp, ranked):
    """A map manager placing via the ranked WAVE (ranked=True) or the per-unit SCAN."""
    from Warehouse.layout.Aisle_Storage import Aisle
    from Warehouse.inventory.Inventory_Management import Inventory_Manager, Placement
    Aisle.next_aisle_id = 1
    random.seed(SEED)
    from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
    wh  = Warehouse_Builder().from_config(wh_cfg).build()
    mgr = Inventory_Manager(wh, affinity=None)
    # _build_inventory orders carry no reorder_point (1 unit/SKU stock); set it to 0 so a
    # single pick depletes and fires a reorder — a high-churn regime that heavily exercises
    # the map placement path (thousands of reorder placements over the run).
    for o in inventory.orders:
        o.reorder_point = 0
    random.seed(SEED + 1)
    mgr.enqueue_all(inventory.orders)
    freq_by_sku = {c.sku: c.demand.relative_frequency for c in inventory.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate      for c in inventory.orders}
    mgr.build_optimal_map(inventory.orders, freq_by_sku, qty_by_sku, wp)
    if ranked:
        mgr.placement = Placement('optmap', build_optmap_fn(mgr), build_optmap_wave_fn(mgr))
    else:
        mgr.placement = Placement('optmap', build_optmap_fn(mgr))
    return wh, mgr


def _aisle_sku_state(mgr):
    """Aisle-aggregated SKU occupancy from the ALWAYS-populated occupied-bin maps
    (_aisle_sku_counts is gated on affinity, which map placement doesn't use).  Bin
    identity may differ across managers on exact ties; the aisle rollup is what feeds
    reorders back, so that's the equivalence we assert."""
    counts: dict = {}
    for bid, b in mgr._unavailable.items():
        sku = mgr._bin_sku.get(bid)
        counts.setdefault(b.location[0], {})
        counts[b.location[0]][sku] = counts[b.location[0]].get(sku, 0) + 1
    return {aid: c for aid, c in counts.items() if c}


def _run_map(wh, mgr, pick_cfg, batch_cfg, inventory):
    from Warehouse.picking.Pick import PickSimulation
    from Warehouse.picking.Workload_Builder import Batch, Task
    random.seed(SEED + 100)
    max_depth = 0
    base_placements = mgr._reorder_placements       # counts every placement (initial incl.)
    for _ in range(N_BATCHES):
        mgr.check_reorders()
        max_depth = max(max_depth, len(mgr._stock_queue))
        batch = Batch(batch_cfg, inventory, affinity=None)
        tasks = Task.from_batch(batch, wh, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()
    reorders = mgr._reorder_placements - base_placements
    return _aisle_sku_state(mgr), max_depth, reorders


@pytest.fixture(scope='module')
def map_assets():
    from perf_simulation import _build_inventory, _build_warehouse_cfg
    from Warehouse.picking.Pick import PickConfig
    from Optimization.metrics.Workload import WorkloadParams
    from Warehouse.picking.Workload_Builder import BatchConfig
    inventory = _build_inventory(N_SKUS, SEED)
    wh_cfg    = _build_warehouse_cfg(N_SKUS, BINS_PER_AISLE)
    pick_cfg  = PickConfig(num_pickers=5, x_speed=1.0, y_speed=0.5,
                           pick_intercept=1.0, pick_weight_coef=1.1,
                           pick_volume_coef=1e-3, cart_swap_coef=10.0)
    wp        = WorkloadParams.from_pick_config(pick_cfg)
    batch_cfg = BatchConfig(inventory_size=N_SKUS, mean_fraction=0.05, std_fraction=0.01)
    return inventory, wh_cfg, pick_cfg, wp, batch_cfg


def test_map_wave_matches_per_unit_scan(map_assets):
    """The ranked wave and the per-unit scan are both exact closest-pref minimisers, so a full
    reorder+pick sim lands SKUs in the same aisles (bin identity may differ only on exact ties)."""
    inventory, wh_cfg, pick_cfg, wp, batch_cfg = map_assets
    wh1, mgr1 = _build_map_mgr(wh_cfg, inventory, wp, ranked=False)
    wh2, mgr2 = _build_map_mgr(wh_cfg, inventory, wp, ranked=True)
    assert mgr1.placement.is_ranked is False
    assert mgr2.placement.is_ranked is True

    scan_counts, _, scan_reord = _run_map(wh1, mgr1, pick_cfg, batch_cfg, inventory)
    wave_counts, _, wave_reord = _run_map(wh2, mgr2, pick_cfg, batch_cfg, inventory)
    # Not vacuous: bins are occupied and the map placement actually ran on reorders.
    assert scan_counts and scan_reord > 0, (len(scan_counts), scan_reord)
    assert wave_reord > 0
    assert scan_counts == wave_counts


def test_map_queue_stays_bounded(map_assets):
    """The ranked wave sheds its per-tier snapshot surplus to the straggler path, so the reorder
    queue never grows without bound across many batches."""
    inventory, wh_cfg, pick_cfg, wp, batch_cfg = map_assets
    wh, mgr = _build_map_mgr(wh_cfg, inventory, wp, ranked=True)
    _counts, max_depth, reorders = _run_map(wh, mgr, pick_cfg, batch_cfg, inventory)
    assert reorders > 0                     # the wave path was actually exercised
    # A leak would make depth climb monotonically with batches; assert it stays modest.
    assert max_depth <= N_SKUS, f'reorder queue depth {max_depth} looks unbounded'
