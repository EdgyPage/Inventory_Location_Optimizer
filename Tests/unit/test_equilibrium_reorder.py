"""test_equilibrium_reorder.py — the Order-Up-To (OUP) equilibrium replenishment model.

The model, end to end
---------------------
At profile-generation time each SKU gets its demand and its supply side, and NOTHING ELSE:

    expected_batch_demand = relative_frequency * quantity_rate
    lead_time_mean        = mean batches before a placed order arrives
    supply_cv             = coefficient of variation of the received quantity

A LEVEL IS NOT A SKU'S FACT (ADR-0002, `docs/adr/0002-the-catalogue-carries-no-stock-levels
.md`).  The generator used to author `equilibrium_qty = coverage_batches x
expected_batch_demand` and a matching `reorder_point`; it no longer authors either.  A run
DECLARES them at setup — `Order.declare_stock` is the one mutation site for all four level
slots (`equilibrium_qty`, `reorder_point`, `stock_plan`, `pipeline_qty`), and on a freshly
generated or catalogue-loaded order those slots are UNSET, not defaulted.  Part A is the
contract that says so; Part B is the two DB round trips (a catalogue declaring nothing, and
a run's own declaration).  Everything from Part C down declares its levels by hand and then
tests reorder BEHAVIOUR, which the ADR did not change.

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

import inspect
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
from Warehouse.generation import generate_inventory as _gen
from Warehouse.generation.generate_inventory import (
    build_inventory_with_profile,
    build_inventory_from_plan,
    generate_run,
    save_inventory_to_db,
    load_inventory_from_db,
    DEFAULT_DIM_SPEC,
    DEFAULT_WEIGHT_SPEC,
)
from Warehouse.inventory.Inventory_Management import Inventory_Manager, _equilibrium_qty
from Warehouse.inventory.inventory_common import UndeclaredStock
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

_TOL = 1e-9

#: The four slots `Order.declare_stock` writes and nothing else may.  A generated or
#: catalogue-loaded order carries NONE of them (ADR-0002).
_LEVEL_SLOTS = ('equilibrium_qty', 'reorder_point', 'stock_plan', 'pipeline_qty')

#: The generator knobs retired with the authored level.  Names, not values: the point is that
#: no builder still accepts one under any spelling.
_RETIRED_KNOBS = frozenset({'equilibrium_coverage_batches', 'reorder_safety_batches',
                            'coverage_batches'})


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
    """An Order with a hand-declared stock level — no DB, no profile generation.

    The physical/demand fields are set directly (this stands in for a catalogue SKU); the
    LEVEL goes through `declare_stock`, the one mutation site (ADR-0002).  Every number is
    identical to what this helper used to assign by hand — `declare_stock`'s clamps
    (Q >= 1; 1 <= rp <= Q-1) are no-ops on every (eq_qty, rp) pair used below — so the
    behaviour tests that consume it are unchanged.
    """
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
    c.lead_time_mean        = lt
    c.supply_cv             = supply_cv
    c.expected_batch_demand = 0.8 * 4.0
    return c.declare_stock(eq_qty, rp)


def _profile(num_skus, seed, **kw):
    """A generated catalogue.  It declares NO stock level — see `_declare_run_levels`."""
    return build_inventory_with_profile(
        num_skus=num_skus, seed=seed,
        handling_splits=[0.5, 0.5],
        category_splits=[1 / 6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        **kw)


def _declare_run_levels(orders, coverage: float = 10.0, safety: float = 2.0):
    """Declare a run's levels over a generated catalogue — what a real run does at setup.

    The catalogue carries none (ADR-0002), and `_equilibrium_qty` RAISES rather than
    defaulting, so anything that stocks a warehouse has to declare first.  The sizing used
    here is deliberately the formula the generator used to author, so the system-level
    regression in Part F is numerically the same test it was before the split; what changed
    is WHO says it.
    """
    for c in orders:
        eq = max(1, round(coverage * c.expected_batch_demand))
        rp = max(1, min(eq - 1, round(c.expected_batch_demand * (c.lead_time_mean + safety))))
        c.declare_stock(eq, rp)
    return orders


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
# Part A: what build_inventory_with_profile must produce — and what it must NOT
# ═════════════════════════════════════════════════════════════════════════════
#
# This part used to assert that every generated SKU carried `equilibrium_qty` /
# `reorder_point` and that both matched the generator's coverage-in-batches formulas.  That
# subject is GONE (ADR-0002): the generator authors no level.  The coverage moved rather
# than vanished — the same three tests now pin the replacement contract, which nothing else
# in the suite pins:  the demand/supply fields are still all there, the four level slots are
# genuinely UNSET (not defaulted, not None), and the retired knobs are gone from every
# builder signature so a caller who still passes one is told.

def test_every_generated_sku_carries_demand_and_supply_but_declares_no_stock():
    """All 100 SKUs, not a spot check.

    The catalogue's half of the contract: a SKU's own facts (geometry, demand, lead time,
    supply reliability) are complete, and `stock_declared()` is False on every one of them.
    A generator that started authoring a level again — or that quietly dropped `supply_cv` —
    fails here rather than three layers down.

    `stock_qty` must be GONE: it is `_equilibrium_qty`'s documented legacy fallback, so a
    lingering copy would answer for an order nobody declared, which is precisely the silence
    ADR-0002 removed.

    (The old harness `break`-ed after the first SKU because each check printed a line.
    Asserting is free, so all 100 are checked.)
    """
    inv = _profile(100, seed=42, lead_time_mean_batches=2.0, supply_cv_mean=0.15)
    assert len(inv.orders) == 100, len(inv.orders)

    for c in inv.orders:
        assert c.expected_batch_demand > 0.0, (
            f'sku={c.sku} expected_batch_demand={c.expected_batch_demand}')
        assert 0.0 < c.demand.relative_frequency <= 1.0, (
            f'sku={c.sku} relative_frequency={c.demand.relative_frequency} outside (0, 1]')
        assert Order.MIN_QTY <= c.demand.quantity_rate <= Order.MAX_QTY, (
            f'sku={c.sku} quantity_rate={c.demand.quantity_rate} outside the clamp')
        assert c.lead_time_mean >= 0.0, f'sku={c.sku} lead_time_mean={c.lead_time_mean}'
        assert c.supply_cv >= 0.0, (
            f'sku={c.sku} supply_cv={c.supply_cv} — a negative standard deviation')
        assert not hasattr(c, 'stock_qty'), (
            f'sku={c.sku} carries the legacy stock_qty={c.stock_qty} — '
            f'_equilibrium_qty would silently fall back to it')
        assert not c.stock_declared(), (
            f'sku={c.sku} came out of the generator with a stock declaration '
            f'(equilibrium_qty={c.equilibrium_qty}) — the catalogue carries no levels')

    # ...and the supply side actually VARIES, or the loop above would pass on a generator
    # that hard-coded every SKU's lead and cv to zero.
    assert any(c.lead_time_mean > 0.0 for c in inv.orders), 'no SKU drew a positive lead time'
    assert len({round(c.supply_cv, 6) for c in inv.orders}) > 1, 'supply_cv is not per-SKU'
    assert len({round(c.expected_batch_demand, 6) for c in inv.orders}) > 1, (
        'every SKU has the same expected_batch_demand — demand does not discriminate, so '
        'every level a run derives from it would be identical too')


def test_the_four_level_slots_are_unset_until_a_run_declares_them():
    """The property the whole ADR rests on: UNSET, not defaulted.

    `Order` is a `__slots__` class, so an unwritten level slot RAISES AttributeError — that
    is what makes `stock_declared()` a real answer and what makes `_equilibrium_qty` able to
    refuse.  A well-meaning `= None` or `= 1` in any construction path would turn every one
    of those refusals back into a silent default, and nothing else in the suite would notice:
    a warehouse sized at one unit per SKU builds, runs and reports.
    """
    inv = _profile(25, seed=42)

    for c in inv.orders:
        for slot in _LEVEL_SLOTS:
            with pytest.raises(AttributeError):
                getattr(c, slot)
        assert not c.stock_declared(), f'sku={c.sku} stock_declared() with every slot unset'

    # `declare_stock` is the one thing that fills them, and it fills ALL four.
    c = inv.orders[0]
    assert c.declare_stock(20, 8) is c, 'declare_stock must return self (it is chained)'
    assert c.stock_declared()
    assert (c.equilibrium_qty, c.reorder_point) == (20, 8)
    assert c.stock_plan is None and c.pipeline_qty is None
    # ...carrying the clamps `Order.build` used to apply: Q >= 1, 1 <= rp <= Q-1.
    assert (_make_carton(sku=901, eq_qty=0, rp=0).equilibrium_qty,
            _make_carton(sku=902, eq_qty=0, rp=0).reorder_point) == (1, 1)
    assert _make_carton(sku=903, eq_qty=10, rp=99).reorder_point == 9, (
        'rp above the target would reorder on a full bin, every batch')


def test_the_retired_coverage_knobs_are_gone_from_every_builder():
    """A retired knob must fail LOUDLY, and the module constants must be gone with it.

    `equilibrium_coverage_batches` / `reorder_safety_batches` / `coverage_batches` are the
    coverage-in-batches conversion ADR-0002 deleted.  If a builder still accepted one — as a
    `**kwargs` sink, or as an ignored parameter kept "for compatibility" — every caller in
    the repo would keep passing 10.0 and 2.0 and get no level and no error.  `inspect
    .signature` is the check, and the `TypeError` below is the behaviour it guarantees.
    """
    for const in ('EQUILIBRIUM_COVERAGE_BATCHES', 'REORDER_SAFETY_BATCHES'):
        assert not hasattr(_gen, const), (
            f'generate_inventory.{const} is back — the authored level went with it')

    for fn in (build_inventory_with_profile, build_inventory_from_plan, generate_run):
        params = inspect.signature(fn).parameters
        still_there = _RETIRED_KNOBS & set(params)
        assert not still_there, f'{fn.__name__}{tuple(params)} still accepts {still_there}'
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()), (
            f'{fn.__name__} grew a **kwargs sink — a retired knob would vanish into it '
            f'instead of raising')

    with pytest.raises(TypeError):
        _profile(2, seed=0, equilibrium_coverage_batches=10.0)
    with pytest.raises(TypeError):
        _profile(2, seed=0, reorder_safety_batches=2.0)


# ═════════════════════════════════════════════════════════════════════════════
# Part B: DB round-trip — the catalogue declares nothing, a run declares four values
# ═════════════════════════════════════════════════════════════════════════════

def _cols(db_path: str, table: str) -> list[str]:
    """The column names of *table*.

    PRAGMA table_info on a table that does not exist returns NO ROWS rather than raising, so
    a mistyped name yields `[]` and every `x not in cols` assertion written against it passes
    for the wrong reason — that is exactly how three assertions in this file went vacuous
    once.  Every caller asserts non-emptiness first; the emptiness guard lives here so it
    cannot be forgotten at a call site.
    """
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
    finally:
        conn.close()


def _rowcount(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
    finally:
        conn.close()


def test_a_generated_catalogue_round_trips_with_an_empty_stock_levels_table():
    """Worker processes RELOAD the inventory from a DB; anything not persisted is lost —
    and, since ADR-0002, anything INVENTED on load is worse.

    The catalogue's file must come back undeclared.  A loader that filled the level slots
    with a NULL, a zero or a 1 would hand every worker a warehouse sized at nothing, and
    `stock_declared()` would answer True for a declaration nobody made.  Emptiness is the
    contract: no row in `stock_levels` means no declaration, as against a NULL column, which
    would read as an authored level that happens to be missing.
    """
    inv = _profile(50, seed=1, lead_time_mean_batches=2.0, supply_cv_mean=0.15)
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
            if abs(c.lead_time_mean - c2.lead_time_mean) > _TOL:
                mismatches.append(('lead_time_mean', sku, c.lead_time_mean, c2.lead_time_mean))
            if abs(c.supply_cv - c2.supply_cv) > _TOL:
                mismatches.append(('supply_cv', sku, c.supply_cv, c2.supply_cv))
            if abs(c.demand.relative_frequency - c2.demand.relative_frequency) > _TOL:
                mismatches.append(('relative_frequency', sku,
                                   c.demand.relative_frequency, c2.demand.relative_frequency))
        assert not mismatches, (
            f'{len(mismatches)} attribute(s) changed across save+load; '
            f'(field, sku, saved, loaded) = {mismatches[:3]}')

        undeclared = [c.sku for c in inv2.orders if c.stock_declared()]
        assert not undeclared, (
            f'{len(undeclared)} loaded SKUs came back DECLARED from a catalogue that '
            f'declares nothing; first few = {undeclared[:3]}')
        for slot in _LEVEL_SLOTS:
            with pytest.raises(AttributeError):
                getattr(inv2.orders[0], slot)

        assert _rowcount(db_path, 'stock_levels') == 0, (
            'a generated catalogue wrote rows into stock_levels — a level is a run\'s '
            'declaration, and this file makes none')

        # The table is `cartons`, NOT `orders` — the persisted name was deliberately kept
        # stable across the Carton->Order rename (generate_inventory._SCHEMA).
        cols = _cols(db_path, 'cartons')
        assert cols, 'PRAGMA table_info(cartons) returned no columns — wrong table name?'
        assert 'stock_qty' not in cols, f'legacy stock_qty column present in a new DB: {cols}'
        assert 'lead_time_mean' in cols, f'lead_time_mean column missing: {cols}'
        # The three level columns LEFT `cartons` (they are a schema vintage now).
        for gone in ('equilibrium_qty', 'reorder_point', 'stock_plan'):
            assert gone not in cols, (
                f'{gone} is back in `cartons`: the catalogue would author a level again '
                f'({cols})')

        levels = _cols(db_path, 'stock_levels')
        assert levels, 'PRAGMA table_info(stock_levels) returned no columns — table missing?'
        for want in ('sku', 'equilibrium_qty', 'reorder_point', 'stock_plan', 'pipeline_qty'):
            assert want in levels, f'{want} missing from stock_levels: {levels}'
    finally:
        os.unlink(db_path)


def test_a_declared_inventory_round_trips_all_four_level_values():
    """The other file this shape serves: a run's own `planned_inventory.db`.

    A dropped `reorder_point` column does not raise — the loaded order simply has none, and
    every worker runs a warehouse that never restocks while the main process's own numbers
    look fine.  All four values are checked per SKU, `stock_plan` included: it is a JSON
    round trip (tuples land as lists) and the loader has to rebuild the tuples, or the
    reorder repacks to a different tier mix than the run planned.
    """
    inv = _profile(30, seed=3, lead_time_mean_batches=2.0)
    want = {}
    for c in inv.orders:                      # what a run's setup would declare
        eq   = 10 + c.sku % 40
        rp   = 1 + c.sku % 5
        plan = [(False, 3, 1 + c.sku % 3), (True, 1, 2)]
        pipe = c.sku % 7
        c.declare_stock(eq, rp, stock_plan=plan, pipeline_qty=pipe)
        want[c.sku] = (eq, rp, plan, pipe)

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        save_inventory_to_db(inv, db_path, {'test': True})
        assert _rowcount(db_path, 'stock_levels') == len(inv.orders), (
            'a declared inventory must write one stock_levels row per SKU')

        loaded = {c.sku: c for c in load_inventory_from_db(db_path).orders}
        assert len(loaded) == len(want), f'{len(loaded)} SKUs loaded, {len(want)} saved'

        mismatches = []
        for sku, (eq, rp, plan, pipe) in want.items():
            c2 = loaded[sku]
            if not c2.stock_declared():
                mismatches.append(('stock_declared', sku, True, False))
                continue
            got = (c2.equilibrium_qty, c2.reorder_point, c2.stock_plan, c2.pipeline_qty)
            if got != (eq, rp, plan, pipe):
                mismatches.append(('level', sku, (eq, rp, plan, pipe), got))
        assert not mismatches, (
            f'{len(mismatches)} declaration(s) changed across save+load; '
            f'(field, sku, saved, loaded) = {mismatches[:3]}')
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
    """One helper reads the target off orders of three vintages — and REFUSES the third.

    The legacy `stock_qty` fallback is why `test_every_generated_sku_carries_demand_and_
    supply_but_declares_no_stock` asserts the attribute is GONE from generated orders: with
    the fallback in place, an undeclared SKU that happened to carry one would answer.

    The undeclared case used to answer 1.  That default is the reason it now raises: the
    catalogue carries no levels (ADR-0002), so every SKU of a freshly loaded catalogue would
    have answered 1, `bucket_requirements` would size every bucket for one unit per SKU, and
    the run would build a warehouse an order of magnitude too small — with no error, at the
    one moment nothing downstream can detect it.
    """
    assert _equilibrium_qty(_make_carton(sku=40, eq_qty=25)) == 25

    legacy = object.__new__(Order)
    legacy.stock_qty = 50
    assert _equilibrium_qty(legacy) == 50, 'legacy stock_qty fallback'

    bare = object.__new__(Order)
    with pytest.raises(UndeclaredStock):
        _equilibrium_qty(bare)

    # ...and a freshly generated SKU is exactly that shape, which is the whole point.
    generated = _profile(1, seed=0).orders[0]
    with pytest.raises(UndeclaredStock):
        _equilibrium_qty(generated)
    assert _equilibrium_qty(generated.declare_stock(17, 4)) == 17


# ═════════════════════════════════════════════════════════════════════════════
# Part F: fill stability over 30 batches (regression)
# ═════════════════════════════════════════════════════════════════════════════

def test_fill_does_not_drift_down_over_thirty_batches():
    """The system-level consequence of everything above, on a full generated profile.

    Every unit test in this file pins one rule in isolation; this one asserts they compose.
    A slow leak — a reorder that fires but under-orders, a release that drops units — shows
    up here as fill drifting down while every isolated test stays green.

    The levels are DECLARED here rather than read off the catalogue (ADR-0002), at the same
    coverage the generator used to author, so the fill numbers this asserts are the ones it
    always asserted.
    """
    inv = _profile(100, seed=42)
    _declare_run_levels(inv.orders, coverage=10.0, safety=2.0)

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
