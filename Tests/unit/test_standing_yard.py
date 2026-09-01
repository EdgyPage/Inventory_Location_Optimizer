"""test_standing_yard.py — doors become real, and flag-off stays byte-identical.

The standing yard (`YardTransit` + the manager's `_receive_standing`) changes WHERE
merchandise waits — on the trailer, behind real doors — and WHO decides — drain-frozen
yard/dock rankings, door-team crews.  What it must NOT change is placement physics, and
this file pins the four byte-identity layers the design decided:

  1. Flag-off binds the v1 classes untouched — identity by construction, held by the
     existing pipeline tests, not re-proven here.
  2. THE DEGENERATE LOCKSTEP: standing-on, FIFO policies, doors >= every trailer that
     ever stands, no receiving cap, allocation='merged' produces the IDENTICAL event
     stream as the v1 drain — records, queue stream, ledgers, censuses, ages — compared
     drain by drain, never as aggregates (aggregates have hidden shape differences here
     before).
  3. THE CONTAINMENT PROPERTY: the same degenerate configuration with allocation='split'
     is identical EXCEPT the dock records' t0/worker — crew allocation is labor-only.
  4. Under a receiving cap the models legitimately diverge in the unloaded set (split
     makes partial progress on every staged trailer; merged completes top-ranked
     trailers first).  That divergence is the FEATURE and is deliberately never
     equality-tested; what is tested is that flows conserve and levels relabel honestly
     (v1's floor remainder is queued; the standing remainder is deferred).

Run:  python -m pytest Tests/unit/test_standing_yard.py -q
"""
from __future__ import annotations

import random

import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.pack import packer
from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import TrailerTransit, YardTransit
from Inbound.unload import UnloadCost
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig)


def _order(sku: int, weight: float = 2.0) -> Order:
    """The `test_trailer_pipeline` fixture shape: hand-set dims, 800 in^3/item."""
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height = 10, 10, 8
    c.weight = weight
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


def _manager(transit, crew: int = 2, skus=(101, 102, 103), weights=None):
    mgr = Inventory_Manager(_warehouse())
    weights = weights or {}
    for sku in skus:
        mgr._originals[sku] = _order(sku, weight=weights.get(sku, 2.0))
    mgr.transit = transit
    mgr.packer = packer
    mgr.enable_receiving(Dock(DockSpec(size=crew, sources=('reorder', 'trailer'))))
    return mgr


def _dispatch(mgr, sku: int, qty: int, vol: int, epoch: float) -> None:
    """One fired reorder, exactly as `_fire_reorders` hands it to the transit."""
    mgr.transit.dispatch(sku, qty, 0, unit_volume=vol, now_s=epoch)
    mgr._deferred_qty[sku] = mgr._deferred_qty.get(sku, 0) + qty


def _drain(mgr, epoch: float, deadline=None):
    """One calendar-plus-labour pass: phases 5 and 6, exactly as check_reorders runs
    them (the put drain is deliberately left out — the queue STREAM is compared instead,
    and placement is a pure function of it)."""
    mgr._now_s = epoch
    plans = mgr._release_arrivals()
    mgr._receive(plans, deadline)


def _queue_stream(mgr) -> list:
    """The put-queue stream: everything placement physics can see, in order."""
    return [(i.source, i.age, i.unit.order.sku, i.unit.quantity, i.unit.unit_category)
            for i in mgr._stock_queue]


def _ledgers(mgr) -> tuple:
    return (dict(mgr._deferred_qty), dict(mgr._queued_qty),
            dict(mgr._queued_sku_counts))


# ── the scripted scenario both lockstep tests replay ──────────────────────────────
#  Drain 1: a reorder that SPLITS across trailers (s1: 12 fill a pup, 3 continue) plus a
#  second SKU sharing the open trailer — two trailers standing at once, a multi-lot
#  trailer, and a split shipment packing as its pieces.  Drain 2: quiet (nothing
#  dispatched — an empty yard must be a no-op).  Drain 3: a third SKU at another volume.

def _run_scenario(mgr, deadline=None) -> list:
    per_drain = []
    epochs = (10_000.0, 20_000.0, 30_000.0)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epochs[0])
    _dispatch(mgr, 102, 4, POSITION_VOLUME, epochs[0])
    for b, epoch in enumerate(epochs):
        if b == 2:
            _dispatch(mgr, 103, 5, POSITION_VOLUME // 2, epoch)
        _drain(mgr, epoch, deadline)
        per_drain.append({
            'records': mgr.drain_receiving_records(),
            'snapshot': mgr.receiving_snapshot(),
            'transit': sorted(mgr.transit.snapshot()),
            'carryover': sorted(mgr.carryover_rows(b)),
            'queue': _queue_stream(mgr),
            'ledgers': _ledgers(mgr),
            'recv_seconds': mgr.receiving_seconds,
        })
    return per_drain


# ── 1. the config contradictions fail loudly ──────────────────────────────────────

def test_standing_without_a_trailer_type_fails_loudly(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_trailer_type', None)
    with pytest.raises(ValueError, match='INBOUND_TRAILER_TYPE'):
        inbound_spec()


def test_standing_without_a_receiving_crew_fails_loudly(monkeypatch):
    """A yard nobody can unload defers its merchandise forever with nothing raising —
    the exact silent-death the loud guard exists for."""
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 0)
    with pytest.raises(ValueError, match='RECV_CREW_SIZE'):
        inbound_spec()


def test_a_wellformed_standing_spec_carries_the_whole_family(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    spec = inbound_spec()
    assert spec['standing'] is True
    assert spec['allocation'] == 'split', 'split is the standing default, by decision'
    assert (spec['yard_policy'], spec['dock_policy']) == ('fifo', 'fifo')
    assert (spec['unload_intercept'], spec['unload_weight_coef'],
            spec['unload_volume_coef']) == (None, None, None)


def test_an_unknown_allocation_fails_at_both_seams(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'inbound_crew_allocation', 'gang')
    with pytest.raises(ValueError, match='gang'):
        inbound_spec()
    with pytest.raises(ValueError, match='gang'):
        YardTransit(Trailer28, allocation='gang')


def test_flag_off_spec_is_unchanged():
    """Defaults produce no spec at all — the structural no-op the archive rides on."""
    from Optimization.config.sim_config import inbound_spec
    assert inbound_spec() is None


def test_the_unload_coefficients_default_by_reference_and_override_cleanly():
    from Warehouse.operations.putaway import PutawayCost
    base = UnloadCost()
    assert (base.intercept, base.weight_coef, base.volume_coef) == (
        PutawayCost.intercept, PutawayCost.weight_coef, PutawayCost.volume_coef)
    own = UnloadCost(intercept=PutawayCost.intercept * 3)
    d_base = Dock(DockSpec(size=1))
    d_own = Dock(DockSpec(size=1), cost=own)
    assert d_own.unload_seconds(2.0, 800.0, 4) > d_base.unload_seconds(2.0, 800.0, 4)


# ── 2. the calendar: release() delivers nothing, stamps are event-accurate ────────

def test_release_is_pure_calendar_and_stamps_the_event_not_the_drain():
    tr = YardTransit(Trailer28, lead_s=600.0)
    tr.dispatch(1, 2, 0, unit_volume=100, now_s=1000.0)
    assert tr.release(now_s=1500.0) == [], '500 s of a 600 s lead elapsed'
    # observed LATE — the drain runs at 5000 — but the trailer arrived at 1600
    assert tr.release(now_s=5000.0) == [], 'the standing release never delivers'
    (trailer,) = tr._yard
    assert trailer.arrived_s == pytest.approx(1600.0), (
        'decisions are drain-quantized; DATA is event-stamped')
    assert tr.merchandise() == 2, 'standing in the yard is still deferred merchandise'


def test_the_yard_is_ordered_by_arrival_stamp_with_seq_as_tiebreak():
    tr = YardTransit(Trailer28, lead_s=0.0)
    per = POSITION_VOLUME
    tr.dispatch(1, 12, 0, unit_volume=per, now_s=0.0)
    tr.dispatch(2, 12, 0, unit_volume=per, now_s=0.0)
    tr.release(now_s=0.0)
    yard = list(tr._yard)
    assert [t.seq for t in yard] == [0, 1], 'equal stamps fall back to seq'
    yard[0].arrived_s = 50.0        # re-stamp: seq 0 now arrived LATER
    ctx = tr.freeze_ctx()
    assert [t.seq for t in tr.yard_order(ctx)] == [1, 0], (
        'FIFO ranks by arrival stamp, not dispatch order')


def test_the_registries_resolve_an_ordering_entry_through_both_frozen_rankings():
    """The seam end-to-end: an @ordering entry registered under YARD/DOCK resolves
    through the accessors and reproduces the seeded 'fifo' rankings on the frozen ctx —
    `yard_order` and `dock_order` untouched, kind-blind by construction."""
    from Inbound.priorities import DOCK_POLICIES, YARD_POLICIES, ordering, yard_key
    fifo = yard_key('fifo')

    @ordering
    def keysort(cands, ctx):
        return sorted(cands, key=lambda t: -fifo(t, ctx))

    @ordering
    def reversed_keysort(cands, ctx):
        return sorted(cands, key=lambda t: fifo(t, ctx))

    for reg in (YARD_POLICIES, DOCK_POLICIES):
        reg['keysort-fifo'] = keysort
        reg['keysort-reversed'] = reversed_keysort
    try:
        tr_key = YardTransit(Trailer28, lead_s=0.0)
        tr_ord = YardTransit(Trailer28, lead_s=0.0,
                             yard_policy='keysort-fifo', dock_policy='keysort-fifo')
        tr_rev = YardTransit(Trailer28, lead_s=0.0, yard_policy='keysort-reversed',
                             dock_policy='keysort-reversed')
        for tr in (tr_key, tr_ord, tr_rev):
            # seq 0 dispatched (and arriving) LATER than seq 1 — a real out-of-seq
            # arrival through the legit machinery, so fifo has something to reorder.
            tr.dispatch(1, 12, 0, unit_volume=POSITION_VOLUME, now_s=100.0)
            tr.dispatch(2, 12, 0, unit_volume=POSITION_VOLUME, now_s=0.0)
            tr.release(now_s=200.0)
        ranks = []
        for tr in (tr_key, tr_ord, tr_rev):
            ctx = tr.freeze_ctx()
            ranks.append([t.seq for t in tr.yard_order(ctx)])
        assert ranks[0] == ranks[1] == [1, 0], (
            'the ordering entry must reproduce the seeded fifo YARD ranking')
        assert ranks[2] == [0, 1], (
            'a REVERSING entry must actually differ — the registered proposal drives '
            'the ranking; an identity pass-through would ride the pre-sorted yard')
        docks = []
        for tr in (tr_key, tr_ord, tr_rev):
            ctx = tr.freeze_ctx()
            for t in tr.yard_order(ctx):
                tr.stage(t, 200.0)
            docks.append([t.seq for t in tr.dock_order(tr.freeze_ctx())])
        assert docks[0] == docks[1] == [1, 0], (
            'the ordering entry must reproduce the seeded fifo DOCK ranking')
        assert docks[2] == [0, 1], 'the reversing entry must differ at the DOCK too'
    finally:
        for reg in (YARD_POLICIES, DOCK_POLICIES):
            reg.pop('keysort-fifo', None)
            reg.pop('keysort-reversed', None)


# ── 3. the degenerate lockstep: merged == the v1 drain, stream for stream ─────────

def test_the_degenerate_lockstep_merged_is_byte_identical_to_v1():
    v1 = _manager(TrailerTransit(Trailer28, lead_s=0.0), crew=2)
    yd = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4, allocation='merged'),
                  crew=2)
    got_v1 = _run_scenario(v1)
    got_yd = _run_scenario(yd)
    for b, (a, s) in enumerate(zip(got_v1, got_yd)):
        assert s['records'] == a['records'], f'drain {b}: unload records diverged'
        assert s['snapshot'] == a['snapshot'], f'drain {b}: (depth, unloaded, cut, s)'
        assert s['transit'] == a['transit'], f'drain {b}: transit census diverged'
        assert s['carryover'] == a['carryover'], f'drain {b}: carryover diverged'
        assert s['queue'] == a['queue'], f'drain {b}: the put-queue STREAM diverged — '
        assert s['ledgers'] == a['ledgers'], f'drain {b}: a ledger diverged'
        assert s['recv_seconds'] == pytest.approx(a['recv_seconds'])
    # non-vacuity: the scenario did real work through real machinery
    assert sum(len(d['records']) for d in got_v1) > 0
    assert got_v1[0]['ledgers'][1], 'nothing was ever queued — the scenario is dead'
    assert not v1._dock.items and not yd._dock.items, (
        'the standing dock floor must stay EMPTY — the buffer is the trailer')


# ── 4. containment: split changes labor stamps ONLY ───────────────────────────────

def test_split_allocation_is_labor_only_against_v1():
    v1 = _manager(TrailerTransit(Trailer28, lead_s=0.0), crew=2)
    sp = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4, allocation='split'),
                  crew=2)
    got_v1 = _run_scenario(v1)
    got_sp = _run_scenario(sp)
    t0_or_worker_moved = False
    for b, (a, s) in enumerate(zip(got_v1, got_sp)):
        assert len(s['records']) == len(a['records']), f'drain {b}: row count moved'
        for i, (rv, rs) in enumerate(zip(a['records'], s['records'])):
            # rows are (t0, dur, sku, qty, worker): everything but t0/worker is pinned
            assert rs[1:4] == rv[1:4], (
                f'drain {b} row {i}: split moved (dur, sku, qty) — that is placement '
                f'physics, not labor')
            if rs[0] != rv[0] or rs[4] != rv[4]:
                t0_or_worker_moved = True
        assert s['snapshot'][:3] == a['snapshot'][:3]
        assert s['snapshot'][3] == pytest.approx(a['snapshot'][3])
        assert s['transit'] == a['transit'], f'drain {b}'
        assert s['carryover'] == a['carryover'], f'drain {b}'
        assert s['queue'] == a['queue'], (
            f'drain {b}: the handoff must be CANONICAL merged order, never '
            f'labor-completion order')
        assert s['ledgers'] == a['ledgers'], f'drain {b}'
    assert t0_or_worker_moved, (
        'no t0/worker differed anywhere — the containment test is vacuous (the scenario '
        'never staged two trailers against a crew of two)')


# ── 5. the door-team mechanics: the deal, the pull, the reassignment ──────────────

def test_the_freed_team_takes_the_staged_trailer_with_no_workers():
    """doors=2, crew=2, three trailers: T0 small, T1 big, T2 (small) waits in the
    yard.  T0's team finishes first, the yard-pull stages T2 at that INSTANT, and the
    freed team takes it (reassignment step 1) — T2 is worked by worker 0 alone, and
    worker 0 never touches T1 while T2 still stands.  The separator is PACK COUNT
    (unload time is intercept-per-pack dominated), not weight — the cost model is
    log-compressed in weight and cannot spread trailers reliably."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split')
    mgr = _manager(tr, crew=2)
    epoch = 10_000.0
    per = POSITION_VOLUME
    _dispatch(mgr, 101, 12, per, epoch)            # T0 (seq 0): few packs
    _dispatch(mgr, 102, 120, per // 10, epoch)     # T1 (seq 1): ~10x the packs
    _dispatch(mgr, 103, 24, per // 2, epoch)       # T2 (seq 2): waits — only 2 doors
    _drain(mgr, epoch)
    recs = mgr.drain_receiving_records()
    by_sku: dict = {}
    for t0, dur, sku, qty, w in recs:
        by_sku.setdefault(sku, []).append((t0, dur, w))
    # the physics precondition that makes the assertions non-vacuous: T2 finishes on
    # worker 0 before T1 finishes on worker 1, so no other reassignment fires first
    tot = {sku: sum(d for _t, d, _w in rows) for sku, rows in by_sku.items()}
    assert tot[101] + tot[103] < tot[102], (
        f'weights no longer separate the trailers ({tot}) — retune the fixture')
    assert {w for _t, _d, w in by_sku[101]} == {0}, 'the deal: top rank takes worker 0'
    assert {w for _t, _d, w in by_sku[103]} == {0}, (
        'reassignment step 1: the freed team takes the no-worker trailer')
    # worker 0 may legitimately join T1 AFTER T2 empties (that is step 3); what would
    # break the deal is worker 0 starting T1 work while its own trailers still stood.
    t2_done_local = max(t + d for t, d, _w in by_sku[103])
    w0_on_t1 = [t for t, _d, w in by_sku[102] if w == 0]
    assert all(t >= t2_done_local for t in w0_on_t1), (
        'worker 0 touched the heavy trailer while its own work still stood')
    # doors freed STAGGERED, and the data is event-stamped: T2 staged at the exact
    # instant T0 emptied (epoch + crew-clock offset), not at the drain boundary.
    stamps = {seq: (a, s, e) for seq, a, s, e, _status in tr.stamps}
    t0_emptied = stamps[0][2]
    t2_staged = stamps[2][1]
    assert t2_staged == pytest.approx(t0_emptied)
    assert t0_emptied == pytest.approx(epoch + max(t + d for t, d, _w in by_sku[101]))
    # everything unloaded: every door freed, nothing standing
    assert tr.staged() == [] and tr.depth == 0 and tr.merchandise() == 0


def test_with_no_replacement_the_freed_team_joins_the_fewest_workers():
    """Two trailers, no yard: when T0 empties there is no no-worker staged trailer and
    no replacement, so step 3 sends worker 0 into T1 and both workers finish it."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split')
    mgr = _manager(tr, crew=2, skus=(101, 102))
    epoch = 10_000.0
    _dispatch(mgr, 101, 12, POSITION_VOLUME, epoch)        # T0: few packs, empties early
    _dispatch(mgr, 102, 120, POSITION_VOLUME // 10, epoch) # T1: ~10x the packs
    _drain(mgr, epoch)
    recs = mgr.drain_receiving_records()
    assert {w for _t, _d, sku, _q, w in recs if sku == 101} == {0}
    assert {w for _t, _d, sku, _q, w in recs if sku == 102} == {0, 1}, (
        'the freed worker never rejoined the standing heavy work — step 3 is dead')
    assert tr.depth == 0 and tr.merchandise() == 0


# ── 6. the whistle: zero-budget door-fill, the cut, and the standing remainder ────

def test_a_zero_budget_drain_still_fills_the_doors():
    """Staging is yard-jockey work, not receiving labour: with the whistle already
    blown, doors fill, nothing unloads, and the cut counts STAGED remainders only —
    the yard is never cut (waiting there is calendar, the fee's domain)."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split')
    mgr = _manager(tr, crew=2)
    epoch = 10_000.0
    per = POSITION_VOLUME
    for sku, qty in ((101, 12), (102, 12), (103, 12)):   # three trailers, two doors
        _dispatch(mgr, sku, qty, per, epoch)
    _drain(mgr, epoch, deadline=0.0)
    assert len(tr.staged()) == 2, 'the door-fill must not be budget-gated'
    assert all(t.staged_s == epoch for t in tr.staged())
    assert mgr._dock.unloaded == 0 and mgr.drain_receiving_records() == []
    staged_packs = sum(len(t.pending) for t in tr.staged())
    depth, unloaded, cut, seconds = mgr.receiving_snapshot()
    assert (unloaded, seconds) == (0, 0.0)
    assert cut == staged_packs, 'the cut re-counts staged remainders, nothing else'
    assert len(tr._yard) == 1, 'one trailer still standing in the yard'
    # ledgers untouched: nothing was handed to the queue, so nothing flipped
    assert mgr._queued_qty == {} and len(mgr._stock_queue) == 0
    assert mgr._deferred_qty == {101: 12, 102: 12, 103: 12}


def test_the_remainder_carries_as_a_consumed_index_and_the_ledger_flips_per_unit():
    """A capped drain leaves the trailer holding its door, its plan intact and its
    remainder as the un-taken tail; the next drain finishes without re-packing, and
    position = on_hand + queued + deferred never wobbles."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split')
    mgr = _manager(tr, crew=1)
    epoch = 10_000.0
    _dispatch(mgr, 101, 12, POSITION_VOLUME, epoch)
    mgr._now_s = epoch
    mgr._release_arrivals()
    # plan once with an open day to LEARN the first duration, on a twin manager
    twin = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split'),
                    crew=1)
    _dispatch(twin, 101, 12, POSITION_VOLUME, epoch)
    _drain(twin, epoch)
    first_dur = twin.drain_receiving_records()[0][1]

    mgr._receive((), deadline=first_dur * 0.5)     # starts one unit, then the gate
    (trailer,) = tr.staged()
    pending_obj = trailer.pending
    staged_at = trailer.staged_s
    done_so_far = trailer.taken
    assert 0 < done_so_far < len(pending_obj), 'the cap must bite mid-trailer'
    assert trailer.staged, 'a trailer holds its door across drains until empty'
    flipped = sum(mgr._queued_qty.values())
    standing = sum(mgr._deferred_qty.values())
    assert flipped == sum(i.unit.quantity for i in pending_obj[:done_so_far]), (
        'the deferred->queued flip is PER UNIT at unload')
    assert flipped + standing == 12, 'position wobbled across the boundary'
    assert mgr.receiving_snapshot()[2] == len(pending_obj) - done_so_far
    mgr.drain_receiving_records()                  # the batch boundary: clocks restart

    # drain 2: the SAME pending list continues from its index — no re-pack — and the
    # trailer still holds the door it took in drain 1
    _drain(mgr, epoch + 10_000.0)
    assert trailer.pending is None and trailer.taken == 0, 'dropped when fully worked'
    assert trailer.staged_s == staged_at, 'one staging, held across the drains'
    assert sum(mgr._queued_qty.values()) == 12 and sum(mgr._deferred_qty.values()) == 0
    assert tr.depth == 0 and tr.merchandise() == 0
    assert trailer.emptied_s is not None and trailer.emptied_s > epoch + 10_000.0


def test_capped_levels_relabel_between_the_models_and_flows_conserve():
    """Layer 4, stated as the design states it: under a cap the two models legitimately
    diverge in WHERE the remainder stands — v1's floor remainder is QUEUED, the standing
    remainder is DEFERRED — while the conserved total never moves.  Deliberately not an
    equality test on the unloaded set: that divergence is the feature."""
    def _capped(transit):
        mgr = _manager(transit, crew=1)
        epoch = 10_000.0
        _dispatch(mgr, 101, 12, POSITION_VOLUME, epoch)
        _drain(mgr, epoch, deadline=1e-9)      # a day of nothing: one START each
        queued = sum(mgr._queued_qty.values())
        deferred = sum(mgr._deferred_qty.values())
        floor = sum(i.unit.quantity for i in mgr._dock.items)
        handed = sum(i.unit.quantity for i in mgr._stock_queue)
        return queued, deferred, floor, handed

    q_v1, d_v1, floor_v1, handed_v1 = _capped(TrailerTransit(Trailer28, lead_s=0.0))
    q_yd, d_yd, floor_yd, handed_yd = _capped(
        YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split'))
    assert q_v1 + d_v1 == q_yd + d_yd == 12, 'flows conserve under any cap'
    assert floor_v1 > 0 and q_v1 == 12, "v1's remainder stands QUEUED on the floor"
    assert floor_yd == 0 and d_yd > 0, 'the standing remainder stays DEFERRED aboard'
    assert handed_v1 == q_v1 - floor_v1
    assert handed_yd == q_yd


# ── 7. the census: yard + staged remainders, so conservation reads true ───────────

def test_the_census_extends_over_yard_and_staged_remainders():
    tr = YardTransit(Trailer28, lead_s=0.0, doors=1, allocation='merged')
    mgr = _manager(tr, crew=1)
    epoch = 10_000.0
    _dispatch(mgr, 101, 12, POSITION_VOLUME, epoch)
    _dispatch(mgr, 102, 12, POSITION_VOLUME, epoch)
    mgr._now_s = epoch
    mgr._release_arrivals()
    mgr._receive((), deadline=0.0)             # fill the one door, unload nothing
    assert tr.depth == 2, 'one staged + one standing — both are in-flight entries'
    assert tr.merchandise() == 24 == sum(mgr._deferred_qty.values()), (
        'the census IS the deferred ledger, seen from the transit')
    rows = sorted(tr.snapshot())
    assert rows == [(101, 12, 0), (102, 12, 0)], 'standing rows carry remaining 0'
