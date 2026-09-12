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

    row = mgr.receiving.receive(mgr, None)

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
