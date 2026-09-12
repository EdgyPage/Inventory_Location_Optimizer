"""test_site_receiving.py — the receiving coordinator, and the two ports it reaches through.

`SiteReceiving` (`Inbound/receiving.py`) took the standing drain out of the inventory
manager so that ONE dock can serve two channels' leaves.  With one leaf it must be the
drain the manager used to run, phase for phase — that equivalence is what makes the
extraction landable before any second leaf exists, and it is what this file pins.

Three claims, and each fails for a different reason:

  1. THE PHASE COMPOSITION.  `SiteReceiving.drain([leaf], ...)` and `leaf.check_reorders(...)`
     drive the same seven phases in the same order, so a seeded scenario produces the
     identical dock records, yard rows, queue stream and ledgers — compared per batch as
     SEQUENCES, never as sums (memory `lockstep-tests-compare-aggregates-only`: all three
     lockstep tests could pass on differently-shaped event streams when they compared
     aggregates).
  2. THE PORTS IN ISOLATION.  `plan_lot` and `accept` are public and separately callable,
     which is the testability the extraction bought: the two merchandise-bearing steps of
     a drain can now be exercised without standing up a dock at all.
  3. THE LOUD REFUSAL.  A standing transit with no coordinator bound raises rather than
     silently receiving nothing — `pool-run-swallows-dead-arms` is the failure mode, a run
     that looks healthy and answers a different question.

Run:  python -m pytest Tests/unit/test_site_receiving.py -q
"""
from __future__ import annotations

import pytest

from Inbound.receiving import SiteReceiving
from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import YardTransit

from Tests.unit.test_standing_yard import (
    _dispatch, _ledgers, _manager, _queue_stream)


def _yard(**kw):
    return YardTransit(Trailer28, lead_s=0.0, doors=4, **kw)


def _snapshot(mgr) -> dict:
    """Everything a drain can move, as ordered sequences."""
    return {
        'records':  mgr.drain_receiving_records(),
        'yard':     mgr.drain_yard_drains(),
        'queue':    _queue_stream(mgr),
        'ledgers':  _ledgers(mgr),
        'transit':  sorted(mgr.transit.snapshot()),
        'recv_s':   mgr.receiving_seconds,
        # The put drain runs inside BOTH compositions, so the queue is empty by the time
        # this is taken and its equality is weak on its own.  On-hand is what the queue
        # turned into, and it is where a placement divergence would actually show.
        'on_hand':  dict(mgr._current_quantities),
    }


def _scenario(mgr, *, via_coordinator: bool) -> list:
    """The standing-yard scenario, driven either way, snapshotted per batch.

    Deliberately the same shape as `test_standing_yard._run_scenario`: a reorder that
    splits across trailers plus a second SKU sharing the open trailer, a quiet batch, and
    a third SKU at another volume.
    """
    out = []
    epochs = (10_000.0, 20_000.0, 30_000.0)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, epochs[0])
    _dispatch(mgr, 102, 4, POSITION_VOLUME, epochs[0])
    for b, epoch in enumerate(epochs):
        if b == 2:
            _dispatch(mgr, 103, 5, POSITION_VOLUME // 2, epoch)
        if via_coordinator:
            mgr.receiving.drain([mgr], now_s=epoch)
        else:
            mgr.check_reorders(now_s=epoch)
        out.append(_snapshot(mgr))
    return out


# ── 1. one leaf through the coordinator IS check_reorders ─────────────────────────

@pytest.mark.parametrize('allocation', ['merged', 'split'])
def test_one_leaf_through_the_coordinator_matches_check_reorders(allocation):
    """The whole point of landing this before a second leaf exists.

    Both paths end in the same `SiteReceiving.receive`, so what is being pinned here is
    the PHASE ORDER: `drain` composes tick/reclaim/advance/fire/release per leaf, then one
    shared receive, then the put drain — and `check_reorders` composes exactly that for
    one channel.  Reordering either changes results (firing before the lead tick would
    decrement an order in the batch it was placed), so this is the test that fails when
    the site interleave drifts from the single-channel one.
    """
    direct = _manager(_yard(allocation=allocation), crew=2)
    viacrd = _manager(_yard(allocation=allocation), crew=2)

    got_direct = _scenario(direct, via_coordinator=False)
    got_viacrd = _scenario(viacrd, via_coordinator=True)

    assert len(got_direct) == len(got_viacrd) == 3
    for b, (a, c) in enumerate(zip(got_direct, got_viacrd)):
        for field in ('records', 'yard', 'queue', 'ledgers', 'transit',
                      'recv_s', 'on_hand'):
            assert a[field] == c[field], (
                f'batch {b}: {field} diverged between check_reorders and the coordinator')

    # Non-vacuity: a scenario that received nothing would satisfy every equality above.
    assert any(s['records'] for s in got_direct), 'the scenario unloaded nothing'
    assert got_direct[-1]['recv_s'] > 0.0, 'no receiving labour was ever charged'
    assert any(s['on_hand'] for s in got_direct), 'nothing was ever placed into a bin'
    assert sum(len(s['yard']) for s in got_direct) == 3, 'a drain recorded no yard row'


def test_the_coordinator_returns_the_yard_row_rather_than_recording_it():
    """`receive` hands the row back so the CALLER decides where a row belongs.

    With two leaves the row is site-scoped and belongs in neither leaf's table ("Design
    the site scope in the run tree"), so the coordinator must not have written it
    anywhere by the time it returns.
    """
    mgr = _manager(_yard(), crew=2)
    _dispatch(mgr, 101, 15, POSITION_VOLUME, 10_000.0)
    mgr._now_s = 10_000.0
    mgr._release_arrivals()

    row = mgr.receiving.receive((mgr,), None)

    assert isinstance(row, tuple) and len(row) == 4, row
    assert mgr.drain_yard_drains() == [], (
        'receive() recorded the row itself — the caller can no longer place it')


# ── 2. the ports, exercised without a dock ────────────────────────────────────────

def test_plan_lot_returns_both_halves_and_neither_derives_the_other():
    """`plan_lot` is the reason the port is not `-> list[PutawayItem]`.

    The plans become `trailer.plans` and the dock's arrival notice; the items become
    `trailer.pending`.  A plan holds several units, so the grouping is not recoverable
    from the items — which is what a single-return signature would have thrown away.
    """
    mgr = _manager(_yard(), crew=2)

    plans, items = mgr.plan_lot(101, 15, 'trailer')

    assert plans, 'no plans'
    assert items, 'no items'
    assert sum(len(p.units) for p in plans) == len(items), (
        'the plans and the items describe different unit sets')
    assert all(i.source == 'trailer' for i in items), 'the source did not reach the stamp'
    # Non-vacuity for "neither derives the other": at least one plan carries several units,
    # so flattening really does lose the grouping.
    assert max(len(p.units) for p in plans) > 1, (
        'every plan held one unit — the test cannot see the grouping it is about')


def test_plan_lot_debits_only_the_packer_shortfall():
    """The remainder ledger stays the PLANNED quantities the census also counts."""
    mgr = _manager(_yard(), crew=2)
    mgr._deferred_qty[101] = 15

    plans, items = mgr.plan_lot(101, 15, 'trailer')
    packed = sum(u.quantity for p in plans for u in p.units)

    assert mgr._deferred_qty[101] == 15 - max(0, 15 - packed), (
        'the arrival debit is not the packer shortfall')


def test_accept_flips_the_ledger_and_charges_the_leaf_not_the_dock():
    """`position = on_hand + queued + deferred` never wobbles, and `dur` is the only
    dock-derived thing a leaf is told: `t0` and `w` are the DOCK's row."""
    mgr = _manager(_yard(), crew=2)
    _plans, items = mgr.plan_lot(101, 15, 'trailer')
    item = items[0]
    qty = item.unit.quantity
    mgr._deferred_qty[101] = qty
    before_queue = len(mgr._stock_queue)

    mgr.accept(item, 12.5)

    assert mgr._deferred_qty[101] == 0, 'the deferred leg did not clear'
    assert mgr._queued_sku_counts[101] == 1
    assert mgr._queued_qty[101] == qty
    assert mgr.receiving_seconds == 12.5, 'the leaf was not charged the unload'
    assert len(mgr._stock_queue) == before_queue + 1, 'the item never reached the queue'


# ── 3. the refusal ────────────────────────────────────────────────────────────────

def test_a_standing_transit_without_a_coordinator_refuses_loudly():
    """Silently receiving nothing is a run that looks healthy and answers a different
    question (memory `pool-run-swallows-dead-arms`)."""
    mgr = _manager(_yard(), crew=2)
    mgr.receiving = None
    _dispatch(mgr, 101, 15, POSITION_VOLUME, 10_000.0)
    mgr._now_s = 10_000.0
    plans = mgr._release_arrivals()

    with pytest.raises(RuntimeError, match='receiving coordinator'):
        mgr._receive(plans, None)


# ── 4. the phase ORDER, pinned directly ───────────────────────────────────────────
#
# The behavioural equivalence above is real but it is NOT sensitive to phase order on a
# zero-lead scenario: reordering `_advance_lead_queue` and `_fire_reorders` inside `drain`
# leaves every record, ledger and on-hand figure identical, because nothing is ever in
# flight for a tick to advance.  That was established by MUTATION, not assumed — and it is
# the `real-test-coverage-is-317` failure exactly: an assertion that cannot fail.
#
# So the order is pinned for what it is: a claim about the SEQUENCE of calls, recorded.
# This fails on any reordering, insertion or omission, on any scenario.

#: The seven phases `check_reorders` composes, in its declared order.
_PHASES = ('_tick_batch', 'reclaim_emptied_bins', '_advance_lead_queue',
           '_fire_reorders', '_release_arrivals', '_receive', 'drain_putaway')

#: What the SITE composition drives on a leaf instead: the same order, with the leaf's own
#: `_receive` replaced in place by the coordinator's ONE shared drain.  That substitution
#: is the whole design — every other phase stays the leaf's, and the shared receive sits in
#: exactly the slot the per-leaf receive occupied.
_SITE_PHASES = tuple('receive@site' if p == '_receive' else p for p in _PHASES)


def _record(mgr, coordinator=None) -> list:
    """Wrap each phase on ONE instance to append its name, then delegate."""
    seen: list = []

    def wrap(obj, name, label):
        bound = getattr(obj, name)

        def recorder(*a, **kw):
            seen.append(label)
            return bound(*a, **kw)
        setattr(obj, name, recorder)

    for name in _PHASES:
        wrap(mgr, name, name)
    if coordinator is not None:
        wrap(coordinator, 'receive', 'receive@site')
    return seen


def test_the_site_composition_substitutes_only_the_receive_phase():
    """`drain([leaf])` is `check_reorders` with ONE shared receive in the receive slot.

    THE ORDER IS THE BEHAVIOUR (`check_reorders`' own docstring): firing before the lead
    tick would decrement an order in the batch it was placed, and releasing before firing
    would delay every lead-0 arrival by a batch.  The site composition claims the same
    order with a single substitution, so it cannot drift — in either direction — without
    this failing.

    `_release_arrivals` in particular must still run per leaf BEFORE the shared receive,
    even though it is a structural no-op in standing mode: it is the phase that lands
    trailers in the yard, so dropping it would strand every arrival.
    """
    direct = _manager(_yard(), crew=2)
    viacrd = _manager(_yard(), crew=2)
    seen_direct = _record(direct)
    seen_viacrd = _record(viacrd, viacrd.receiving)

    _dispatch(direct, 101, 15, POSITION_VOLUME, 10_000.0)
    _dispatch(viacrd, 101, 15, POSITION_VOLUME, 10_000.0)
    direct.check_reorders(now_s=10_000.0)
    viacrd.receiving.drain([viacrd], now_s=10_000.0)

    assert seen_direct == list(_PHASES), (
        f'check_reorders no longer drives the declared phase list: {seen_direct}')
    assert seen_viacrd == list(_SITE_PHASES), (
        f'the site composition drifted: {seen_viacrd} vs {list(_SITE_PHASES)}')
    # The substitution is exactly one phase wide — nothing else moved, was added or lost.
    assert (len(seen_viacrd) == len(seen_direct)
            and sum(a != b for a, b in zip(seen_direct, seen_viacrd)) == 1), (
        'the site composition differs from check_reorders by more than the receive phase')


# ══════════════════════════════════════════════════════════════════════════════════
# THE COUPLED HALF: one dock, one yard, TWO leaves
# ══════════════════════════════════════════════════════════════════════════════════
#
# Everything above is the one-leaf coordinator, and it stays true.  What follows is the
# thing that coordinator existed to make possible: a mixed trailer arriving at a site
# whose two channels are two separate managers, two separate warehouses and two separate
# ledgers.  Four claims, and each fails for its own reason:
#
#   1. THE TWO ROUTES.  A bare lot resolves its packer from the `{sku: leaf}` owner dict;
#      an unloaded unit resolves its taker from `regime_of`.  They are cross-checked,
#      because a disagreement is a ledger balancing in the wrong warehouse.
#   2. THE REFUSALS, and every one of them guards a SILENT wrong answer: an overlapping
#      partition, a second yard behind one dock, a partial site drain, two epochs over one
#      set of doors, a leaf reading the site's dock as its own.
#   3. THE SPACE VIEW is contributed TAGGED once there are two, which is what partitions
#      `empties` so a mixed trailer's store units rank against store bins.
#   4. THE SITE RECEIVING CLOCK: one base, asked for twice, committed once.

from Warehouse.kernel.timeline import WorkDay

from Tests.unit.test_standing_yard import _order, _warehouse

from Inbound.dock import Dock, DockSpec
from Inbound.pack import packer as _packer
from Warehouse.catalog.Order import StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.kernel.regime import regime_of


#: The two channels, in the declared order the driver binds them in.  Store first, which
#: is also the order a mixed trailer's lots load in.
_STORE, _FUL = 'store', 'fulfillment'

#: Disjoint sku blocks, one per channel -- the catalogue partition a coupled unit asserts
#: at the driver and this coordinator asserts again at bind.
_STORE_SKUS = (101, 102)
_FUL_SKUS = (201, 202)


def _ful_order(sku: int):
    """A fulfillment-regime order: same shape as `_order`, fulfillment storage type.

    That one field is the whole difference `regime_of` reads -- the packer then produces
    `FulfillmentBin` units whose `unit_category` says so, which is how step 4 routes
    without anything being stamped at the unit level.
    """
    c = _order(sku)
    c.storage_type = (_FUL, _FUL)
    c.storage_handle_config = StorageHandleConfig(_FUL, _FUL)
    c.lift_group = (_FUL, _FUL)
    return c


def _leaf(transit, skus, ful: bool):
    """One channel's manager, sharing `transit` with its sibling.

    No dock and no coordinator of its own: under coupling BOTH are the site's, and this is
    the shape the driver will build -- the coordinator is constructed once, above, and
    bound onto each leaf.
    """
    mgr = Inventory_Manager(_warehouse())
    for sku in skus:
        mgr._originals[sku] = _ful_order(sku) if ful else _order(sku)
    mgr.transit = transit
    mgr.packer = _packer
    return mgr


def _site(crew: int = 2, day=None, **yard_kw):
    """`(coordinator, store_leaf, ful_leaf)` — one dock, one yard, two bound leaves."""
    transit = _yard(**yard_kw)
    dock = Dock(DockSpec(size=crew, sources=('reorder', 'trailer')))
    crd = SiteReceiving(dock, transit, day=day)
    store = _leaf(transit, _STORE_SKUS, ful=False)
    ful = _leaf(transit, _FUL_SKUS, ful=True)
    for leaf, ch in ((store, _STORE), (ful, _FUL)):
        leaf.enable_receiving(dock)
        leaf.receiving = crd
        crd.bind(leaf, ch)
    return crd, store, ful


def _mixed_trailer(crd, store, ful, epoch: float = 10_000.0):
    """Fire one reorder from each channel into the ONE yard and land the trailer.

    Both leaves dispatch into the same transit, so the open trailer carries store lots and
    fulfillment lots — the charter's mixed LOAD, which is the thing the owner routing
    exists for.  `_release_arrivals` is driven per leaf (the site composition's phase 3)
    and is a structural no-op in standing mode: it lands the trailer in the yard.
    """
    for leaf, skus in ((store, _STORE_SKUS), (ful, _FUL_SKUS)):
        for sku in skus:
            _dispatch(leaf, sku, 6, POSITION_VOLUME // 2, epoch)
    for leaf in (store, ful):
        leaf._now_s = epoch
        leaf._release_arrivals()


# ── 5. the two routes, and the cross-check ────────────────────────────────────────

def test_a_mixed_trailer_is_packed_lot_by_lot_by_its_owner():
    """STEP 1's route. A lot is a bare `(sku, qty)` — no unit exists yet — so the owner
    comes from the dict built at bind time, and `_originals`, the packer, `inbound_split`
    and `_putaway_seq` are all the owning leaf's.

    Asserted on the STAMPS, not on a call count: each leaf's `_putaway_seq` advances by
    exactly the units it packed, so a lot packed by the wrong leaf shows up as a sequence
    that ran on the wrong side."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    crd.receive([store, ful], None)

    assert store._putaway_seq > 0 and ful._putaway_seq > 0, (
        'one leaf packed nothing — the trailer was not mixed, or one route took both')
    # Every queued unit is its own leaf's regime, in BOTH leaves. The store leaf holding a
    # fulfillment unit is merchandise delivered to the wrong warehouse.
    for leaf, regime in ((store, _STORE), (ful, _FUL)):
        got = {regime_of(it.unit) for it in leaf._stock_queue}
        assert got == {regime}, f'the {regime} leaf queued {got}'


def test_the_handoff_routes_by_regime_and_the_two_leaves_sum_to_the_dock():
    """STEP 4's route, and the property that makes it checkable: `accept` credits the
    OWNING leaf's `_recv_seconds`, so the two leaves sum EXACTLY to the site total the
    dock accrued. Reporting one leaf's figure as the site's is
    `a-right-site-total-hides-two-wrong-shares`; this is the arithmetic that keeps both
    readings available and honest."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    crd.receive([store, ful], None)

    assert store.receiving_seconds > 0.0 and ful.receiving_seconds > 0.0, (
        'one leaf was charged no receiving labour at all')
    assert (store.receiving_seconds + ful.receiving_seconds
            == pytest.approx(crd.dock.seconds)), (
        'the leaves do not sum to the site dock — a unit was charged to nobody or twice')


def test_a_unit_whose_regime_disagrees_with_the_owner_dict_is_refused():
    """The CROSS-CHECK, which is the only thing that can see the catalogue partition and
    the regime tagging disagree. Planted the way it would really happen: a store-owned sku
    whose order is fulfillment-shaped, which is what a mis-filtered channel produces."""
    crd, store, ful = _site()
    # The store leaf owns 101, but its template is re-shaped to the other regime — so the
    # dict says store and the unloaded unit says fulfillment.
    store._originals[101] = _ful_order(101)
    _mixed_trailer(crd, store, ful)

    with pytest.raises(ValueError, match='wrong warehouse'):
        crd.receive([store, ful], None)


def test_an_unowned_sku_on_a_site_trailer_is_refused():
    """The yard is the site's, so a lot nobody owns is merchandise this site never
    ordered — and the alternative to raising is packing it onto whichever leaf bound
    first."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    crd._owner.pop(101)

    with pytest.raises(ValueError, match='no bound leaf owns it'):
        crd.receive([store, ful], None)


# ── 6. the refusals that guard a silent wrong answer ──────────────────────────────

def _half_site():
    """A coordinator with only the STORE leaf bound — the shape the bind refusals need,
    because a fully bound one refuses every second bind on the duplicate-channel rule
    before it can reach the rule under test."""
    transit = _yard()
    dock = Dock(DockSpec(size=2, sources=('reorder', 'trailer')))
    crd = SiteReceiving(dock, transit)
    store = _leaf(transit, _STORE_SKUS, ful=False)
    store.enable_receiving(dock)
    store.receiving = crd
    crd.bind(store, _STORE)
    return crd, store


def test_binding_two_leaves_that_own_one_sku_is_refused():
    """An overlap means the channel filter let one order into both leaves. Nothing
    downstream would notice: the lot would be packed by whichever bound first, delivered
    to that warehouse, and its ledger would balance there."""
    crd, _store = _half_site()
    overlapping = _leaf(crd.transit, (_STORE_SKUS[0],), ful=True)
    with pytest.raises(ValueError, match='owned by'):
        crd.bind(overlapping, _FUL)


def test_binding_a_leaf_with_a_different_transit_is_refused():
    """One site is one yard. Two yards behind one dock gives each leaf its own trailers
    while the drain ranks only the coordinator's — and it is also what makes the
    site-scoped `_advance_lead_queue` honest, because the phase is driven on one leaf and
    must reach the same object either way."""
    crd, _store = _half_site()
    stray = _leaf(_yard(), _FUL_SKUS, ful=True)
    with pytest.raises(ValueError, match='different transit'):
        crd.bind(stray, _FUL)


def test_binding_a_channel_that_is_not_a_regime_is_refused():
    """`_leaf_for` routes the handoff by `regime_of(unit)`, so the channel a leaf binds
    under IS its regime. Unchecked, a misspelling binds happily and fails on the first
    unloaded unit — after the trailer was planned, the doors filled and the crew charged."""
    crd, _store = _half_site()
    other = _leaf(crd.transit, _FUL_SKUS, ful=True)
    with pytest.raises(ValueError, match='not a storage regime'):
        crd.bind(other, 'fulfilment')          # one 'l': the spelling that would bind


def test_binding_one_channel_twice_is_refused():
    crd, _store, _ful = _site()
    again = _leaf(crd.transit, (301,), ful=False)
    with pytest.raises(ValueError, match='already bound'):
        crd.bind(again, _STORE)


def test_a_partial_site_drain_is_refused():
    """The only way to reach this is a coupled leaf's own `check_reorders`, and what it
    would do is unload the site's day for one channel while the other's arrivals stand on
    the yard: half a site day's receiving, attributed whole."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    with pytest.raises(RuntimeError, match='one dock is one drain'):
        crd.receive([store], None)
    # And the same refusal through the phase the manager itself would drive.
    with pytest.raises(RuntimeError, match='one dock is one drain'):
        store._receive((), None)


def test_two_epochs_over_one_set_of_doors_are_refused():
    """The two leaves' PICK crews genuinely release at different instants inside one site
    day, so the driver has to hand the site epoch down. Taking leaf[0]'s silently would
    rank one yard against two different 'now's and nothing would say which one ran."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    ful._now_s = store._now_s + 1.0
    with pytest.raises(ValueError, match='different epochs'):
        crd.receive([store, ful], None)


def test_a_non_standing_transit_is_refused_at_construction():
    """A coupled run REQUIRES the standing yard. The v1 and flag-off transits drain through
    the manager's own dock deque, a separate path with its own owner problem — and the
    alternative to refusing is a silent fallback to per-leaf receiving with a coupled label
    on it (memory `pool-run-swallows-dead-arms`)."""
    from Inbound.transit import TrailerTransit
    from Inbound.trailer import Trailer28
    dock = Dock(DockSpec(size=1, sources=('reorder',)))
    with pytest.raises(ValueError, match='STANDING yard'):
        SiteReceiving(dock, TrailerTransit(Trailer28, lead_s=0.0))


# ── 7. the yard row is the SITE's ─────────────────────────────────────────────────

def test_a_coupled_drain_parks_its_yard_row_and_hands_no_leaf_a_number():
    """The row is trailer- and door-denominated, so it belongs to neither channel. Handing
    it to leaf[0] would publish a site total under one channel's name, which is exactly
    the failure `a-right-site-total-hides-two-wrong-shares` records."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)

    row = crd.receive([store, ful], None)

    assert row is None, 'a coupled drain handed its caller a site-scoped row'
    # STRONGER THAN "the leaf recorded nothing": the accessor itself refuses once the
    # scope is the site's, so a driver that kept draining per leaf finds out loudly rather
    # than writing an empty yard into both channels' tables (site-dock 24).
    for lf in (store, ful):
        with pytest.raises(RuntimeError, match='drain_yard_drains'):
            lf.drain_yard_drains()
        assert lf._yard_drains == [], 'a leaf recorded the site yard row'
    parked = crd.drain_site_rows()
    assert len(parked) == 1 and len(parked[0]) == 4, parked
    assert crd.drain_site_rows() == [], 'the accessor did not start the list over'


# ── 8. the space view is TAGGED once there are two ────────────────────────────────

def test_two_leaves_contribute_tagged_views_and_one_does_not():
    """The tag is what partitions `empties`, and `compose_site_view` refuses an untagged
    contribution the moment there are two — so the absence at one leaf cannot survive into
    the coupled case. The tag is the channel the leaf BOUND under, never `regime_of` on a
    key: `BinKey` is a plain tuple and `regime_of` answers 'store' for every one of them."""
    from Inbound.space import SpaceTimeline
    from Warehouse.picking.Workload_Builder import drain_sku

    crd, store, ful = _site()
    for leaf in (store, ful):
        SpaceTimeline(drain_sku).attach(leaf)
    got = crd._freeze_views([store, ful], 7.0)
    assert [r for r, _v in got] == [_STORE, _FUL], got
    assert all(v.frozen_at == 7.0 for _r, v in got)

    solo = _manager(_yard(), crew=2)
    SpaceTimeline(drain_sku).attach(solo)
    lone = solo.receiving._freeze_views([solo], 7.0)
    assert [r for r, _v in lone] == [None], (
        'a composition of one was tagged; it partitions nothing, and the composer returns '
        'it by identity, which is what keeps every standing-yard run on disk identical')


# ── 9. the leaf accessors refuse once the scope is the site's ─────────────────────

#: The TEN reads a leaf must not answer for a site. The first four are the ones the design
#: named; the next two are worse than a wrong level -- they would hand one leaf the OTHER
#: channel's rows and restart a shared crew's clocks half-way through the site's batch; the
#: last three are the yard's own rows (site-dock 24), and `standing_yard_trailers` is the
#: worst of the nine because it does not DRAIN: two leaves reading it would bill every
#: trailer still on site at run end twice.
_SITE_SCOPED_READS = ('dock_depth', 'in_transit_qty', 'transit_snapshot',
                      'receiving_snapshot', 'drain_receiving_records',
                      'drain_repack_records', 'drain_yard_drains',
                      'drain_yard_trailers', 'standing_yard_trailers',
                      'lead_queue_depth')


def _read(mgr, name):
    got = getattr(mgr, name)
    return got() if callable(got) else got


def test_the_leaf_scoped_reads_answer_before_the_second_leaf_binds():
    """Non-vacuity for the refusals below, and the byte-identity half: an UNCOUPLED leaf
    answers all ten exactly as it always did."""
    solo = _manager(_yard(), crew=2)
    assert solo.site_scoped is False
    for name in _SITE_SCOPED_READS:
        _read(solo, name)


def test_every_site_scoped_read_refuses_on_a_coupled_leaf():
    """A leaf reporting `dock_depth == 0` while the site dock is backed up is the
    silent-wrong-number class this repo keeps getting bitten by. The flag is stamped on
    EVERY bound leaf when the second binds -- the first one retroactively -- because scope
    is a property of the site, not of bind order."""
    _crd, store, ful = _site()
    for leaf in (store, ful):
        assert leaf.site_scoped is True
        for name in _SITE_SCOPED_READS:
            with pytest.raises(RuntimeError, match='coupled site'):
                _read(leaf, name)


# ── 10. the site receiving clock ──────────────────────────────────────────────────

def _day(length: float = 1000.0):
    return WorkDay(length=length)


def test_the_site_day_is_based_at_the_shift_start_or_the_carry():
    """`max(day.start_of(i), recv_clock)` — the receivers start at shift start and work
    what is standing, or carry on from where yesterday's overrun left them. Never either
    leaf's `arm_clock`: that is a PICK crew's release instant, and basing the dock on it
    would idle the site's receivers whenever a pick crew overran its day."""
    crd, _store, _ful = _site(day=_day())
    base, deadline = crd.open_batch(2)
    assert base == 2000.0 and deadline == pytest.approx(1000.0)

    crd.recv_clock = 2400.0
    crd._open = None                      # a fresh day, without replaying a whole batch
    crd._owed_records = set()
    base, deadline = crd.open_batch(2)
    assert base == 2400.0, 'the carry did not win over the shift start'
    assert deadline == pytest.approx(600.0), 'the whistle is not the rest of THAT day'


def test_open_batch_is_idempotent_per_day_so_both_leaves_read_one_base():
    """ONE BASE, ASKED FOR TWICE. Both leaves stamp their unload rows from the same epoch
    because there is one crew on one dock; the first call computes and the rest read. A
    recompute between the two would hand the second leaf a base the first never used."""
    crd, _store, _ful = _site(day=_day())
    first = crd.open_batch(3)
    crd.recv_clock = 99_999.0             # a carry that would move a recomputed base
    assert crd.open_batch(3) == first, 'the second caller recomputed the day'


def test_the_carry_is_committed_when_the_last_leaf_reports():
    """The reset has ONE owner. A leaf committing the carry before the other has recorded
    would rebase the second leaf's rows against an epoch it never ran in."""
    crd, store, ful = _site(day=_day())
    crd.open_batch(0)
    crd.note_records(store, 120.0)
    assert crd.recv_clock == 0.0, 'the carry moved before every leaf reported'
    crd.note_records(ful, 80.0)
    assert crd.recv_clock == 120.0, 'the carry is not the LAST finish across the site'


def test_a_day_with_no_records_anywhere_leaves_the_carry_alone():
    """The crew is where it was — exactly as an unpooled leaf leaves `recv_clock` alone."""
    crd, store, ful = _site(day=_day())
    crd.recv_clock = 55.0
    crd.open_batch(0)
    crd.note_records(store, None)
    crd.note_records(ful, None)
    assert crd.recv_clock == 55.0


def test_a_second_report_from_one_leaf_is_refused():
    crd, store, ful = _site(day=_day())
    crd.open_batch(0)
    crd.note_records(store, 10.0)
    with pytest.raises(RuntimeError, match='twice'):
        crd.note_records(store, 10.0)


def test_a_day_that_opens_while_a_leaf_still_owes_records_is_refused():
    """The silent version of this is a day opened under a leaf that has not stamped its
    rows yet, which rebases them against the next day's epoch."""
    crd, store, _ful = _site(day=_day())
    crd.open_batch(0)
    crd.note_records(store, 10.0)
    with pytest.raises(RuntimeError, match='still owes records'):
        crd.open_batch(1)


def test_a_site_clock_without_a_working_day_refuses():
    """Coupling is an era feature and the era completes the grid, so this is unreachable by
    design rather than by luck — which is a reason to state it, not to omit it."""
    crd, _store, _ful = _site()
    with pytest.raises(RuntimeError, match='working day'):
        crd.open_batch(0)


def test_the_last_report_restarts_the_site_docks_crew_clocks():
    """THE RESET HAS ONE OWNER, and on a coupled run it is this method.

    Uncoupled the owner is `Dock.drain_records`, reached through the leaf accessor
    `drain_receiving_records` — which REFUSES on a coupled leaf. Removing the old owner
    without appointing a new one is silent and cumulative: the dock's batch-local clocks
    would carry across days while the runner kept adding an epoch, so every row after day 0
    would be stamped late by every preceding day's receiving seconds, and eventually
    `can_start` would be false from the first unload while `cut` reported a full backlog.
    """
    crd, store, ful = _site(day=_day())
    crd.open_batch(0)
    crd.dock.charge(120.0)                     # a day's work on the shared crew
    assert crd.dock.finish > 0.0, 'the fixture charged nothing, so the reset is vacuous'
    crd.note_records(store, 120.0)
    assert crd.dock.finish > 0.0, (
        'the clocks restarted before every leaf reported — the other leaf is about to '
        'stamp its rows against a clock that has already gone back to zero')
    crd.note_records(ful, None)
    assert crd.dock.finish == 0.0, (
        'the site dock crew clocks were never restarted; the next day rows are stamped '
        'late by today receiving seconds, and nothing raises')


def test_a_drain_against_a_different_day_than_the_clocks_is_refused():
    """The put pool states the reason and it is the same one here: a crew gated on a
    different day than the epoch its rows are stamped from does work nobody has the hours
    for, and every row of it looks ordinary."""
    crd, store, ful = _site(day=_day())
    _base, deadline = crd.open_batch(0)
    _mixed_trailer(crd, store, ful)
    with pytest.raises(RuntimeError, match='drained against a deadline'):
        crd.receive([store, ful], deadline + 1.0)
    crd.receive([store, ful], deadline)        # the matching one is accepted


def test_one_bound_leaf_still_takes_the_single_leaf_route():
    """ONE threshold for "is the site real yet", and it is the SECOND leaf — the same one
    `bind` stamps `site_scoped` on and `_freeze_views` tags on. Binding one leaf to get the
    site clock must not switch the drain to a routing model there is nothing to route."""
    crd, store = _half_site()
    _dispatch(store, _STORE_SKUS[0], 6, POSITION_VOLUME // 2, 10_000.0)
    store._now_s = 10_000.0
    store._release_arrivals()

    row = crd.receive([store], None)

    assert row is not None, 'a single bound leaf was treated as a site and lost its row'
    assert store._putaway_seq > 0
    assert crd.drain_site_rows() == [], 'the row was parked AND returned'


def test_an_unbound_coordinator_refuses_two_leaves():
    """The return shape and the partial-drain refusal key on the SAME fact. Keyed
    differently, an unbound coordinator handed two leaves passes the refusal, parks its row
    at site scope and returns None — and `drain` drops it, so the row is written nowhere."""
    transit = _yard()
    crd = SiteReceiving(Dock(DockSpec(size=2, sources=('reorder', 'trailer'))), transit)
    a = _leaf(transit, _STORE_SKUS, ful=False)
    b = _leaf(transit, _FUL_SKUS, ful=True)
    with pytest.raises(RuntimeError, match='no way to say whose merchandise'):
        crd.receive([a, b], None)


def test_a_leaf_with_no_epoch_beside_one_with_an_epoch_is_refused():
    """An unstamped leaf is not "the same instant as the others" — it is a leaf the driver
    forgot to hand the site epoch to, and taking the other's silently would rank one yard
    against a "now" that leaf never ran in."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    ful._now_s = None
    with pytest.raises(ValueError, match='some leaves .* carry an epoch'):
        crd.receive([store, ful], None)


def test_an_ulp_of_epoch_difference_is_within_tolerance():
    """Floats compare with a tolerance, never `==` (CLAUDE.md section 2). The driver hands
    both leaves the same value, so any real gap is a defect — but a value that has been
    through an addition and back may differ by an ulp on a clock measured in millions of
    seconds, and refusing that would be a refusal nobody could act on."""
    crd, store, ful = _site()
    _mixed_trailer(crd, store, ful)
    ful._now_s = store._now_s + 1e-9
    crd.receive([store, ful], None)            # does not raise


def test_a_drain_with_no_leaf_refuses_from_both_entry_points():
    crd, _store, _ful = _site()
    with pytest.raises(ValueError, match='no leaf'):
        crd.receive([], None)
    with pytest.raises(ValueError, match='no leaf'):
        crd.drain([])


def test_records_reported_before_the_day_opens_say_so():
    """Without this the first report raises "reported twice in site day None", which names
    the wrong cause."""
    crd, store, _ful = _site(day=_day())
    with pytest.raises(RuntimeError, match='before the site day was opened'):
        crd.note_records(store, 1.0)


def test_an_unbound_leaf_cannot_report_records():
    crd, _store, _ful = _site(day=_day())
    crd.open_batch(0)
    stray = _leaf(crd.transit, (301,), ful=False)
    with pytest.raises(ValueError, match='without being bound'):
        crd.note_records(stray, 1.0)


# ── 11. the site dock, partitioned: one drain, two channels' rows ─────────────────

def _open_and_drain(crd, store, ful, *, day_index: int = 0, deadline=None):
    """Open the site day, land one MIXED trailer and drain the dock once.

    The shape the driver runs: `open_batch` first (it is what decides which day the rows
    belong to), then the site's one `receive` over both leaves.
    """
    _base, day_deadline = crd.open_batch(day_index)
    _mixed_trailer(crd, store, ful)
    # The whistle MUST be the day the clocks are running on -- `receive` refuses any other,
    # which is the guard that stops a crew doing work nobody has the hours for.
    crd.receive([store, ful], day_deadline if deadline is None else deadline)


def test_the_partition_hands_each_leaf_its_own_unload_rows():
    """One dock is one drain, and ADR-0005 puts the pack-denominated half of it back on the
    owning channel.  Routed by SKU through the same `{sku: leaf}` dict step 1 packs by, so
    an unload row and the lot that produced it cannot land in different channels."""
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    s_rows = crd.drain_records_for(store)
    f_rows = crd.drain_records_for(ful)
    assert s_rows and f_rows, (
        f'the mixed trailer produced {len(s_rows)} store and {len(f_rows)} fulfillment '
        f'row(s); a partition test over an empty side proves nothing')
    assert {r[2] for r in s_rows} <= set(_STORE_SKUS)
    assert {r[2] for r in f_rows} <= set(_FUL_SKUS)


def test_the_dock_is_partitioned_once_and_served_to_each_leaf_once():
    """A DRAIN, so a second call must raise rather than hand over rows that are no longer
    anybody's.  The same "compute once, serve each caller once" contract `open_batch` keeps,
    with a sharper reason."""
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    crd.drain_records_for(store)
    with pytest.raises(RuntimeError, match='twice in site day'):
        crd.drain_records_for(store)
    # the OTHER leaf is untouched by the first leaf's second attempt
    assert crd.drain_records_for(ful) is not None


def test_the_snapshot_shares_close_against_the_docks_own_totals():
    """THE CLOSURE, which is the whole reason the decomposition is trustworthy: the shares
    are accrued independently of the dock's own counters -- the seconds especially, which
    come from each leaf's own `receiving_seconds` and are the only surface that sees a
    repack -- so summing them back is a real check rather than a restatement."""
    crd, store, ful = _site(day=_day())
    dock = crd.dock
    _open_and_drain(crd, store, ful)
    site_unloaded, site_seconds = dock.unloaded, dock.seconds
    assert site_unloaded > 0 and site_seconds > 0.0, 'the drain unloaded nothing'
    s_snap = crd.snapshot_for(store)
    f_snap = crd.snapshot_for(ful)
    assert s_snap[1] + f_snap[1] == site_unloaded
    assert s_snap[3] + f_snap[3] == pytest.approx(site_seconds, abs=1e-6)
    assert s_snap[1] > 0 and f_snap[1] > 0, (
        f'one channel took the whole drain: {s_snap} / {f_snap}')


def test_a_count_the_channels_cannot_account_for_is_refused():
    """The closure has to FAIL on a broken decomposition, not merely hold on a correct one.

    A dock counter the channels cannot account for is one channel carrying part of the
    other's receiving — the site total wearing one channel's name, which is the whole class
    of defect (`a-right-site-total-hides-two-wrong-shares`) this decomposition exists to
    make impossible rather than merely unlikely.
    """
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    crd.dock.unloaded += 1
    with pytest.raises(RuntimeError, match='channels account for'):
        crd.snapshot_for(store)


def test_labour_the_leaves_never_booked_is_refused():
    """The seconds half, and it is the sharper one: the per-leaf seconds come from each
    leaf's OWN `receiving_seconds` and the site total from the dock, so a gap is labour one
    of the two never saw — an unload handed to a leaf that did not book it, or a repack
    charged to a dock no leaf owns."""
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    crd.dock.seconds += 1.0
    with pytest.raises(RuntimeError, match='leaves accrued'):
        crd.snapshot_for(store)


def test_a_snapshot_asked_for_twice_in_one_site_day_is_refused():
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    crd.snapshot_for(store)
    with pytest.raises(RuntimeError, match='twice in site day'):
        crd.snapshot_for(store)


def test_the_partition_is_refused_before_the_site_day_is_opened():
    """`open_batch` is what decides which day these rows belong to, so a partition without
    one would stamp a batch nobody scheduled."""
    crd, store, _ful = _site(day=_day())
    with pytest.raises(RuntimeError, match='before the site day was opened'):
        crd.snapshot_for(store)


def test_the_transit_census_splits_by_sku_and_sums_to_the_sites_own():
    """All three transit reads come off ONE pass so they cannot disagree, and the split is
    by SKU -- the pack rule -- so rows and units SUM back to the site's census exactly."""
    crd, store, ful = _site(day=_day())
    crd.open_batch(0)
    for leaf, skus in ((store, _STORE_SKUS), (ful, _FUL_SKUS)):
        for sku in skus:
            _dispatch(leaf, sku, 6, POSITION_VOLUME // 2, 10_000.0)
    s_rows, s_qty, s_depth = crd.transit_census_for(store)
    f_rows, f_qty, f_depth = crd.transit_census_for(ful)
    assert s_rows and f_rows, 'one channel has nothing in flight; the split proves nothing'
    assert {r[0] for r in s_rows} <= set(_STORE_SKUS)
    assert {r[0] for r in f_rows} <= set(_FUL_SKUS)
    whole = crd.transit.snapshot()
    assert len(s_rows) + len(f_rows) == len(whole) == s_depth + f_depth
    assert s_qty + f_qty == sum(int(r[1]) for r in whole)


def test_the_dock_floor_is_decomposed_by_regime():
    """A LEVEL, so it neither drains nor resets -- and zero on every standing-yard run by
    construction, which is a fact about the standing model rather than a licence to assume
    it.  Asserted BOTH ways: empty here, and decomposed the moment anything stands."""
    crd, store, ful = _site(day=_day())
    _open_and_drain(crd, store, ful)
    assert crd.dock_depth_for(store) == 0 and crd.dock_depth_for(ful) == 0
    # PLANT one unit of each channel on the floor, so the zero above is a fact about the
    # standing model rather than a test that can only ever read zero.
    planted = {}
    for leaf, skus, ful_flag in ((store, _STORE_SKUS, False), (ful, _FUL_SKUS, True)):
        _plans, items = leaf.plan_lot(skus[0], 6, 'reorder')
        crd.dock.items.append(items[0])
        planted[regime_of(items[0].unit)] = leaf
    assert set(planted) == {_STORE, _FUL}, f'the plant produced {sorted(planted)}'
    assert crd.dock_depth_for(store) == 1 and crd.dock_depth_for(ful) == 1
    assert crd.dock_depth_for(store) + crd.dock_depth_for(ful) == len(crd.dock.items)


def test_the_trailer_stamps_are_the_sites_and_the_leaves_refuse_them():
    """Both leaves hold ONE transit, so a leaf draining it would take every trailer on the
    site into its own table and leave the other channel's yard looking empty."""
    # A ONE-SECOND DAY, so the whistle stops the crew before the trailer empties and a
    # stamp is left STANDING: the censored tail is the row two leaves would bill twice, and
    # a test over an empty tail would prove nothing about it.
    crd, store, ful = _site(day=_day(length=1.0))
    _open_and_drain(crd, store, ful)
    for leaf in (store, ful):
        with pytest.raises(RuntimeError, match='coupled site'):
            leaf.drain_yard_trailers()
        with pytest.raises(RuntimeError, match='coupled site'):
            leaf.standing_yard_trailers()
        with pytest.raises(RuntimeError, match='coupled site'):
            leaf.drain_yard_drains()
    standing = crd.standing_trailer_stamps()
    assert standing, 'the mixed trailer left no standing stamp to be counted twice'
    # READ, never drained: a second read returns the same rows, which is exactly why two
    # leaves reading it would bill every censored trailer twice.
    assert crd.standing_trailer_stamps() == standing
    # the DRAIN-shaped sibling empties, and the SITE row is parked rather than handed down
    assert crd.drain_site_rows() and crd.drain_site_rows() == []
