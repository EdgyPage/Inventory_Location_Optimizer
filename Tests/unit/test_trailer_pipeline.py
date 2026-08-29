"""test_trailer_pipeline.py — the v1 trailer model, from loading to admission.

What v1 IS: fired reorders load item-by-item into trailers (FIFO next-fit at pallet and
trailer level), a lot that outgrows the open trailer continues in a fresh one, arrivals
(lead zero = instantly) emit one delivery per contiguous trailer lot, each portion packs on
its own (`inbound_split` realized structurally), and admissions carry the `'trailer'`
provenance.  What v1 is NOT: doors do not throttle, policies are FIFO, and a positive lead
without a clock fails SAFE — merchandise waits rather than teleports.

Run:  python -m pytest Tests/unit/test_trailer_pipeline.py -q
"""
from __future__ import annotations

import random

import pytest

from Inbound.pack import packer
from Inbound.priorities import DockContext, bounded_order
from Inbound.trailer import (
    LoadPallet, POSITION_VOLUME, Trailer, Trailer28, Trailer53)
from Inbound.transit import TrailerTransit
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig)


def _order(sku: int = 1, length: int = 10, width: int = 10, height: int = 8) -> Order:
    """Hand-set dimensions, the `test_inbound_load_plan` fixture shape: 800 in^3/item."""
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height = length, width, height
    c.weight = 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty = 20
    c.reorder_point = 10
    c.lead_time_mean = 0.0
    c.supply_cv = 0.0
    c.expected_batch_demand = 3.2
    return c


def _warehouse():
    Aisle.next_aisle_id = 1
    random.seed(0)
    w, h = aisle_width_for(4), aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Warehouse_Builder().from_config(cfg).build()


# ── 1. the objects: footprint arithmetic and next-fit ────────────────────────────

def test_the_types_carry_the_decided_positions():
    assert Trailer53.pallet_positions == 26
    assert Trailer28.pallet_positions == 12
    assert Trailer53.capacity() == 26 * POSITION_VOLUME
    assert POSITION_VOLUME == 48 ** 3


def test_a_pallet_is_next_fit_and_merges_contiguous_lots():
    p = LoadPallet()
    per = POSITION_VOLUME // 4          # four items fill one pallet exactly
    assert p.add(1, 3, per) == 3
    assert p.add(1, 5, per) == 1        # same SKU continues the lot...
    assert p.lots == [[1, 4, per]]      # ...and merges rather than fragmenting
    assert p.add(2, 1, per) == 0        # full: next-fit closes, never squeezes


def test_a_trailer_fills_pallet_by_pallet_and_stops_at_its_positions():
    t = Trailer(Trailer28, seq=0)
    per = POSITION_VOLUME               # one item = one whole pallet position
    assert t.load(7, 12, per) == 12     # exactly the pup's 12 positions
    assert len(t.pallets) == 12
    assert t.load(7, 1, per) == 0       # position 13 does not exist
    assert t.sku_totals() == {7: 12}


# ── 2. transit: FIFO next-fit across trailers, per-trailer deliveries ────────────

def test_a_lot_that_outgrows_the_trailer_continues_in_a_fresh_one():
    tr = TrailerTransit(Trailer28, lead_s=0.0)
    per = POSITION_VOLUME
    tr.dispatch(1, 15, 0, unit_volume=per)      # 12 fit the pup, 3 ride the next
    deliveries = tr.release()
    assert deliveries == [[1, 12, 0], [1, 3, 0]], (
        'each trailer portion is its OWN delivery — that split is the model')


def test_fifo_loading_keeps_lots_contiguous_and_in_order():
    tr = TrailerTransit(Trailer53, lead_s=0.0)
    per = POSITION_VOLUME // 2
    tr.dispatch(1, 3, 0, unit_volume=per)
    tr.dispatch(2, 2, 0, unit_volume=per)
    tr.dispatch(3, 1, 0, unit_volume=per)
    assert tr.release() == [[1, 3, 0], [2, 2, 0], [3, 1, 0]]


def test_a_positive_lead_without_a_clock_fails_safe():
    tr = TrailerTransit(Trailer53, lead_s=600.0)
    tr.dispatch(1, 2, 0, unit_volume=100)
    assert tr.release(None) == [], 'no clock -> merchandise WAITS, never teleports'
    assert tr.merchandise() == 2
    assert tr.release(now_s=0.0) == [], 'dispatched with no clock: due date unknowable'


def test_a_lead_elapses_against_the_absolute_clock():
    tr = TrailerTransit(Trailer53, lead_s=600.0)
    tr.dispatch(1, 2, 0, unit_volume=100, now_s=1000.0)
    assert tr.release(now_s=1500.0) == [], '500 s of a 600 s lead elapsed'
    assert tr.release(now_s=1600.1) == [[1, 2, 0]]
    assert tr.merchandise() == 0


def test_the_census_counts_what_has_not_reached_the_dock():
    tr = TrailerTransit(Trailer28, lead_s=0.0)
    per = POSITION_VOLUME
    tr.dispatch(1, 14, 0, unit_volume=per)      # one full pup departed + 2 on the open one
    assert tr.depth == 2
    assert tr.merchandise() == 14
    assert sorted(tr.snapshot()) == [(1, 2, 1), (1, 12, 1)]
    tr.release()
    assert tr.depth == 0 and tr.merchandise() == 0 and tr.snapshot() == []


# ── 3. the ordering bound (the k_cap analog, inert under fifo) ───────────────────

def test_the_bound_is_inert_under_fifo_and_bites_under_a_real_key():
    ctx = DockContext(doors=4, free_doors=4, yard_depth=3)
    trailers = [Trailer(Trailer53, seq=i) for i in range(3)]
    fifo = lambda t, c: -float(t.seq)
    biggest_last = lambda t, c: float(t.seq)          # prefers the NEWEST
    assert [t.seq for t in bounded_order(trailers, fifo, ctx, None)] == [0, 1, 2]
    assert [t.seq for t in bounded_order(trailers, fifo, ctx, 1)] == [0, 1, 2]
    assert [t.seq for t in bounded_order(trailers, biggest_last, ctx, None)] == [2, 1, 0]
    assert [t.seq for t in bounded_order(trailers, biggest_last, ctx, 1)] == [0, 1, 2], (
        'bound=1 is strict arrival order whatever the policy prefers — k_cap semantics')
    assert [t.seq for t in bounded_order(trailers, biggest_last, ctx, 2)] == [1, 2, 0]


# ── 4. through the manager: provenance, conservation, per-portion packing ────────

def test_trailer_admissions_carry_the_fourth_source_and_conserve_merchandise():
    mgr = Inventory_Manager(_warehouse())
    o = _order(sku=101)
    mgr._originals[101] = o
    mgr.transit = TrailerTransit(Trailer53, lead_s=0.0)
    mgr.packer = packer
    mgr.transit.dispatch(101, 6, 0, unit_volume=o.volume())
    plans = mgr._release_arrivals()
    assert [p.received for p in plans] == [6]
    items = list(mgr._stock_queue)
    assert items and all(i.source == 'trailer' for i in items)
    assert sum(u.quantity for u in (i.unit for i in items)) == 6


def test_per_trailer_portions_pack_as_the_pieces_they_are():
    """A reorder split across two pups packs per portion — six items whole pack a pallet,
    the same six as 12+... portions pack what each portion is.  Compared directly against
    the packer ground truth so a regression cannot hide behind the pipeline."""
    mgr = Inventory_Manager(_warehouse())
    o = _order(sku=101)
    mgr._originals[101] = o
    per_item = o.volume()
    # capacity quantizes per PALLET (perfect packing within 48^3, waste between
    # pallets is real): items-per-pallet x positions, never trailer-volume // item.
    fits = (POSITION_VOLUME // per_item) * Trailer28.pallet_positions
    qty = fits + 3                                   # forces a two-trailer split
    mgr.transit = TrailerTransit(Trailer28, lead_s=0.0)
    mgr.packer = packer
    mgr.transit.dispatch(101, qty, 0, unit_volume=per_item)
    plans = mgr._release_arrivals()
    assert [p.received for p in plans] == [fits, 3]
    want = [u.unit_category for q in (fits, 3)
            for u in viable_storage_units(o.reorder(), q)]
    got = [u.unit_category for p in plans for u in p.units]
    assert got == want


def test_the_ledger_debits_every_portion_of_a_split_reorder():
    mgr = Inventory_Manager(_warehouse())
    o = _order(sku=101)
    mgr._originals[101] = o
    mgr.transit = TrailerTransit(Trailer28, lead_s=0.0)
    mgr.packer = packer
    per_item = o.volume()
    qty = (POSITION_VOLUME // per_item) * Trailer28.pallet_positions + 3
    mgr._deferred_qty[101] = qty                     # what _fire_reorders would credit
    mgr.transit.dispatch(101, qty, 0, unit_volume=per_item)
    mgr._release_arrivals()
    assert mgr._deferred_qty[101] == 0, 'both portions debited, nothing double-counted'
