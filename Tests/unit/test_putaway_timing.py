"""test_putaway_timing.py — put-away costs seconds, and costs nothing else.

Put-away was a zero-duration phase for the life of the project: `check_reorders` →
`_stock` → `_execute_placement` moved inventory and produced no time value anywhere. A
strategy that deferred placement therefore looked free, and a second crew had nothing to be
measured in.

Two things this pins.

**It is the pick model, not a second one.** Travel to the location plus the same
height-bracketed handling expression, through the same `cost_model` primitives, with
defaults that mirror `PickConfig`'s own. This project has already paid for two default sets
that drifted 55× apart; a put model with independent magic numbers would be the same bill
again.

**It is ADDITIVE.** Turning it on must not move a single pick result — same items, same
order, same instants — because the two streams are simulated independently and merged, and
nothing here models contention. That is the property that makes it safe to enable by
default, and it is asserted rather than asserted-in-prose.

Run:  python -m pytest Tests/unit/test_putaway_timing.py -q
"""
from __future__ import annotations

import inspect
import types

import pytest

from Warehouse.kernel.cost_model import (
    DEFAULT_HEIGHT_BRACKETS, SpeedProfile, handle_var, height_multiplier, per_pick,
)
from Warehouse.operations.putaway import PutawayCost, put_cost

FOOT = SpeedProfile(2.0, 4.0)
MACHINE = SpeedProfile(3.0, 2.0)


# ── the cost expression ───────────────────────────────────────────────────────────

def test_it_is_travel_plus_the_pick_handling_expression():
    """Written out longhand here, so a change to either half fails loudly rather than
    quietly re-tuning put-away."""
    c = PutawayCost()
    got = put_cost(120.0, 48.0, weight=10, volume=100, quantity=3, speed=MACHINE, cost=c)
    travel = 120.0 * MACHINE.x_pace + 48.0 * MACHINE.y_pace
    handling = per_pick(height_multiplier(DEFAULT_HEIGHT_BRACKETS, 48.0), c.intercept,
                        handle_var(10, 100, c.weight_coef, c.volume_coef), 3)
    assert got == pytest.approx(travel + handling)


def test_a_higher_bin_costs_more_to_reach_and_more_to_handle():
    """Both terms respond to height: y-travel scales, and the bracket multiplier steps."""
    low = put_cost(100.0, 10.0, 10, 100, 1, MACHINE, PutawayCost())
    high = put_cost(100.0, 300.0, 10, 100, 1, MACHINE, PutawayCost())
    assert high > low


def test_a_faster_crew_puts_away_faster():
    slow = put_cost(200.0, 0.0, 10, 100, 1, SpeedProfile(1.0, 1.0), PutawayCost())
    fast = put_cost(200.0, 0.0, 10, 100, 1, SpeedProfile(4.0, 1.0), PutawayCost())
    assert fast < slow


def test_the_two_modes_give_different_costs():
    """The whole reason Mode exists: a machine and a walker are not the same putter."""
    at = dict(x_phys=200.0, y_phys=100.0, weight=10, volume=100, quantity=1)
    assert put_cost(**at, speed=FOOT, cost=PutawayCost()) != \
           put_cost(**at, speed=MACHINE, cost=PutawayCost())


def test_more_units_cost_more_but_the_travel_is_paid_once():
    """`per_pick` is mult*(intercept + qty*var): the trip is not re-charged per unit."""
    one = put_cost(100.0, 0.0, 10, 100, 1, MACHINE, PutawayCost())
    ten = put_cost(100.0, 0.0, 10, 100, 10, MACHINE, PutawayCost())
    assert ten > one
    assert ten < 10 * one


def test_the_defaults_mirror_the_pick_models():
    """Not a second set of magic numbers to reconcile later."""
    from Warehouse.picking.Pick import PickConfig
    pc, put = PickConfig(), PutawayCost()
    assert (put.intercept, put.weight_coef, put.volume_coef) == \
           (pc.pick_intercept, pc.pick_weight_coef, pc.pick_volume_coef)
    assert put.height_brackets == pc.height_brackets


# ── the manager binding ───────────────────────────────────────────────────────────

def _mgr():
    """A manager with the timing seam bound, without building a warehouse."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    m = Inventory_Manager.__new__(Inventory_Manager)
    m._put_speed = None
    m._put_cost = None
    m._put_clock = 0.0
    m._put_clocks = [0.0]
    m._put_seconds = 0.0
    m._put_records = []
    return m


def _unit(sku=1, qty=2, weight=10, volume=100):
    return types.SimpleNamespace(
        quantity=qty, order=types.SimpleNamespace(sku=sku, weight=weight,
                                                  volume=lambda: volume))


def _bin(x=100.0, y=48.0, aisle=7):
    return types.SimpleNamespace(x_phys=x, y_phys=y, location=(aisle, 1, 1))


def test_timing_is_off_until_it_is_bound():
    m = _mgr()
    assert m._put_speed is None
    assert m.putaway_seconds == 0.0


def test_binding_it_makes_a_placement_cost_seconds():
    m = _mgr()
    m.enable_putaway_timing(MACHINE)
    m._cost_putaway(_unit(), _bin(), 'reorder')
    assert m.putaway_seconds > 0.0
    assert m.putaway_seconds == pytest.approx(
        put_cost(100.0, 48.0, 10, 100, 2, MACHINE, PutawayCost()))


def test_the_crews_clock_runs_forward_without_gaps():
    """Each put starts where the previous ended — the put stream's own timeline."""
    m = _mgr()
    m.enable_putaway_timing(MACHINE)
    for _ in range(3):
        m._cost_putaway(_unit(), _bin(), 'reorder')
    recs = m.drain_putaway_records()
    assert len(recs) == 3
    for prev, nxt in zip(recs, recs[1:]):
        assert nxt[0] == pytest.approx(prev[0] + prev[1])
    assert m.putaway_seconds == pytest.approx(sum(r[1] for r in recs))


def test_a_record_carries_where_the_unit_came_from():
    """The PutawayItem provenance, so a trailer becomes a fourth source without a reshape."""
    m = _mgr()
    m.enable_putaway_timing(FOOT)
    m._cost_putaway(_unit(sku=42, qty=5), _bin(aisle=3), 'reslot')
    (t0, dur, sku, qty, aisle, x, y, source, worker), = m.drain_putaway_records()
    assert (sku, qty, aisle, source, worker) == (42, 5, 3, 'reslot', 0)
    assert dur > 0 and t0 == 0.0 and (x, y) == (100.0, 48.0)


def test_draining_hands_over_ownership():
    m = _mgr()
    m.enable_putaway_timing(FOOT)
    m._cost_putaway(_unit(), _bin(), 'intake')
    assert len(m.drain_putaway_records()) == 1
    assert m.drain_putaway_records() == []
    assert m.putaway_seconds > 0.0, 'draining the rows must not reset the labor total'


# ── additive: it must not touch the pick path ─────────────────────────────────────

def test_costing_a_put_does_not_write_a_bin():
    """`_cost_putaway` is called from inside `_execute_placement`, which the mutation
    allowlist already names. It must not become a sixth bin writer itself."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    src = inspect.getsource(Inventory_Manager._cost_putaway)
    assert '.storage' not in src
    assert 'storage.quantity' not in src


def test_the_charge_happens_after_the_placement_is_committed():
    """Ordering matters: a cost model that raised would otherwise abort a half-applied
    placement."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    src = inspect.getsource(Inventory_Manager._execute_placement)
    assert src.index('bin_.storage = unit') < src.index('_cost_putaway')


def test_the_binder_follows_the_existing_opt_in_precedent():
    """`enable_sigma_fd` is the pattern: the harness binds, the domain never reads CONFIG."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    assert hasattr(Inventory_Manager, 'enable_putaway_timing')
    doc = inspect.getdoc(Inventory_Manager.enable_putaway_timing) or ''
    assert 'enable_sigma_fd' in doc


# ── the drain is a batch boundary ─────────────────────────────────────────────────

def test_draining_restarts_the_crews_clock():
    """The bug this closes, and it was measured on a real eight-batch arm.

    Records carry `t_start` on the crew's clock measured from the START OF THE BATCH,
    because `work_events.put_rows` offsets them onto the arm's absolute axis by adding the
    batch epoch — and the epoch is not known until after the batch's picks are simulated,
    so the manager cannot stamp absolute times itself.

    Without the reset, `_put_clock` accumulated across the whole arm while `put_rows` still
    added the epoch, so every put after batch 0 was stamped too late by the total put-away
    seconds of every preceding batch. On a store arm: batch 7's puts landed at `t_local`
    53,769–64,152 s against a 17,906 s batch, and batch 6's puts overran batch 7's picks.
    """
    m = _mgr()
    m.enable_putaway_timing(MACHINE)

    m._cost_putaway(_unit(), _bin(), 'reorder')
    first = m.drain_putaway_records()
    assert first[0][0] == 0.0

    m._cost_putaway(_unit(), _bin(), 'reorder')
    second = m.drain_putaway_records()
    assert second[0][0] == 0.0, (
        'the put crew\'s clock carried across a batch boundary; put_rows adds the batch '
        'epoch on top, so every later put row would be stamped too late')


def test_every_batch_starts_its_records_at_zero():
    m = _mgr()
    m.enable_putaway_timing(FOOT)
    for _batch in range(4):
        for _ in range(3):
            m._cost_putaway(_unit(), _bin(), 'reorder')
        recs = m.drain_putaway_records()
        assert recs[0][0] == 0.0
        for prev, nxt in zip(recs, recs[1:]):
            assert nxt[0] == pytest.approx(prev[0] + prev[1])


def test_the_labor_total_still_accumulates_across_batches():
    """The clock restarts; the LABOR does not. They are different questions."""
    m = _mgr()
    m.enable_putaway_timing(FOOT)
    m._cost_putaway(_unit(), _bin(), 'reorder')
    one = m.putaway_seconds
    m.drain_putaway_records()
    m._cost_putaway(_unit(), _bin(), 'reorder')
    assert m.putaway_seconds == pytest.approx(2 * one)


# ── a crew of N works like N people ───────────────────────────────────────────────

def _crew_mgr(size, speed=MACHINE):
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    m = Inventory_Manager.__new__(Inventory_Manager)
    m._put_speed = m._put_cost = None
    m._put_clock = 0.0
    m._put_clocks = [0.0]
    m._put_seconds = 0.0
    m._put_records = []
    m.enable_putaway_timing(speed, size=size)
    return m


def test_a_crew_of_one_is_exactly_the_old_serial_clock():
    """The default, and the reason this change is byte-identical for today's runs."""
    m = _crew_mgr(1)
    for _ in range(4):
        m._cost_putaway(_unit(), _bin(), 'reorder')
    recs = m.drain_putaway_records()
    assert [r[8] for r in recs] == [0, 0, 0, 0], 'every put is worker 0'
    for prev, nxt in zip(recs, recs[1:]):
        assert nxt[0] == pytest.approx(prev[0] + prev[1]), 'strictly back to back'


def test_two_putters_halve_the_makespan():
    """The defect: one serial clock meant a crew of two took exactly as long as a crew of
    one, while `put_rows` round-robined the rows across both — so the record claimed two
    people were working and the instants said otherwise."""
    def makespan(size):
        m = _crew_mgr(size)
        for _ in range(6):
            m._cost_putaway(_unit(), _bin(), 'reorder')
        recs = m.drain_putaway_records()
        return max(r[0] + r[1] for r in recs), m.putaway_seconds

    one_span, one_labor = makespan(1)
    two_span, two_labor = makespan(2)
    assert two_labor == pytest.approx(one_labor), 'the same work: LABOR is unchanged'
    assert two_span == pytest.approx(one_span / 2), 'two people: the MAKESPAN halves'


def test_each_worker_runs_its_own_gapless_clock():
    m = _crew_mgr(3)
    for _ in range(9):
        m._cost_putaway(_unit(), _bin(), 'reorder')
    recs = m.drain_putaway_records()
    assert {r[8] for r in recs} == {0, 1, 2}, 'all three worked'
    for w in (0, 1, 2):
        own = [r for r in recs if r[8] == w]
        assert own[0][0] == 0.0
        for prev, nxt in zip(own, own[1:]):
            assert nxt[0] == pytest.approx(prev[0] + prev[1])


def test_no_worker_is_in_two_places_at_once():
    """The same invariant the DB-level reconciliation checks, at the source."""
    m = _crew_mgr(2)
    for i in range(8):
        m._cost_putaway(_unit(qty=1 + i % 3), _bin(x=10.0 * i), 'reorder')
    recs = m.drain_putaway_records()
    for w in (0, 1):
        own = sorted((r[0], r[1]) for r in recs if r[8] == w)
        for (t0, d0), (t1, _d1) in zip(own, own[1:]):
            assert t1 >= t0 + d0 - 1e-9


def test_the_drain_restarts_every_workers_clock():
    m = _crew_mgr(2)
    for _ in range(4):
        m._cost_putaway(_unit(), _bin(), 'reorder')
    m.drain_putaway_records()
    for _ in range(2):
        m._cost_putaway(_unit(), _bin(), 'reorder')
    assert all(r[0] == 0.0 for r in m.drain_putaway_records()), 'both start the batch at 0'


def test_a_crew_of_zero_is_rejected():
    with pytest.raises(ValueError, match='does no work'):
        _crew_mgr(0)
