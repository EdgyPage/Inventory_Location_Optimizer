"""test_space_timeline.py — the standing dock's space view, and its neutrality.

`Inbound/space.py` extends the dormant `_emptied_at` substrate into the two-tier
`SpaceView` every standing drain freezes onto `ctx.space`: bins empty NOW (with actual
clear stamps) plus bins the standing demand is PREDICTED to clear — projected by the
sim's own drain rule (`Workload_Builder.drain_sku`), untimed by decision.  The design's
neutrality obligations, each pinned here:

  1. THE DEGENERATE LOCKSTEP RUNS WITH THE TIMELINE ON: standing-merged + timeline vs
     the v1 drain is stream-identical, and timeline-on vs timeline-off on the standing
     path itself is identical end to end (receive, placements, picks, reclaims) —
     maintenance neutrality, proven not assumed.
  2. PURITY: building a view mutates no manager state and consumes no RNG.
  3. THE DRAIN RULE IS THE SIM'S OWN: the extracted rule reproduces `Task.from_batch`'s
     bin_pick (and shortfall) on identical state.

Plus the mechanics the cache ticket will key on: per-event-class version counters
(equality-only), the clear-stamp lifecycle (harvest keeps only stamped bins, fill
expires), and the frozen-copy contract of the view itself — and the futuresight
window slot ("Build the futuresight window feed", 13): its own slot, riding the
`demand_v` event, never read by the projection.

The `_emptied_at` read-site pin (reclaim-harvest is the ONE legal reader) lives in
`Tests/unit/test_bin_empty_timing.py`, beside the stamp's other pins.

Run:  python -m pytest Tests/unit/test_space_timeline.py -q
"""
from __future__ import annotations

import random
from collections import defaultdict
from types import SimpleNamespace

import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.pack import packer
from Inbound.priorities import DockContext
from Inbound.space import SpaceTimeline, SpaceView
from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import TrailerTransit, YardTransit
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig)
from Warehouse.picking.Workload_Builder import Task, drain_sku


# ── fixtures (the test_standing_yard shapes, so the lockstep compares like to like) ──

def _order(sku: int, weight: float = 2.0) -> Order:
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


def _manager(transit, crew: int = 2, skus=(101, 102, 103)):
    mgr = Inventory_Manager(_warehouse())
    for sku in skus:
        mgr._originals[sku] = _order(sku)
    mgr.transit = transit
    mgr.packer = packer
    mgr.enable_receiving(Dock(DockSpec(size=crew, sources=('reorder', 'trailer'))))
    return mgr


def _stocked_manager(skus=((101, 12), (102, 5))):
    """A manager with merchandise already IN bins (sku 101 packs as two 6-qty pallets,
    102 as one 5-qty singleton — verified shapes the projection tests key on)."""
    mgr = Inventory_Manager(_warehouse())
    for sku, qty in skus:
        mgr._originals[sku] = _order(sku)
        mgr._release_to_stock(sku, qty)
    mgr._stock()
    return mgr


def _dispatch(mgr, sku: int, qty: int, vol: int, epoch: float) -> None:
    mgr.transit.dispatch(sku, qty, 0, unit_volume=vol, now_s=epoch)
    mgr._deferred_qty[sku] = mgr._deferred_qty.get(sku, 0) + qty


def _drain(mgr, epoch: float, deadline=None):
    mgr._now_s = epoch
    plans = mgr._release_arrivals()
    mgr._receive(plans, deadline)


def _queue_stream(mgr) -> list:
    return [(i.source, i.age, i.unit.order.sku, i.unit.quantity, i.unit.unit_category)
            for i in mgr._stock_queue]


def _ledgers(mgr) -> tuple:
    return (dict(mgr._deferred_qty), dict(mgr._queued_qty),
            dict(mgr._queued_sku_counts))


def _mgr_fingerprint(mgr) -> tuple:
    """Everything a freeze could plausibly perturb.  Bin lists compare by identity
    (Aisle.Bin defines no __eq__), which is exactly the strictness wanted here."""
    return (
        {k: list(v) for k, v in mgr._index.items()},
        dict(mgr._bin_index_pos),
        {s: list(bins) for s, bins in mgr._sku_singleton_bins.items()},
        {s: list(bins) for s, bins in mgr._sku_pallet_bins.items()},
        dict(mgr._current_quantities),
        dict(mgr._queued_qty),
        dict(mgr._deferred_qty),
        list(mgr._pending_reclaim),
        dict(mgr._emptied_at),
        len(mgr._stock_queue),
    )


# ── 1a. the degenerate lockstep, timeline ON, against the v1 drain ────────────────

def _run_receive_scenario(mgr, tl=None) -> list:
    """The test_standing_yard scripted scenario, with a per-drain demand injection on
    the timeline side (the fifo keys never read the view, so it must change nothing)."""
    per_drain = []
    epochs = (10_000.0, 20_000.0, 30_000.0)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epochs[0])
    _dispatch(mgr, 102, 4, POSITION_VOLUME, epochs[0])
    for b, epoch in enumerate(epochs):
        if b == 2:
            _dispatch(mgr, 103, 5, POSITION_VOLUME // 2, epoch)
        if tl is not None:
            tl.inject_demand({101: 3, 102: 1}, released_at=epoch)
        _drain(mgr, epoch)
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


def test_the_degenerate_lockstep_holds_with_the_timeline_on():
    """Obligation 1, first half: standing-merged WITH the always-on timeline is still
    stream-identical to the v1 drain — the timeline is pure data under fifo/fifo."""
    v1 = _manager(TrailerTransit(Trailer28, lead_s=0.0), crew=2)
    got_v1 = _run_receive_scenario(v1)
    yd = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4, allocation='merged'),
                  crew=2)
    tl = SpaceTimeline(drain_sku).attach(yd)
    got_yd = _run_receive_scenario(yd, tl=tl)
    for b, (a, s) in enumerate(zip(got_v1, got_yd)):
        assert s['records'] == a['records'], f'drain {b}: unload records diverged'
        assert s['snapshot'] == a['snapshot'], f'drain {b}: (depth, unloaded, cut, s)'
        assert s['transit'] == a['transit'], f'drain {b}: transit census diverged'
        assert s['carryover'] == a['carryover'], f'drain {b}: carryover diverged'
        assert s['queue'] == a['queue'], f'drain {b}: the put-queue STREAM diverged'
        assert s['ledgers'] == a['ledgers'], f'drain {b}: a ledger diverged'
        assert s['recv_seconds'] == pytest.approx(a['recv_seconds'])
    # non-vacuity: real work happened AND the timeline was really on the whole time
    assert sum(len(d['records']) for d in got_v1) > 0
    assert tl.views_built == 3, 'a drain froze no view — the hook is dead'
    assert tl.demand_v == 3


# ── 1b. timeline-on vs timeline-off on the standing path, end to end ──────────────

def _run_standing_cycle(mgr, tl=None) -> list:
    """Receive + put-away placements + picks + reclaims, so every timeline touchpoint
    (inject, freeze, fill, harvest) fires — in check_reorders' phase order."""
    out = []
    epochs = (10_000.0, 20_000.0, 30_000.0)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epochs[0])
    _dispatch(mgr, 102, 4, POSITION_VOLUME, epochs[0])
    for b, epoch in enumerate(epochs):
        if b == 2:
            _dispatch(mgr, 103, 5, POSITION_VOLUME // 2, epoch)
        if tl is not None:
            tl.inject_demand({101: 6}, released_at=epoch)   # 6 = one whole pallet
        mgr.reclaim_emptied_bins()                          # step 0: harvest fires here
        _drain(mgr, epoch)                                  # step 4: freeze fires here
        mgr._drain_putaway()                                # step 5: fill fires here
        # one deterministic pick-dry per round, exactly alike on both managers
        bins = (list(mgr._sku_pallet_bins.get(101, ()))
                or list(mgr._sku_singleton_bins.get(101, ())))
        if bins:
            b0 = bins[0]
            qty = b0.storage.quantity
            b0.storage = None
            mgr._notify_pick(101, qty)
            mgr._notify_bin_emptied(b0, at=epoch + 500.0)
        out.append({
            'records': mgr.drain_receiving_records(),
            'placements': sorted((bn.location, bn.storage.order.sku, bn.storage.quantity)
                                 for bn in mgr._unavailable.values()
                                 if bn.storage is not None),
            'queue': _queue_stream(mgr),
            'ledgers': _ledgers(mgr),
            'index': {k: [bn.location for bn in v] for k, v in mgr._index.items()},
            'current': dict(mgr._current_quantities),
        })
    mgr.reclaim_emptied_bins()
    out.append({'final_index': {k: [bn.location for bn in v]
                                for k, v in mgr._index.items()}})
    return out


def test_the_timeline_changes_nothing_on_the_standing_path_end_to_end():
    """Obligation 1, second half: attach the timeline to one of two identical standing
    managers and run receive, placements, picks and reclaims — every fingerprint
    matches, so all four touchpoints are behavior-neutral, not just the freeze."""
    plain = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4, allocation='merged'),
                     crew=2)
    got_plain = _run_standing_cycle(plain)
    timed = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4, allocation='merged'),
                     crew=2)
    tl = SpaceTimeline(drain_sku).attach(timed)
    got_timed = _run_standing_cycle(timed, tl=tl)
    assert got_timed == got_plain
    # non-vacuity: every event class actually fired on the timeline side
    assert tl.views_built == 3
    assert tl.demand_v == 3
    assert tl.fill_v > 0, 'no placement reached the fill hook'
    assert tl.reclaim_v > 0, 'no reclaim reached the harvest hook'
    assert tl.emptied_at, 'no stamp survived — the harvest kept nothing'


# ── 2. purity: a freeze mutates no manager state and consumes no RNG ──────────────

def test_freeze_is_pure():
    mgr = _stocked_manager()
    tl = SpaceTimeline(drain_sku).attach(mgr)
    tl.inject_demand({101: 8, 102: 5}, released_at=42.0)
    before_mgr = _mgr_fingerprint(mgr)
    before_rng = random.getstate()
    view = tl.freeze(mgr, 42.0)
    assert _mgr_fingerprint(mgr) == before_mgr, 'building a view mutated manager state'
    assert random.getstate() == before_rng, 'building a view consumed RNG'
    assert view.predicted, 'the pin is vacuous — the projection predicted nothing'


# ── 3. the drain rule is the sim's own, by construction AND by pin ────────────────

def test_the_extracted_drain_rule_reproduces_from_batch():
    """`drain_sku` ≡ `Task.from_batch`'s bin_pick (and shortfall) on identical state —
    the guarantee that the projection can never drift from what picking will do."""
    mgr = _stocked_manager(skus=((101, 12), (102, 5)))
    items = {101: 8, 102: 99}                     # partial drain + a real shortfall
    tasks, short = Task.from_batch_with_shortfall(
        SimpleNamespace(items=dict(items)), mgr.warehouse, manager=mgr)
    from_batch_takes = {id(b): q for t in tasks
                        for b, q in zip(t.path, t.planned) if q > 0}
    rule_takes: defaultdict = defaultdict(int)
    rule_short = {}
    for sku, qty in items.items():
        rem = drain_sku(mgr._sku_singleton_bins.get(sku, ()),
                        mgr._sku_pallet_bins.get(sku, ()), qty, rule_takes)
        if rem > 0:
            rule_short[sku] = rem
    assert {id(b): q for b, q in rule_takes.items()} == from_batch_takes
    assert rule_short == short
    assert from_batch_takes and short, 'one side of the pin never fired'


# ── 4. predicted clears: exact, untimed, keyed like the empties ───────────────────

def test_predicted_clears_are_exact_and_untimed():
    mgr = _stocked_manager()
    tl = SpaceTimeline(drain_sku).attach(mgr)
    # 101 holds two 6-qty pallets; demand 3 drains the first PARTIALLY -> not predicted.
    # 102 holds one 5-qty singleton; demand 5 clears it exactly -> predicted.
    tl.inject_demand({101: 3, 102: 5}, released_at=77.0)
    view = tl.freeze(mgr, 77.0)
    skey = ('conveyable', 'food', 'singleton', 'singleton')
    assert list(view.predicted) == [skey], f'predicted keys: {list(view.predicted)}'
    (b102,) = view.predicted[skey]
    assert b102.storage.order.sku == 102
    # demand that clears BOTH of 101's pallets predicts both, under the pallet key
    tl.inject_demand({101: 15}, released_at=88.0)
    view2 = tl.freeze(mgr, 88.0)
    pkeys = [k for k in view2.predicted if k[3] == 'pallet']
    assert len(pkeys) == 1 and len(view2.predicted[pkeys[0]]) == 2
    # untimed by decision: the only times anywhere are FACTS — the demand's release
    # instant and (here absent: nothing was ever picked) actual clear stamps
    assert view2.released_at == 88.0
    assert view2.emptied_at == {}
    assert view2.frozen_at == 88.0


# ── 5. versions: per-event-class counters, and the demand is one batch deep ───────

def test_versions_bump_per_event_class_and_demand_replaces():
    tl = SpaceTimeline(drain_sku)
    b1, b2, b3 = object(), object(), object()
    tl.inject_demand({7: 3}, released_at=1.0)
    tl.inject_demand({8: 4}, released_at=2.0)
    assert tl.demand_v == 2
    assert tl.demand == {8: 4}, 'standing demand is one batch deep — it REPLACES'
    assert tl.released_at == 2.0
    tl.harvest([b1, b2, b3], {id(b1): 5.5})
    assert tl.reclaim_v == 3, 'reclaim_v is +1 per harvested BIN, not per harvest'
    assert tl.emptied_at == {id(b1): 5.5}, 'only actual stamps enter the map'
    tl.fill(b1)
    tl.fill(b2)                       # no stamp to expire — still a fill event
    assert tl.fill_v == 2
    assert tl.emptied_at == {}, 'a filled bin is no longer empty; its stamp expires'


def test_the_window_rides_the_injection_and_replaces():
    """The futuresight feed ("Build the futuresight window feed", 13): the window is
    its OWN slot, set by the same injection that bumps `demand_v` — one event, no
    fourth counter — replaced wholesale each batch, and invisible to the projection
    (a merged window would silently redefine "Predicted clear")."""
    mgr = _stocked_manager()
    tl = SpaceTimeline(drain_sku).attach(mgr)
    tl.inject_demand({102: 5}, released_at=1.0, window=({101: 6}, {102: 2}))
    assert tl.demand_v == 1, 'the window rides the demand event — no extra bump'
    view = tl.freeze(mgr, 1.0)
    assert view.window == ({101: 6}, {102: 2})
    skey = ('conveyable', 'food', 'singleton', 'singleton')
    assert list(view.predicted) == [skey], (
        'the projection reads the standing demand ONLY: 101 appears solely in the '
        'window, and predicting its pallets would break the one-batch-deep pin')
    tl.inject_demand({101: 3}, released_at=2.0)
    assert tl.freeze(mgr, 2.0).window is None, (
        'a lawful injection (no window) leaves the slot EMPTY — replaced wholesale, '
        'never carried over from the previous batch')
    tl.inject_demand({101: 3}, released_at=3.0, window=())
    assert tl.freeze(mgr, 3.0).window == (), (
        'the end of the script is an EMPTY window, not None and not an error')


# ── 6. the view is a frozen copy ──────────────────────────────────────────────────

def test_the_view_is_a_frozen_copy_of_live_state():
    mgr = _stocked_manager()
    tl = SpaceTimeline(drain_sku).attach(mgr)
    tl.inject_demand({101: 6}, released_at=5.0)
    view = tl.freeze(mgr, 5.0)
    empties_then = {k: len(v) for k, v in view.empties.items()}
    versions_then = view.versions
    # mutate live state: place more stock (removes free bins, bumps fill_v)
    mgr._originals[103] = _order(103)
    mgr._release_to_stock(103, 5)
    mgr._stock()
    assert {k: len(v) for k, v in view.empties.items()} == empties_then, (
        'the frozen empties tracked the live index')
    assert view.versions == versions_then
    later = tl.freeze(mgr, 6.0)
    assert later.versions != versions_then, 'the fill never bumped a version'
    assert sum(len(v) for v in later.empties.values()) < sum(empties_then.values())


# ── 7. the view arrives at ctx-freeze, and nowhere else ───────────────────────────

def _spy_transit(seen: dict, **kw):
    """A YardTransit whose freeze_ctx records the drain's ctx (the class is slotted,
    so an instance rebind is impossible — a subclass is the honest spy)."""
    class _Spy(YardTransit):
        def freeze_ctx(self):
            ctx = super().freeze_ctx()
            seen.setdefault('ctx', ctx)
            return ctx
    return _Spy(Trailer28, **kw)


def test_ctx_space_arrives_at_ctx_freeze():
    seen: dict = {}
    tr = _spy_transit(seen, lead_s=0.0, doors=4, allocation='merged')
    mgr = _manager(tr, crew=2)
    tl = SpaceTimeline(drain_sku).attach(mgr)
    epoch = 10_000.0
    tl.inject_demand({101: 2}, released_at=epoch)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epoch)
    _drain(mgr, epoch)
    ctx = seen['ctx']
    assert isinstance(ctx.space, SpaceView), 'ctx-freeze did not freeze a view'
    assert ctx.space.frozen_at == epoch
    assert ctx.space.versions == (tl.demand_v, tl.reclaim_v, tl.fill_v)
    assert ctx.space.released_at == epoch


def test_without_a_timeline_ctx_space_stays_none():
    """The default everywhere: bare DockContext, the v1 path, and a standing manager
    nothing attached to — `space` is None and fifo keys never look."""
    assert DockContext(doors=4, free_doors=4, yard_depth=0).space is None
    seen: dict = {}
    tr = _spy_transit(seen, lead_s=0.0, doors=4, allocation='merged')
    mgr = _manager(tr, crew=2)
    epoch = 10_000.0
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epoch)
    _drain(mgr, epoch)
    assert seen['ctx'].space is None
