"""test_equilibrium_reorder.py — the Order-Up-To (OUP) equilibrium replenishment model.

The model, end to end
---------------------
At profile-generation time each SKU gets three numbers derived from its demand:

    expected_batch_demand = relative_frequency * quantity_rate
    equilibrium_qty       = max(1, round(coverage_batches * expected_batch_demand))
    reorder_point         = max(1, min(eq - 1, round(demand * (lead_time + safety_batches))))

At run time `check_reorders` fires when inventory POSITION (on-hand + queued + deferred)
falls to or below `reorder_point`, and orders back up to the target:

    pipeline = round(rp * lead / (lead + 1))        # expected in-transit over the lead
    ordered  = equilibrium_qty + pipeline - position
    received = max(1, round(N(ordered, ordered * supply_cv)))

Every order enters `_lead_queue` as `[sku, qty, remaining_lead]` — even at lead 0 — and is
released into the stock queue when `remaining_lead` reaches 0.

Why POSITION and not on-hand
----------------------------
Half the tests here exist for one reason: an order that has fired but not yet arrived must
still count toward the threshold.  If it does not, a SKU with a long lead time re-orders
every single batch while its first order is in transit, the queue grows without bound, and
the warehouse ends up massively over-ordered — with no error anywhere.  That is what
`test_deferred_blocks_duplicate_reorder`, `test_position_counts_queued_units` and
`test_oup_qty_uses_position` pin.

    python -m pytest Tests/unit/test_equilibrium_reorder.py -q

History: this file used a `check()` harness whose `fail()` body was a `print`, so 43 of its
assertions could not fail the suite, and one test hard-returned after a failing check.  The
`supply_cv` tests additionally asserted against a *transcription* of the sampling formula
rather than the production path — see the Part G banner.  Do not re-introduce `check()`.
"""
from __future__ import annotations

import math
import os
import random
import sqlite3
import tempfile
from statistics import mean

import pytest

from Warehouse.layout.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.catalog.Demand import Demand
from Warehouse.generation.generate_inventory import (
    build_inventory_with_profile,
    save_inventory_to_db,
    load_inventory_from_db,
    DEFAULT_DIM_SPEC,
    DEFAULT_WEIGHT_SPEC,
)
from Warehouse.inventory.Inventory_Management import Inventory_Manager, _equilibrium_qty
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

_TOL = 1e-9


# ── fixtures ──────────────────────────────────────────────────────────────────

def _small_warehouse(seed: int = 0) -> tuple[WarehouseConfig, Inventory_Manager]:
    """4 aisles, both unit families, both handling types — enough to place a few SKUs."""
    Aisle.next_aisle_id = 1     # class counter — reset or aisle ids leak between tests
    random.seed(seed)           # Warehouse_Builder draws from the module-level random
    w = aisle_width_for(4)      # 192
    h = aisle_height_for(6)     # 288
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
    wh = Warehouse_Builder().from_config(cfg).build()
    return cfg, Inventory_Manager(wh)


def _make_carton(sku: int, eq_qty: int = 20, rp: int = 10,
                 lt: float = 0.0, supply_cv: float = 0.0) -> Order:
    """An Order with hand-set OUP parameters — no DB, no profile generation."""
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length = 8
    c.width  = 8
    c.height = 6
    c.weight = 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty       = eq_qty
    c.reorder_point         = rp
    c.lead_time_mean        = lt
    c.supply_cv             = supply_cv
    c.expected_batch_demand = 0.8 * 4.0
    return c


def _profile(num_skus, seed, **kw):
    return build_inventory_with_profile(
        num_skus=num_skus, seed=seed,
        handling_splits=[0.5, 0.5],
        category_splits=[1 / 6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        **kw)


def _clear_in_flight(mgr, sku):
    """Drop every trace of an in-flight order so the next trigger fires a fresh wave.

    Needed because the duplicate-reorder guard (correctly) refuses to re-order a SKU whose
    position already covers the threshold — which is exactly what makes repeated sampling
    of the receipt quantity impossible without this reset.
    """
    mgr._queued_sku_counts.pop(sku, None)
    mgr._queued_qty.pop(sku, None)
    mgr._deferred_qty.pop(sku, None)
    mgr._lead_queue = [e for e in mgr._lead_queue if e[0] != sku]


# ═════════════════════════════════════════════════════════════════════════════
# Part A: what build_inventory_with_profile must produce
# ═════════════════════════════════════════════════════════════════════════════

def test_every_generated_sku_carries_the_three_oup_attributes():
    """All 100 SKUs, not a spot check.

    `check_reorders` reads `equilibrium_qty`, `reorder_point` and `lead_time_mean` off the
    order with no default worth having: a missing attribute means a SKU that is never
    restocked.  `stock_qty` must be GONE — the loader is canonical-schema only, and a
    lingering legacy attribute would let `_equilibrium_qty`'s fallback mask a real omission.

    (The old harness `break`-ed after the first SKU because each check printed a line.
    Asserting is free, so all 100 are checked.)
    """
    inv = _profile(100, seed=42, equilibrium_coverage_batches=10.0, reorder_safety_batches=2.0)
    assert len(inv.orders) == 100, len(inv.orders)

    for c in inv.orders:
        assert hasattr(c, 'equilibrium_qty'), f'sku={c.sku} has no equilibrium_qty'
        assert hasattr(c, 'reorder_point'),   f'sku={c.sku} has no reorder_point'
        assert hasattr(c, 'lead_time_mean'),  f'sku={c.sku} has no lead_time_mean'
        assert not hasattr(c, 'stock_qty'), (
            f'sku={c.sku} still carries the legacy stock_qty={c.stock_qty} — '
            f'_equilibrium_qty would silently fall back to it')
        assert c.equilibrium_qty >= 1, f'sku={c.sku} equilibrium_qty={c.equilibrium_qty}'
        assert c.reorder_point <= c.equilibrium_qty, (
            f'sku={c.sku} reorder_point={c.reorder_point} > equilibrium_qty='
            f'{c.equilibrium_qty} — it would reorder on a full bin, every batch')


def test_equilibrium_and_reorder_point_match_their_formulas_exactly():
    """The two derived quantities are recomputed here from `expected_batch_demand`.

    Both are integers, so this is an exact comparison by construction — a rounding change in
    the generator shows up as an off-by-one on hundreds of SKUs rather than as drift.
    """
    inv = _profile(200, seed=7, equilibrium_coverage_batches=10.0, reorder_safety_batches=2.0)

    bad_eq, bad_rp = [], []
    for c in inv.orders:
        want_eq = max(1, round(10.0 * c.expected_batch_demand))
        # ROP = demand x (lead_time + safety), floored at 1 and capped at eq-1.
        want_rp = max(1, min(c.equilibrium_qty - 1,
                             round(c.expected_batch_demand * (c.lead_time_mean + 2.0))))
        if c.equilibrium_qty != want_eq:
            bad_eq.append((c.sku, c.equilibrium_qty, want_eq))
        if c.reorder_point != want_rp:
            bad_rp.append((c.sku, c.reorder_point, want_rp))

    assert not bad_eq, (
        f'{len(bad_eq)}/{len(inv.orders)} SKUs where equilibrium_qty != '
        f'round(10 x expected_batch_demand); (sku, got, want) = {bad_eq[:3]}')
    assert not bad_rp, (
        f'{len(bad_rp)}/{len(inv.orders)} SKUs where reorder_point != '
        f'demand x (lead + safety) capped at eq-1; (sku, got, want) = {bad_rp[:3]}')


def test_reorder_point_rises_with_demand():
    """The formula is only useful if it discriminates.

    A generator bug that collapsed every `expected_batch_demand` to a constant would satisfy
    the formula test above perfectly while making every SKU's policy identical.
    """
    by_demand = sorted(_profile(200, seed=7,
                                equilibrium_coverage_batches=10.0,
                                reorder_safety_batches=2.0).orders,
                       key=lambda c: c.expected_batch_demand)
    low, high = by_demand[0], by_demand[-1]

    assert high.reorder_point > low.reorder_point, (
        f'slowest mover (demand={low.expected_batch_demand:.3f}) has rp={low.reorder_point}; '
        f'fastest (demand={high.expected_batch_demand:.3f}) has rp={high.reorder_point}')


# ═════════════════════════════════════════════════════════════════════════════
# Part B: DB round-trip
# ═════════════════════════════════════════════════════════════════════════════

def test_oup_attributes_survive_a_db_round_trip():
    """Worker processes RELOAD the inventory from a DB; anything not persisted is lost.

    A dropped `reorder_point` column does not raise — the loaded order simply has none, and
    every worker runs a warehouse that never restocks while the main process's own numbers
    look fine.
    """
    inv = _profile(50, seed=1, equilibrium_coverage_batches=8.0,
                   reorder_safety_batches=2.0, lead_time_mean_batches=2.0)
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        save_inventory_to_db(inv, db_path, {'test': True})
        inv2 = load_inventory_from_db(db_path)

        orig   = {c.sku: c for c in inv.orders}
        loaded = {c.sku: c for c in inv2.orders}
        assert len(loaded) == len(orig), f'{len(loaded)} SKUs loaded, {len(orig)} saved'

        mismatches = []
        for sku, c in orig.items():
            c2 = loaded[sku]
            if c.equilibrium_qty != c2.equilibrium_qty:
                mismatches.append(('equilibrium_qty', sku, c.equilibrium_qty, c2.equilibrium_qty))
            if c.reorder_point != c2.reorder_point:
                mismatches.append(('reorder_point', sku, c.reorder_point, c2.reorder_point))
            if abs(c.lead_time_mean - c2.lead_time_mean) > _TOL:
                mismatches.append(('lead_time_mean', sku, c.lead_time_mean, c2.lead_time_mean))
        assert not mismatches, (
            f'{len(mismatches)} attribute(s) changed across save+load; '
            f'(field, sku, saved, loaded) = {mismatches[:3]}')

        # The table is `cartons`, NOT `orders` — the persisted name was deliberately kept
        # stable across the Carton->Order rename (generate_inventory._SCHEMA).  This read
        # used to name `orders`; PRAGMA table_info on a table that does not exist returns
        # NO ROWS rather than raising, so `cols` was always [] and all three checks below
        # were vacuous — the absent-check passed for the wrong reason and the two
        # present-checks asserted nothing.  The emptiness guard goes first, so the same
        # mistake fails loudly instead of silently.
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(cartons)').fetchall()]
        conn.close()
        assert cols, 'PRAGMA table_info(cartons) returned no columns — wrong table name?'
        assert 'stock_qty' not in cols, f'legacy stock_qty column present in a new DB: {cols}'
        assert 'equilibrium_qty' in cols, f'equilibrium_qty column missing: {cols}'
        assert 'lead_time_mean' in cols, f'lead_time_mean column missing: {cols}'
    finally:
        os.unlink(db_path)


# (A legacy stock_qty-schema DB load test was removed with the schema: the loader is
#  canonical-schema only, and backward compatibility with pre-equilibrium_qty DBs is
#  intentionally dropped.)


# ═════════════════════════════════════════════════════════════════════════════
# Part C: OUP refills to the target, not by a fixed batch size
# ═════════════════════════════════════════════════════════════════════════════

def test_a_reorder_refills_all_the_way_to_the_equilibrium_target():
    """Order-Up-To, not order-a-fixed-amount: the order SIZE depends on how far down it is.

    A fixed reorder quantity would leave a heavily-depleted SKU short after a restock and a
    lightly-depleted one over-stocked, and neither shows up as an error.
    """
    _, mgr = _small_warehouse(seed=0)
    c = _make_carton(sku=1, eq_qty=20, rp=8, lt=0.0)
    mgr.enqueue(c)

    assert mgr._current_quantities.get(1, 0) == 20, (
        f'initial placement put {mgr._current_quantities.get(1, 0)} units in bins, '
        f'expected equilibrium_qty 20')

    mgr._current_quantities[1] = 5          # simulate picks depleting to 5, below rp=8
    mgr._depleted_skus.add(1)

    triggered = mgr.check_reorders()
    assert 1 in triggered, f'check_reorders returned {triggered} at on-hand 5, rp 8'
    assert mgr._current_quantities.get(1, 0) == 20, (
        f'refilled to {mgr._current_quantities.get(1, 0)}, expected the equilibrium target 20 '
        f'(15 units ordered from an on-hand of 5)')


def test_a_reorder_never_overshoots_the_equilibrium_target():
    """The other side of the same formula: `eq - position` cannot exceed `eq`.

    An overshoot is how a "restocks are fine" run silently consumes bins that other SKUs
    needed, and the queue growth surfaces batches later somewhere else entirely.
    """
    _, mgr = _small_warehouse(seed=1)
    c = _make_carton(sku=2, eq_qty=30, rp=10, lt=0.0)
    mgr.enqueue(c)

    mgr._current_quantities[2] = 9          # one below rp -> orders 30 - 9 = 21
    mgr._depleted_skus.add(2)
    mgr.check_reorders()

    final = mgr._current_quantities.get(2, 0)
    assert final <= 30, f'refilled to {final}, above the equilibrium_qty ceiling of 30'
    assert final > 9, f'refilled to {final}; no units arrived at all from an on-hand of 9'


# ═════════════════════════════════════════════════════════════════════════════
# Part D: lead time defers, and deferral does not double-order
# ═════════════════════════════════════════════════════════════════════════════

def test_lead_time_zero_arrives_in_the_same_batch():
    """A lead-0 order still passes THROUGH `_lead_queue`; it just leaves in the same call.

    Every order takes the same path regardless of lead — steps 2 and 3 of `check_reorders`
    both run each batch — so lead 0 must leave the queue empty, not bypass it.
    """
    _, mgr = _small_warehouse(seed=2)
    mgr.enqueue(_make_carton(sku=10, eq_qty=20, rp=8, lt=0.0))

    mgr._current_quantities[10] = 5
    mgr._depleted_skus.add(10)
    mgr.check_reorders()

    assert len(mgr._lead_queue) == 0, (
        f'lead_time=0 left {mgr._lead_queue} in the lead queue; it must be released in the '
        f'same batch it was ordered')
    assert mgr._current_quantities.get(10, 0) == 20, (
        f'on-hand {mgr._current_quantities.get(10)} after a lead-0 restock, expected 20')


def test_lead_time_three_arrives_on_exactly_the_third_batch():
    """The deferral is deterministic (`round(lead_time_mean)`), so the arrival batch is exact.

    Asserting only "it arrives eventually" would pass a manager that released everything
    immediately — which is precisely the bug that makes an in-transit pipeline invisible.
    """
    _, mgr = _small_warehouse(seed=3)
    mgr.enqueue(_make_carton(sku=20, eq_qty=20, rp=8, lt=3.0))

    mgr._current_quantities[20] = 5
    mgr._depleted_skus.add(20)
    triggered = mgr.check_reorders()          # batch 1: fires, enters the lead queue at rem=3

    assert 20 in triggered, f'check_reorders returned {triggered}'
    assert mgr._queued_sku_counts.get(20, 0) == 0, (
        f'{mgr._queued_sku_counts.get(20, 0)} units went straight to the stock queue; a '
        f'lead-3 order must wait')
    assert any(e[0] == 20 for e in mgr._lead_queue), (
        f'sku 20 absent from the lead queue {mgr._lead_queue}')
    assert mgr._current_quantities.get(20, 0) == 5, (
        f'on-hand {mgr._current_quantities.get(20)} already restored before the lead elapsed')

    # Batches 2 and 3 must not deliver; batch 4 (three elapsed batches) must.
    for elapsed in (1, 2):
        mgr.check_reorders()
        assert mgr._current_quantities.get(20, 0) == 5, (
            f'order arrived after {elapsed} batch(es), lead_time_mean was 3.0')

    mgr.check_reorders()
    on_hand = mgr._current_quantities.get(20, 0)
    # Order-up-to is lead-aware (eq + pipeline - position), so the arrival restores to AT
    # LEAST the equilibrium — the pipeline term covers demand during the next lead.
    assert on_hand >= 20, (
        f'on-hand {on_hand} after the lead elapsed, expected at least equilibrium_qty 20')
    assert not mgr._lead_queue, f'lead queue not drained: {mgr._lead_queue}'


def test_an_in_flight_order_blocks_a_second_reorder_for_the_same_sku():
    """The unbounded-queue bug in one assertion.

    Without this guard a lead-5 SKU fires a fresh full-size order every batch for five
    batches, and the warehouse ends up with six times the stock it asked for.
    """
    _, mgr = _small_warehouse(seed=4)
    mgr.enqueue(_make_carton(sku=30, eq_qty=20, rp=8, lt=5.0))

    mgr._current_quantities[30] = 5
    mgr._depleted_skus.add(30)
    mgr.check_reorders()                      # deferred order now in flight

    mgr._current_quantities[30] = 2           # fall further; the order is still in transit
    mgr._depleted_skus.add(30)
    triggered2 = mgr.check_reorders()

    assert 30 not in triggered2, (
        f'sku 30 re-ordered ({triggered2}) while a wave was still in the lead queue '
        f'{mgr._lead_queue}')


def test_inventory_position_counts_queued_units_toward_the_threshold():
    """POSITION = on-hand + queued + deferred, at BOTH decision points.

    `_notify_pick` decides whether to flag depleted and `check_reorders` decides whether to
    fire; both must read position.  A SKU with 20 units queued for a bin is not short of
    stock, it is short of SPACE, and ordering more makes that strictly worse.
    """
    _, mgr = _small_warehouse(seed=21)
    c = _make_carton(sku=30, eq_qty=20, rp=8)
    mgr._originals[30] = c

    mgr._current_quantities[30] = 5           # on-hand 5
    mgr._queued_qty[30]         = 20          # 20 already on order, awaiting a bin
    mgr._depleted_skus.clear()
    mgr._notify_pick(30, 3)                   # on-hand 5->2; position 2 + 20 = 22 > rp 8

    assert 30 not in mgr._depleted_skus, (
        f'flagged depleted at position {mgr._current_quantities[30] + mgr._queued_qty[30]}, '
        f'well above reorder_point 8')

    # Even FORCED into the depleted set every batch, the position check must hold the line.
    fired = []
    for batch in range(10):
        mgr._depleted_skus.add(30)
        if 30 in mgr.check_reorders():
            fired.append(batch)
    assert not fired, f'reordered on batch(es) {fired} despite 20 units already on order'
    assert mgr._queued_qty.get(30, 0) == 20, (
        f'on-order ballooned to {mgr._queued_qty.get(30, 0)} across 10 batches, from 20')


def test_the_order_size_is_measured_from_position_not_on_hand():
    """The exact OUP arithmetic, with both an on-hand and an on-order component.

        eq=30, rp=12, lead=5, on-hand=6, queued=6  ->  position = 12 == rp, so it fires
        pipeline = round(rp * lead / (lead + 1)) = round(12 * 5/6) = 10
        ordered  = eq + pipeline - position = 30 + 10 - 12 = 28

    Computing from on-hand alone would give 30 + 10 - 6 = 34 — six units of double-order,
    which is exactly the drift that accumulates into an unbounded queue.

    lead > 0 keeps the new wave deferred (in `_deferred_qty`), so no placement muddies the
    accounting.
    """
    _, mgr = _small_warehouse(seed=22)
    c = _make_carton(sku=31, eq_qty=30, rp=12, lt=5.0, supply_cv=0.0)
    mgr._originals[31] = c
    mgr._current_quantities[31] = 6
    mgr._queued_qty[31]          = 6

    before = mgr._deferred_qty.get(31, 0)
    mgr._depleted_skus.add(31)
    mgr.check_reorders()
    ordered = mgr._deferred_qty.get(31, 0) - before

    assert ordered == 28, (
        f'ordered {ordered}; expected 28 = eq(30) + pipeline(10) - position(12). '
        f'34 would mean it measured from on-hand(6) instead of position')


def test_a_stamped_pipeline_replaces_the_heuristic_and_an_unstamped_one_keeps_it():
    """The era stamps `pipeline_qty = round(d_s x lead)` on the SKU ("Choose the coverage
    floor", decision 6; `Order.pipeline_allowance` is the one definition).  Same order as
    above with the stamp: ordered = eq(30) + STAMP(3) - position(12) = 21, not 28.  An
    unstamped order -- every flag-off run -- reads the heuristic byte for byte."""
    _, mgr = _small_warehouse(seed=22)
    c = _make_carton(sku=32, eq_qty=30, rp=12, lt=5.0, supply_cv=0.0)
    assert c.pipeline_allowance() == 10                     # unstamped: round(12 x 5/6)
    c.pipeline_qty = 3
    assert c.pipeline_allowance() == 3
    assert c.reorder().pipeline_allowance() == 3, 'the stamp rides the reorder copy'
    mgr._originals[32] = c
    mgr._current_quantities[32] = 6
    mgr._queued_qty[32]          = 6
    before = mgr._deferred_qty.get(32, 0)
    mgr._depleted_skus.add(32)
    mgr.check_reorders()
    assert mgr._deferred_qty.get(32, 0) - before == 21
    # `pipeline_qty = 0` is a stamp too (a lead-0 SKU under the era), not "absent".
    c0 = _make_carton(sku=33, eq_qty=30, rp=12, lt=5.0)
    c0.pipeline_qty = 0
    assert c0.pipeline_allowance() == 0


# ═════════════════════════════════════════════════════════════════════════════
# Part E: the _equilibrium_qty accessor
# ═════════════════════════════════════════════════════════════════════════════

def test_equilibrium_qty_accessor_covers_all_three_order_shapes():
    """One helper reads the target off orders of three vintages.

    The legacy `stock_qty` fallback is why `test_every_generated_sku_carries_the_three_oup_
    attributes` asserts the attribute is GONE from generated orders: with the fallback in
    place, a generator that stopped setting `equilibrium_qty` would keep working silently.
    """
    assert _equilibrium_qty(_make_carton(sku=40, eq_qty=25)) == 25

    legacy = object.__new__(Order)
    legacy.stock_qty = 50
    assert _equilibrium_qty(legacy) == 50, 'legacy stock_qty fallback'

    bare = object.__new__(Order)
    assert _equilibrium_qty(bare) == 1, 'an order with neither attribute defaults to 1'


# ═════════════════════════════════════════════════════════════════════════════
# Part F: fill stability over 30 batches (regression)
# ═════════════════════════════════════════════════════════════════════════════

def test_fill_does_not_drift_down_over_thirty_batches():
    """The system-level consequence of everything above, on a full generated profile.

    Every unit test in this file pins one rule in isolation; this one asserts they compose.
    A slow leak — a reorder that fires but under-orders, a release that drops units — shows
    up here as fill drifting down while every isolated test stays green.
    """
    inv = _profile(100, seed=42, equilibrium_coverage_batches=10.0, reorder_safety_batches=2.0)

    Aisle.next_aisle_id = 1
    random.seed(42)
    w, h = aisle_width_for(6), aisle_height_for(8)
    cfgs = []
    for cat in ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']:
        for handling in ['conveyable', 'non-conveyable']:
            cfgs.append(AisleConfig(handling, cat, 'pallet', w, h,
                                    ['small', 'medium', 'large', 'extra_large'], [0.25] * 4))
        for handling in ['conveyable', 'non-conveyable']:
            cfgs.append(AisleConfig(handling, cat, 'singleton', w, h, ['singleton'], None))

    n_aisles = len(cfgs)
    wh  = Warehouse_Builder().from_config(WarehouseConfig(
        total_aisles=n_aisles, aisle_splits=[1 / n_aisles] * n_aisles, aisle_configs=cfgs)).build()
    mgr = Inventory_Manager(wh)
    mgr.enqueue_all(inv.orders)

    from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
    total_bins = len(wh.bins)
    batch_cfg  = BatchConfig(inventory_size=100, mean_fraction=0.3, std_fraction=0.05)
    fills: list[float] = []

    for _ in range(30):
        mgr.check_reorders()
        Task.from_batch(Batch(batch_cfg, inv, affinity=None), wh, manager=mgr)
        fills.append(len(mgr.unavailable) / total_bins)

    first_half, second_half = mean(fills[:15]), mean(fills[15:])
    drift = second_half - first_half

    assert drift > -0.15, (
        f'fill dropped {abs(drift):.1%} over 30 batches '
        f'(first half {first_half:.1%} -> second half {second_half:.1%})')
    assert mean(fills) >= 0.30, (
        f'mean fill {mean(fills):.1%} below the 30% floor — the warehouse is emptying out')


# ═════════════════════════════════════════════════════════════════════════════
# Part G: supply_cv — the stochastic received quantity
# ═════════════════════════════════════════════════════════════════════════════
#
# These three used to assert against a TRANSCRIPTION of the sampling formula
# (`random.gauss(ideal, ideal*cv)` inline in the test), which had already drifted from
# production: `inventory_reorder.check_reorders` draws from a per-reorder
# `random.Random(f'{seed}:{sku}:{batch}')` so the received quantity is a pure function of
# the seed and independent of global-stream call order.  A test of a copy of a formula
# cannot detect that the original changed, so all three now drive `check_reorders` and read
# `mgr.units_ordered` — the real path, the real RNG.

def test_supply_cv_zero_delivers_exactly_the_ordered_quantity():
    """cv=0 must skip the sampler entirely (`qty = ideal`), not draw from a zero-variance
    normal — `round(gauss(ideal, 0))` would still be a float round-trip."""
    for on_hand in (20, 15, 10):
        _, mgr = _small_warehouse(seed=0)
        eq = 40
        c = _make_carton(sku=50 + on_hand, eq_qty=eq, rp=25, supply_cv=0.0)
        mgr.enqueue(c)

        mgr._current_quantities[c.sku] = on_hand
        mgr._depleted_skus.add(c.sku)
        triggered = mgr.check_reorders()

        assert c.sku in triggered, (
            f'no reorder at on-hand {on_hand} with reorder_point 25')
        assert mgr.units_ordered == eq - on_hand, (
            f'on-hand {on_hand}: ordered {mgr.units_ordered}, expected exactly '
            f'{eq - on_hand} with supply_cv=0')


def test_supply_cv_above_zero_varies_the_received_quantity_around_the_target():
    """cv=0.2 over 50 restocks: spread, an unbiased centre, and never a non-positive receipt.

    The RNG is keyed on `(seed, sku, batch)`, so successive batches draw different values
    even though nothing else about the SKU changes — that is what makes this loop a real
    sample rather than 50 copies of one draw.
    """
    _, mgr = _small_warehouse(seed=12)
    mgr._seed = 1234                                # keys the per-reorder RNG
    eq, on_hand = 100, 30
    ideal = eq - on_hand                            # 70
    mgr.enqueue(_make_carton(sku=60, eq_qty=eq, rp=40, supply_cv=0.20))

    received = []
    for _ in range(50):
        mgr._current_quantities[60] = on_hand
        _clear_in_flight(mgr, 60)
        mgr._depleted_skus.add(60)
        mgr.check_reorders()
        received.append(mgr.units_ordered)

    assert len(set(received)) >= 5, (
        f'only {len(set(received))} distinct quantities across 50 restocks — the sampler is '
        f'not varying (a fixed RNG key would do this)')
    assert all(r >= 1 for r in received), f'minimum received {min(received)}, floor is 1'
    avg = mean(received)
    assert abs(avg - ideal) / ideal < 0.20, (
        f'mean received {avg:.1f} vs target {ideal} — off-centre by '
        f'{abs(avg - ideal) / ideal:.1%}, more than the 20% sampling band')


def test_the_receipt_floor_holds_even_at_an_absurd_supply_cv():
    """cv=2.0 makes the normal draw negative roughly a third of the time; `max(1, ...)`
    is the only thing between that and a zero-or-negative-unit StorageUnit.

    The old version of this test hard-returned on the first violation after printing FAIL,
    so it reported PASS whether or not the floor held.  Asserting inside the loop names the
    exact iteration instead.
    """
    _, mgr = _small_warehouse(seed=12)
    mgr._seed = 999
    mgr.enqueue(_make_carton(sku=70, eq_qty=5, rp=2, supply_cv=2.0))

    for i in range(50):
        mgr._current_quantities[70] = 1
        _clear_in_flight(mgr, 70)
        mgr._depleted_skus.add(70)
        mgr.check_reorders()
        assert mgr.units_ordered >= 1, (
            f'restock {i} received {mgr.units_ordered} units at supply_cv=2.0 — the '
            f'max(1, ...) floor did not hold')


def test_supply_cv_is_generated_per_sku_and_survives_a_db_round_trip():
    """A single shared cv would make every SKU's supply equally (un)reliable, which is not
    what `supply_cv_mean` is for — and a dropped column would silently restore that."""
    inv = _profile(50, seed=5, supply_cv_mean=0.15)

    negatives = [(c.sku, c.supply_cv) for c in inv.orders if getattr(c, 'supply_cv', -1) < 0]
    assert not negatives, f'negative supply_cv (a negative std-dev): {negatives[:3]}'
    distinct = {round(c.supply_cv, 4) for c in inv.orders}
    assert len(distinct) > 1, (
        f'all 50 SKUs share supply_cv={distinct} — it is not being drawn per SKU')

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        save_inventory_to_db(inv, db_path, {})
        loaded = {c.sku: c.supply_cv for c in load_inventory_from_db(db_path).orders}
        mismatches = [(s, c.supply_cv, loaded.get(s))
                      for c in inv.orders
                      if abs(c.supply_cv - loaded.get(c.sku, -1)) > _TOL]
        assert not mismatches, (
            f'{len(mismatches)} supply_cv values changed across save+load; '
            f'(sku, saved, loaded) = {mismatches[:3]}')
    finally:
        os.unlink(db_path)
