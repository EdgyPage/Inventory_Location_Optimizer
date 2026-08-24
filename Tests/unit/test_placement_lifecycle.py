"""test_placement_lifecycle.py — every manager state dict, at every stage of a SKU's life.

`Inventory_Manager` keeps a dozen parallel indexes over the same bins, and none of them is
derived on demand: `_unavailable`, `_bin_sku`, `_sku_pallet_bins` / `_sku_singleton_bins`,
`_current_quantities`, `_initial_quantities`, `_originals`, `_pending_reclaim`,
`_depleted_skus`, `_index`, `_aisle_sku_counts`, `_aisle_lift_sum`.  They are maintained
INCREMENTALLY by the placement, pick, reclaim and reorder paths.

That is why this file exists.  A missed update to any one of them is silent: the run
continues, the numbers stay plausible, and the damage shows up as a warehouse that slowly
loses bins (a bin dropped from `_index` is never offered again) or a SKU that is never
restocked (missing from `_originals`).  Each test below walks one stage and pins what every
index must say afterwards.

    Stage 1  initial placement       — every index populated, consistently
    Stage 2  pick depletion          — `_notify_pick` -> `_depleted_skus`, `_pending_reclaim`
    Stage 3  bin reclaim             — the emptied bin returns to `_index`, leaves the rest
    Stage 4  reorder trigger         — `.reorder()` flags, OUP quantity, `_initial_quantities`
    Stage 5  load-aware reorder      — lift state maintained through a B/C-strategy restock
    Stage 6  duplicate-reorder guard — in-flight SKUs are not re-ordered
    Stage 7  end-to-end mini-sim     — the five stages composed, through the real pick loop

    python -m pytest Tests/unit/test_placement_lifecycle.py -q

History: this file used a `check()` harness whose `fail()` body was a `print`, so its 34
assertions could not fail the suite; two tests additionally hard-returned after a failing
check.  Both of those guards are now plain asserts — see Stage 2 and Stage 3.  Tests here
use real `assert`; do not re-introduce `check()`.
"""
from __future__ import annotations

import random
from typing import Any

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.catalog.Order import Order
from Warehouse.catalog.Demand import Demand
from Warehouse.inventory.Inventory_Management import Inventory_Manager, LoadParams, Placement
from Warehouse.placement.Assignment_Functions import (
    build_load_minimizing_assignment_fn,
    build_load_maximizing_assignment_fn,
)
from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
from Optimization.metrics.Workload import WorkloadParams

# ── warehouse factory ─────────────────────────────────────────────────────────
#
# Deliberately tiny (140 bins, 2 aisles): a small warehouse makes the index arithmetic
# checkable by hand, and every test below enqueues at `quantity=1` so it cannot fill up and
# start conflating "no bin was offered" with "the index is broken".

_W, _H = 5 * 48, 4 * 48   # 5 pallet-column widths x 4 extra_large-height levels
_AISLE_CFGS = [
    AisleConfig('conveyable', 'food', 'pallet',    _W, _H, ['small'], None),
    AisleConfig('conveyable', 'food', 'singleton', _W, _H, ['small', 'medium'], [0.5, 0.5]),
]
_WH_CFG = WarehouseConfig(total_aisles=2, aisle_splits=[0.5, 0.5], aisle_configs=_AISLE_CFGS)


def _build_warehouse(seed: int = 0) -> tuple[Any, Inventory_Manager]:
    """Build a small warehouse and return (warehouse, manager)."""
    Aisle.next_aisle_id = 1     # class counter — reset or aisle ids leak between tests
    random.seed(seed)           # Warehouse_Builder draws from the module-level random
    wh = Warehouse_Builder().from_config(_WH_CFG).build()
    return wh, Inventory_Manager(wh)


def _build_warehouse_with_affinity(seed: int = 0) -> tuple[Any, Inventory_Manager, AffinityStore]:
    """Same warehouse plus an in-memory affinity store.

    The manager only maintains `_aisle_sku_counts` / `_aisle_lift_sum` when an affinity
    store is attached, so the lift-state tests need this variant and Stage 1 (which asserts
    those stay EMPTY without one) needs the plain one.
    """
    Aisle.next_aisle_id = 1
    random.seed(seed)
    wh       = Warehouse_Builder().from_config(_WH_CFG).build()
    affinity = AffinityStore(':memory:')
    return wh, Inventory_Manager(wh, affinity=affinity), affinity


def _make_carton(sku: int, stock_qty: int = 35,
                 handling: str = 'conveyable',
                 category: str = 'food') -> Order:
    """An Order with known dimensions and OUP parameters — no DB, no profile generation."""
    from Warehouse.catalog.Order import StorageHandleConfig
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = (handling, category)
    c.storage_handle_config = StorageHandleConfig(handling, category)
    c.lift_group            = (handling, category)
    c.length  = 8
    c.width   = 8
    c.height  = 6
    c.weight  = 5
    c.demand                = Demand.from_rates(0.9, 3.0)
    c.equilibrium_qty       = stock_qty           # OUP target
    c.reorder_point         = max(1, stock_qty // 2)
    c.lead_time_mean        = 0.0
    c.expected_batch_demand = 0.9 * 3.0
    return c


def _bins_of(mgr, sku):
    return [b for b in mgr.unavailable if b.storage and b.storage.order.sku == sku]


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1: initial placement — every index populated and mutually consistent
# ═════════════════════════════════════════════════════════════════════════════

def test_initial_placement_populates_every_index_consistently():
    """Five SKUs, one `enqueue_all`, then every index is cross-checked against the bins.

    The three cross-checks that matter are the ones no single index can catch alone:
      - `len(_bin_sku) == len(_unavailable)`  — one index is not lagging the other;
      - `_current_quantities[sku] == sum of that SKU's bin quantities` — the running
        on-hand total, which every reorder decision reads, matches physical reality;
      - `_originals` / `_initial_quantities` cover every SKU — a SKU missing from either
        is one that can never be restocked, with no error at the time it happens.
    """
    wh, mgr = _build_warehouse(seed=42)
    orders = [_make_carton(sku=i, stock_qty=30) for i in range(1, 6)]
    mgr.enqueue_all(orders)

    # ── 1a: every SKU reachable through the per-SKU bin indexes
    unindexed = [c.sku for c in orders
                 if not (mgr._sku_singleton_bins.get(c.sku) or mgr._sku_pallet_bins.get(c.sku))]
    assert not unindexed, f'SKUs absent from both _sku_*_bins indexes: {unindexed}'

    # ── 1b/1c: the two bin-level indexes agree on how many bins are occupied
    placed = len(mgr.unavailable)
    assert placed > 0, 'enqueue_all placed nothing — the rest of this test proves nothing'
    assert len(mgr._bin_sku) == placed, (
        f'_bin_sku has {len(mgr._bin_sku)} entries but _unavailable has {placed} bins')

    # ── 1d/1e: per-SKU quantity accounting agrees with the bins themselves
    for c in orders:
        bins_for_c = _bins_of(mgr, c.sku)
        physical = sum(b.storage.quantity for b in bins_for_c)
        assert physical == c.equilibrium_qty, (
            f'sku={c.sku}: {physical} units across {len(bins_for_c)} bins, '
            f'expected equilibrium_qty {c.equilibrium_qty}')
        assert mgr._current_quantities.get(c.sku, 0) == physical, (
            f'sku={c.sku}: _current_quantities says '
            f'{mgr._current_quantities.get(c.sku, 0)}, bins hold {physical}')

    # ── 1f/1g: the restock prerequisites
    assert not [c.sku for c in orders if c.sku not in mgr._initial_quantities], (
        f'_initial_quantities missing SKUs: '
        f'{[c.sku for c in orders if c.sku not in mgr._initial_quantities]}')
    assert not [c.sku for c in orders if c.sku not in mgr._originals], (
        f'_originals missing SKUs (they can never be restocked): '
        f'{[c.sku for c in orders if c.sku not in mgr._originals]}')

    # ── 1h: lift state is affinity-gated BY DESIGN — no affinity store, no counts.
    # Pinned because it is the surface of a real bug: placement that runs before
    # init_lift_state sees empty aisle sets (see test_warehouse_sizing's
    # test_init_lift_state_populates_aisle_sets, which asserts the repair).
    total_counts = sum(sum(d.values()) for d in mgr._aisle_sku_counts.values())
    assert total_counts == 0, (
        f'_aisle_sku_counts is {total_counts}, expected 0 — it must only be maintained when '
        f'an affinity store is attached')

    # ── 1i: initial stock is not a reorder
    flagged = [b.location for b in mgr.unavailable
               if b.storage and getattr(b.storage.order, '_is_reorder', False)]
    assert not flagged, f'_is_reorder set on initially-placed bins: {flagged[:3]}'


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2: pick depletion -> _depleted_skus, _pending_reclaim
# ═════════════════════════════════════════════════════════════════════════════

def test_pick_depletion_flags_only_once_the_reorder_point_is_crossed():
    """The threshold must be exact on BOTH sides — one pick above it, one pick across it.

    Only asserting the crossing would pass a manager that flags every SKU on every pick,
    which would fire a reorder wave per batch for the whole catalogue.
    """
    wh, mgr = _build_warehouse(seed=42)
    order = _make_carton(sku=10, stock_qty=20)
    mgr.enqueue_all([order])                     # 20 units, reorder_point 10

    placed_bins = _bins_of(mgr, 10)
    # This used to be a `check(...)` followed by `if not placed_bins: return`, so a
    # placement failure exited early and reported PASS.  The warehouse has 140 bins for one
    # 20-unit SKU, so an empty result is a real defect, not a precondition.
    assert placed_bins, 'SKU 10 was not placed in any bin'

    initial   = mgr._current_quantities[10]
    threshold = order.reorder_point
    assert initial > threshold, (
        f'fixture precondition: on-hand {initial} must start ABOVE reorder_point {threshold}')

    mgr._notify_pick(10, initial - (threshold + 1))          # land exactly one unit above
    assert mgr._current_quantities[10] == threshold + 1, mgr._current_quantities[10]
    assert 10 not in mgr._depleted_skus, (
        f'flagged at on-hand {mgr._current_quantities[10]}, one unit ABOVE the '
        f'reorder_point {threshold}')

    mgr._notify_pick(10, 1)                                   # one more: cross it
    assert mgr._current_quantities[10] == threshold, mgr._current_quantities[10]
    assert 10 in mgr._depleted_skus, (
        f'NOT flagged at on-hand {mgr._current_quantities[10]} == reorder_point {threshold}')


def test_emptying_a_bin_queues_it_for_reclaim():
    """An emptied bin must be parked in `_pending_reclaim`, not returned to `_index` at once.

    Reclaim is deferred to the top of `check_reorders` so a bin freed mid-batch cannot be
    handed out to the same batch's placements — a bin in two places at once.
    """
    wh, mgr = _build_warehouse(seed=42)
    mgr.enqueue_all([_make_carton(sku=10, stock_qty=20)])
    bin_ = _bins_of(mgr, 10)[0]

    bin_.storage.quantity = 0
    bin_.storage = None
    mgr._notify_bin_emptied(bin_)

    assert bin_ in mgr._pending_reclaim, (
        f'bin {bin_.location} emptied but absent from _pending_reclaim — it is now occupied '
        f'by nothing and offered to no one')


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3: _reclaim_empty_bins — the bin returns, and nothing else moves
# ═════════════════════════════════════════════════════════════════════════════

def test_reclaim_returns_the_bin_to_the_available_index_and_clears_every_trace():
    """Five indexes must move together on one reclaim; each is checked separately.

    A partial reclaim is the classic silent leak: the bin leaves `_unavailable` but never
    reaches `_index`, so it is gone from the warehouse for the rest of the run and the only
    symptom is a fill rate that drifts down.
    """
    wh, mgr, affinity = _build_warehouse_with_affinity(seed=42)
    orders = [_make_carton(sku=i, stock_qty=15) for i in range(1, 4)]
    mgr.enqueue_all(orders, quantity=1)
    mgr.init_lift_state(affinity)

    placed = _bins_of(mgr, 1)
    # Also a former `check(...)` + `if not placed: return` hard-return.  Three 1-unit SKUs
    # into 140 bins always place, so this is a defect rather than a skip condition.
    assert placed, 'SKU 1 has no placed bin'

    bin_      = placed[0]
    pre_avail = sum(len(v) for v in mgr._index.values())

    bin_.storage.quantity = 0
    bin_.storage = None
    mgr._notify_bin_emptied(bin_)
    mgr._reclaim_empty_bins()

    post_avail = sum(len(v) for v in mgr._index.values())
    assert post_avail == pre_avail + 1, (
        f'_index went {pre_avail} -> {post_avail}; the reclaimed bin was not returned '
        f'(a permanently lost bin)')
    assert id(bin_) not in {id(b) for b in mgr.unavailable}, (
        f'bin {bin_.location} still listed in _unavailable after reclaim')
    assert id(bin_) not in mgr._bin_sku, (
        f'_bin_sku still maps bin {bin_.location} to a SKU after reclaim')
    assert bin_ not in mgr._sku_pallet_bins.get(1, set()), '_sku_pallet_bins still holds it'
    assert bin_ not in mgr._sku_singleton_bins.get(1, set()), '_sku_singleton_bins still holds it'
    assert len(mgr._pending_reclaim) == 0, (
        f'_pending_reclaim still holds {len(mgr._pending_reclaim)} bins after '
        f'_reclaim_empty_bins — they would be reclaimed twice')


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 4: check_reorders — flags, OUP quantity, and what must NOT change
# ═════════════════════════════════════════════════════════════════════════════

def test_reorder_trigger_sets_the_flag_and_leaves_the_initial_baseline_alone():
    """A restock must be distinguishable from initial stock, and must not rewrite history.

    `_initial_quantities` is the baseline every fill/depletion ratio is measured against.
    If a reorder updated it, the measured depletion would reset to zero on every restock and
    the whole convergence metric would flatline at a plausible-looking value.
    """
    wh, mgr = _build_warehouse(seed=42)
    stock_qty = 40
    order = _make_carton(sku=20, stock_qty=stock_qty)
    mgr.enqueue_all([order], quantity=1)     # one unit — deliberately below reorder_point 20

    initial = mgr._initial_quantities[20]
    mgr._notify_pick(20, 1)                  # pick it to zero
    assert 20 in mgr._depleted_skus, 'precondition: sku 20 must be flagged depleted'

    triggered = mgr.check_reorders()
    assert 20 in triggered, f'check_reorders returned {triggered}, expected sku 20'

    reorder_bins = [b for b in mgr.unavailable
                    if b.storage and b.storage.order.sku == 20
                    and getattr(b.storage.order, '_is_reorder', False)]
    queued = [it for it in mgr._stock_queue
              if it.unit.order.sku == 20
              and getattr(it.unit.order, '_is_reorder', False)]
    assert reorder_bins or queued, (
        'a reorder fired but produced no unit — none placed, none queued')

    # Restock quantity comes from the OUP formula (eq + pipeline - position), NOT from the
    # initial-stock override, so it must never be the equilibrium_qty verbatim per bin.
    for b in reorder_bins:
        assert 1 <= b.storage.quantity <= stock_qty * 2, (
            f'reorder bin {b.location} holds {b.storage.quantity}, outside [1, {stock_qty * 2}]')
    for u in queued:
        assert 1 <= u.quantity <= stock_qty * 2, (
            f'queued reorder unit holds {u.quantity}, outside [1, {stock_qty * 2}]')

    assert mgr._initial_quantities[20] == initial, (
        f'_initial_quantities[20] rewritten by a reorder: {initial} -> '
        f'{mgr._initial_quantities.get(20)}')
    assert 20 not in mgr._depleted_skus, (
        '_depleted_skus not cleared after check_reorders — sku 20 would reorder again next batch')


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 5: load-aware assignment (strategies B/C) maintains lift state on reorder
# ═════════════════════════════════════════════════════════════════════════════

def test_load_aware_reorder_maintains_aisle_state_for_both_directions():
    """The load-min and load-max scorers READ `_aisle_sku_counts` / `_aisle_lift_sum` and
    are responsible for WRITING them back as they place.

    A scorer that reads but does not commit sees a permanently empty warehouse and places
    every unit as if it were the first — which looks exactly like a working run.  Both
    directions are exercised because they are separate code paths through the shared core.
    """
    wp = WorkloadParams()
    lp = LoadParams(lambda_=1.1, k=1.0, gamma=1.5)

    for label, build_fn, base_sku in (('load_min', build_load_minimizing_assignment_fn, 30),
                                      ('load_max', build_load_maximizing_assignment_fn, 40)):
        wh, mgr, affinity = _build_warehouse_with_affinity(seed=42)
        orders = [_make_carton(sku=i, stock_qty=20) for i in range(base_sku, base_sku + 5)]
        mgr.enqueue_all(orders, quantity=1)
        mgr.init_lift_state(affinity)

        mgr.placement = Placement(label, build_fn(
            lp, affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_lift_sum, mgr._aisle_idx_sets,
        ))

        mgr._notify_pick(base_sku, 1)
        assert base_sku in mgr._depleted_skus, (
            f'[{label}] precondition: sku {base_sku} must be flagged depleted')

        triggered = mgr.check_reorders()
        assert base_sku in triggered, (
            f'[{label}] check_reorders returned {triggered}, expected sku {base_sku}')

        total_counts = sum(sum(d.values()) for d in mgr._aisle_sku_counts.values())
        assert total_counts > 0, (
            f'[{label}] _aisle_sku_counts still empty after a restock placed through the '
            f'load-aware scorer — the scorer is not committing aisle state')
        assert mgr.queue_depth == 0, (
            f'[{label}] {mgr.queue_depth} units left queued in a 140-bin warehouse holding '
            f'6 units — the load-aware path failed to place')


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 6: the duplicate-reorder guard
# ═════════════════════════════════════════════════════════════════════════════

def test_a_sku_already_on_order_is_not_reordered_again():
    """Inventory POSITION = on-hand + queued + deferred, so an in-flight SKU is covered.

    Without the guard, a SKU whose restock cannot be placed (warehouse full) is re-ordered
    every batch forever and the queue grows without bound — the exact symptom
    `test_reorder_queue.py` exists to detect at the system level.  This is that guard in
    isolation, with the depleted flag FORCED back on so only the position check can stop it.
    """
    wh, mgr = _build_warehouse(seed=42)
    mgr.enqueue_all([_make_carton(sku=50, stock_qty=10),
                     _make_carton(sku=51, stock_qty=10)], quantity=1)
    # Fill the warehouse so the reorder cannot place and must stay on the books.
    mgr.enqueue_all([_make_carton(sku=100 + i) for i in range(100)], quantity=1)

    mgr._notify_pick(50, mgr._current_quantities.get(50, 0))
    mgr.check_reorders()                       # first call — the reorder goes on order
    depth_after_first = mgr.queue_depth

    mgr._notify_pick(50, 1)                    # already at 0; cannot cross the threshold again
    mgr._depleted_skus.add(50)                 # force the flag so ONLY the position check applies
    mgr.check_reorders()

    assert mgr.queue_depth == depth_after_first, (
        f'queue {depth_after_first} -> {mgr.queue_depth}: sku 50 was re-ordered while a '
        f'wave was already on order')


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 7: the five stages composed, through the real pick loop
# ═════════════════════════════════════════════════════════════════════════════

def test_end_to_end_mini_sim_picks_restocks_and_places_for_five_batches():
    """Everything above, wired together: Batch -> Task -> PickSimulation -> reorder -> place.

    The individual stages each stub out their neighbours; this is the only test in the file
    where a pick actually reaches `_notify_pick` through the simulator.  Its assertions are
    about the loop staying LIVE — a batch that yields no task, or a simulation that yields
    no 'done' event, would let every other assertion here pass over an empty run.
    """
    wh, mgr = _build_warehouse(seed=99)
    orders = [_make_carton(sku=i, stock_qty=15) for i in range(1, 6)]
    mgr.enqueue_all(orders, quantity=1)

    pick_cfg  = PickConfig(num_pickers=2, x_speed=1.0, y_speed=0.5,
                           pick_intercept=0.5, pick_weight_coef=0.1,
                           pick_volume_coef=0.001, cart_swap_coef=2.0)
    batch_cfg = BatchConfig(inventory_size=5, mean_fraction=0.6, std_fraction=0.1)

    class _Inv:
        """Batch only reads `.orders`."""
        def __init__(self, c):
            self.orders = c

    inv = _Inv(orders)
    reorders_total = batches_with_tasks = done_total = 0

    for batch_num in range(5):
        reorders_total += len(mgr.check_reorders())

        tasks = Task.from_batch(Batch(batch_cfg, inv, affinity=None), wh, manager=mgr)
        if not tasks:
            continue
        batches_with_tasks += 1

        events = PickSimulation(tasks, pick_cfg, manager=mgr).run()
        done = [e for e in events if e.event_type == 'done']
        assert done, f'batch {batch_num + 1}: {len(tasks)} tasks produced no done events'
        done_total += len(done)

    # The old harness closed with `check('completed 5 batches without exception', True)` —
    # an assertion on the literal True.  These four are the properties that were meant.
    assert batches_with_tasks == 5, (
        f'only {batches_with_tasks}/5 batches produced any task — the mini-sim ran mostly '
        f'empty and the assertions above proved little')
    assert done_total > 0, 'no pick completed across five batches'
    assert reorders_total > 0, (
        'five batches of picking triggered no reorder at all — depletion is not reaching '
        '_notify_pick through the simulator')
    assert mgr.queue_depth == 0, (
        f'{mgr.queue_depth} units still queued after 5 batches in a 140-bin warehouse '
        f'holding 5 SKUs — restock is firing but not placing')
