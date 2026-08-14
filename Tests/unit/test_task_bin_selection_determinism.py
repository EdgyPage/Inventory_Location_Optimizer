"""test_task_bin_selection_determinism.py

`Task.from_batch` decides WHICH bins a batch drains.  It reads the manager's
`_sku_singleton_bins` / `_sku_pallet_bins` index, which is `dict[int, set[Aisle.Bin]]` —
and `Aisle.Bin` defines no `__hash__`/`__eq__`, so those sets hash by IDENTITY and iterate
in memory-address order.  Whenever a SKU has more on-hand bins than the batch quantity
consumes (the normal case) that order picks the winners, and under `spawn` + ASLR the
addresses differ per process.  Two identical-seed runs of the same arm therefore used to
drain different bins → different aisles → different tasks → different travel and makespan
→ different depletion → a different reorder cascade, i.e. different `batch_stats`.

The fix is that `Task.from_batch` imposes its OWN order (`sorted(..., key=b.location)`)
instead of inheriting the index's.  These tests pin that:

  A  the decomposition is invariant to the order the index hands bins over — checked
     against seeded permutations INCLUDING the exact reverse of the canonical order;
  B  the bins drained are the lowest-`location` ones, including the partial final take;
  C  singleton bins still drain before pallet bins whatever order the index yields;
  D  the real `Inventory_Manager` (real identity-hashed `set`s) agrees with an
     independently computed canonical expectation.

D is what makes the suite non-vacuous: the expectation is derived here from
`sorted(bins, key=lambda b: b.location)`, never from the production helper, so a
regression in `Workload_Builder` cannot move the goalposts with it.

Usage
-----
    python -m pytest Tests/unit/test_task_bin_selection_determinism.py -q
"""
from __future__ import annotations

import math
import random

from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.picking.Workload_Builder import Batch, Task

# Travel distances are floats; nothing here compares them with `==`.
_TRAVEL_TOL = 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

def _warehouse() -> Warehouse_Builder:
    """Four small aisles — two singleton, two pallet — so one SKU spreads over
    several bins in several aisles.  `Aisle.next_aisle_id` is class state and MUST
    be reset or aisle ids (hence `location`, hence every expectation here) drift
    with test ordering."""
    Aisle.next_aisle_id = 1
    random.seed(0)
    w = aisle_width_for(4)
    h = aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=4,
        aisle_splits=[0.25] * 4,
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
            AisleConfig('conveyable', 'food', 'pallet',    w, h, ['medium'], None),
            AisleConfig('conveyable', 'food', 'pallet',    w, h, ['medium'], None),
        ],
    )
    return Warehouse_Builder().from_config(cfg).build()


def _order(sku: int, eq_qty: int) -> Order:
    """A minimal stocked Order.  Built with `object.__new__` (the idiom
    `test_equilibrium_reorder` already uses) so the physical fields are FIXED —
    `Order.__init__` samples them from the global RNG, which would make the bin
    count, and therefore every location expectation below, seed-dependent."""
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length                = 8
    c.width                 = 8
    c.height                = 6
    c.weight                = 2
    c.demand                = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty       = eq_qty
    c.reorder_point         = 1
    c.lead_time_mean        = 0.0
    c.supply_cv             = 0.0
    c.expected_batch_demand = 3.2
    return c


def _stocked(skus: dict[int, int]):
    """Build a warehouse and stock it; return (warehouse, manager)."""
    wh  = _warehouse()
    mgr = Inventory_Manager(wh)
    for sku, eq_qty in skus.items():
        mgr.enqueue(_order(sku, eq_qty))
    return wh, mgr


def _batch(items: dict[int, int]) -> Batch:
    """A Batch carrying an EXACT item map.  `Batch.__init__` samples both the SKU set
    and the quantities from an RNG; `Task.from_batch` reads only `.items`, so the
    sampling is bypassed to keep the expectations exact."""
    b = object.__new__(Batch)
    b.items = dict(items)
    b.aff   = {}
    return b


class _StubIndexManager:
    """Stand-in exposing only what `Task.from_batch` reads off a manager: the two
    SKU→bins indexes.  Holding LISTS rather than sets is the whole point — it lets a
    test hand the production code a chosen, adversarial iteration order, which a real
    identity-hashed `set` cannot be made to do reproducibly."""

    def __init__(self,
                 singleton: dict[int, list[Aisle.Bin]],
                 pallet: dict[int, list[Aisle.Bin]]) -> None:
        self._sku_singleton_bins = singleton
        self._sku_pallet_bins    = pallet


# ── decomposition fingerprint ────────────────────────────────────────────────

def _fingerprint(tasks: list[Task]) -> tuple:
    """Everything about a decomposition that a downstream metric can see: which aisle,
    which bins in which visit order, what is picked there, and how far the picker walks.
    Two runs agreeing on this agree on travel, makespan and depletion."""
    return tuple(sorted(
        (t.aisle_id,
         tuple(b.location for b in t.path),
         tuple(sorted(t.items.items())),
         t.carts_required)
        for t in tasks
    ))


def _travel(tasks: list[Task]) -> tuple[float, float]:
    return (sum(t.x_traversed for t in tasks), sum(t.y_traversed for t in tasks))


def _canonical_takes(bins: list[Aisle.Bin], qty: int) -> dict[tuple[int, int, int], int]:
    """The expected {location: units taken}, derived HERE from `location` order — never
    from `Workload_Builder`'s own helper, so this cannot drift along with a regression."""
    takes: dict[tuple[int, int, int], int] = {}
    remaining = qty
    for b in sorted(bins, key=lambda x: x.location):
        if remaining <= 0:
            break
        available = b.storage.quantity if b.storage is not None else 0
        take = min(remaining, available)
        if take > 0:
            takes[b.location] = take
            remaining -= take
    return takes


def _permutations(bins: list[Aisle.Bin], n: int, seed: int) -> list[list[Aisle.Bin]]:
    """`n` seeded shuffles, plus the two orders most likely to expose an unsorted drain:
    the canonical one and its exact reverse."""
    rng = random.Random(seed)
    canonical = sorted(bins, key=lambda b: b.location)
    orders = [canonical, list(reversed(canonical))]
    for _ in range(n):
        shuffled = list(bins)
        rng.shuffle(shuffled)
        orders.append(shuffled)
    return orders


# ══════════════════════════════════════════════════════════════════════════════
# A — the decomposition does not depend on the index's iteration order
# ══════════════════════════════════════════════════════════════════════════════

def test_decomposition_is_invariant_to_index_order() -> None:
    """Same bins, same quantities, 12 different iteration orders → one decomposition.

    Reverse-location order is included explicitly: with an unsorted drain it selects the
    HIGHEST-location bins, the exact complement of the canonical selection, so this
    assertion cannot pass by coincidence.
    """
    wh, mgr = _stocked({1: 60})
    singles = list(mgr._sku_singleton_bins.get(1, set()))
    pallets = list(mgr._sku_pallet_bins.get(1, set()))
    # The premise of the whole bug: strictly more bins on hand than the batch consumes.
    assert len(pallets) >= 4, f'fixture too small to exercise selection: {len(pallets)} pallet bins'

    batch = _batch({1: 20})
    seen_fingerprints = set()
    seen_travel = []
    for order in _permutations(pallets, n=10, seed=4242):
        stub  = _StubIndexManager({1: list(singles)}, {1: order})
        tasks = Task.from_batch(batch, wh, manager=stub)
        seen_fingerprints.add(_fingerprint(tasks))
        seen_travel.append(_travel(tasks))

    assert len(seen_fingerprints) == 1, (
        f'{len(seen_fingerprints)} distinct decompositions across 12 index orders; '
        f'bin selection still follows the index')

    x0, y0 = seen_travel[0]
    for x, y in seen_travel[1:]:
        assert math.isclose(x, x0, rel_tol=0.0, abs_tol=_TRAVEL_TOL)
        assert math.isclose(y, y0, rel_tol=0.0, abs_tol=_TRAVEL_TOL)


# ══════════════════════════════════════════════════════════════════════════════
# B — the bins drained are the lowest-location ones
# ══════════════════════════════════════════════════════════════════════════════

def test_drains_the_lowest_location_bins_including_the_partial_take() -> None:
    """Invariance alone would be satisfied by any fixed rule; pin the actual one.

    The quantity is chosen to land MID-BIN so the last bin is only partly drained —
    the case where an off-by-one in the drain loop would otherwise hide.
    """
    wh, mgr = _stocked({1: 60})
    singles = sorted(mgr._sku_singleton_bins.get(1, set()), key=lambda b: b.location)
    pallets = sorted(mgr._sku_pallet_bins.get(1, set()),    key=lambda b: b.location)

    single_stock = sum(b.storage.quantity for b in singles)
    # Drain every singleton, then 1.5 pallet bins' worth.
    qty = single_stock + pallets[0].storage.quantity + max(1, pallets[1].storage.quantity // 2)

    expected = dict(_canonical_takes(singles, qty))
    remaining = qty - sum(expected.values())
    expected.update(_canonical_takes(pallets, remaining))
    assert len(expected) < len(singles) + len(pallets), (
        'quantity drains every bin — the test would not distinguish any ordering')

    tasks = Task.from_batch(_batch({1: qty}), wh, manager=mgr)
    selected = {b.location for t in tasks for b in t.path}
    assert selected == set(expected), (
        f'drained {sorted(selected)}, expected the lowest-location set {sorted(expected)}')

    # Per-aisle totals must match the per-bin takes summed by aisle.
    by_aisle: dict[int, int] = {}
    for loc, take in expected.items():
        by_aisle[loc[0]] = by_aisle.get(loc[0], 0) + take
    actual_by_aisle = {t.aisle_id: t.items[1] for t in tasks}
    assert actual_by_aisle == by_aisle, f'{actual_by_aisle} != {by_aisle}'
    assert sum(actual_by_aisle.values()) == qty


# ══════════════════════════════════════════════════════════════════════════════
# C — singleton-before-pallet survives the sort
# ══════════════════════════════════════════════════════════════════════════════

def test_singletons_still_drain_before_pallets() -> None:
    """Forward-pick before reserve is the documented contract of `Task.from_batch`;
    ordering within each index must not leak across them.  The stub hands the pallet
    bins over first and in reverse-location order — the most hostile arrangement.
    """
    wh, mgr = _stocked({1: 60})
    singles = list(mgr._sku_singleton_bins.get(1, set()))
    pallets = sorted(mgr._sku_pallet_bins.get(1, set()), key=lambda b: b.location, reverse=True)
    single_stock = sum(b.storage.quantity for b in singles)
    assert single_stock > 0

    # Ask for slightly more than the singletons hold: every singleton must be drained,
    # and exactly one pallet bin (the lowest-location one) tops the batch up.
    qty = single_stock + 1
    tasks = Task.from_batch(_batch({1: qty}), wh, manager=_StubIndexManager(
        {1: singles}, {1: pallets}))
    selected = {b.location for t in tasks for b in t.path}

    for b in singles:
        assert b.location in selected, f'singleton {b.location} not drained before pallets'
    lowest_pallet = min(pallets, key=lambda b: b.location)
    assert lowest_pallet.location in selected, (
        f'top-up came from {sorted(selected - {b.location for b in singles})}, '
        f'not the lowest-location pallet bin {lowest_pallet.location}')


# ══════════════════════════════════════════════════════════════════════════════
# D — the real manager's identity-hashed sets agree with the canonical expectation
# ══════════════════════════════════════════════════════════════════════════════

def test_real_manager_matches_the_canonical_expectation() -> None:
    """The production path, with real `set[Aisle.Bin]` indexes.

    Also builds a SECOND, logically identical warehouse in the same process.  Its bins
    are different objects at different addresses, so its sets iterate differently — the
    in-process stand-in for the cross-process `spawn` + ASLR divergence.  Both must land
    on the expectation computed independently above.
    """
    skus = {1: 60, 2: 44, 3: 52}
    demand = {1: 20, 2: 15, 3: 30}

    wh_a, mgr_a = _stocked(skus)
    wh_b, mgr_b = _stocked(skus)   # both alive at once — distinct Bin objects

    expected: dict[tuple[int, int, int], int] = {}
    for sku, qty in demand.items():
        singles = list(mgr_a._sku_singleton_bins.get(sku, set()))
        pallets = list(mgr_a._sku_pallet_bins.get(sku, set()))
        takes = _canonical_takes(singles, qty)
        takes.update(_canonical_takes(pallets, qty - sum(takes.values())))
        for loc, take in takes.items():
            expected[loc] = expected.get(loc, 0) + take

    batch = _batch(demand)
    tasks_a = Task.from_batch(batch, wh_a, manager=mgr_a)
    tasks_b = Task.from_batch(batch, wh_b, manager=mgr_b)

    sel_a = {b.location for t in tasks_a for b in t.path}
    sel_b = {b.location for t in tasks_b for b in t.path}
    assert sel_a == set(expected), (
        f'real-manager selection differs from the canonical one; '
        f'missing={sorted(set(expected) - sel_a)} extra={sorted(sel_a - set(expected))}')
    assert sel_a == sel_b, (
        f'two identical warehouses in one process drained different bins: '
        f'symmetric difference {sorted(sel_a ^ sel_b)}')
    assert _fingerprint(tasks_a) == _fingerprint(tasks_b)

    xa, ya = _travel(tasks_a)
    xb, yb = _travel(tasks_b)
    assert math.isclose(xa, xb, rel_tol=0.0, abs_tol=_TRAVEL_TOL)
    assert math.isclose(ya, yb, rel_tol=0.0, abs_tol=_TRAVEL_TOL)


# ══════════════════════════════════════════════════════════════════════════════
# E — the manager-less fallback picks the same bins as the indexed path
# ══════════════════════════════════════════════════════════════════════════════

def test_manager_and_fallback_branches_agree() -> None:
    """`Task.from_batch` has two drain paths, and they must not disagree.

    The fallback scans `warehouse.bins` (emitted in `location` order) and stably sorts
    singleton-before-pallet, so it yields singletons then pallets, each in location order
    — exactly what the indexed path now does.  It did NOT agree before the index drain was
    sorted, which meant a manager-less caller silently exercised a different rule.
    """
    skus = {1: 60, 2: 44, 3: 52}
    demand = {1: 20, 2: 15, 3: 30}
    wh, mgr = _stocked(skus)

    # The fallback rebuilds its own index from warehouse.bins; sanity-check the premise
    # that that emission order is the canonical one, or the branches agree only by luck.
    locations = [b.location for b in wh.bins]
    assert locations == sorted(locations), 'warehouse.bins is no longer in location order'

    batch = _batch(demand)
    indexed  = Task.from_batch(batch, wh, manager=mgr)
    fallback = Task.from_batch(batch, wh, manager=None)
    assert _fingerprint(indexed) == _fingerprint(fallback), (
        'the indexed drain and the manager-less fallback selected different bins')
