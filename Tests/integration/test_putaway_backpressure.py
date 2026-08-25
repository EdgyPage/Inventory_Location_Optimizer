"""test_putaway_backpressure.py — a full queue refuses; nothing is lost, nothing jumps.

Staging is the floor space a stream has. A queue at its limit refuses admission and the
producer holds — which is the point, because "inbound packs faster than put-away absorbs" is
the condition being modelled, and a silent drop would make it look like it never happened.

Three things have to be true for that to be a model rather than a leak:

  CONSERVATION   every unit ends up placed or waiting somewhere countable. A held unit that
                 falls out of `queue_depth` is indistinguishable from one that vanished.
  ORDER          a held unit keeps its ARRIVAL stamp, so waiting on the dock makes it older
                 rather than younger. Stamping at admission would turn backpressure into a
                 priority inversion — the pallet that waited three batches would enter as
                 the newest thing in the warehouse.
  FAIRNESS       retry stops at the first refusal per queue. Scanning past a blocked item
                 for a younger one that happens to fit is the same inversion by another
                 route, and no real dock works that way.

Run:  python -m pytest Tests/integration/test_putaway_backpressure.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from Warehouse.inventory.put_queue import (
    ANY, PALLET, SINGLETON, PutQueueSet, PutQueueSpec,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


def _mgr(specs=None, n_skus=400):
    a = cs.build_assets(n_skus=n_skus, bins_per_aisle=30,
                        strategy='uni_rank_labor_norsl', seed=42,
                        coverage=2.0, safety=0.4)
    if specs is not None:
        a.mgr.put_queues = PutQueueSet(specs)
    return a


# ── nothing is lost ───────────────────────────────────────────────────────────────

def test_a_tight_staging_limit_holds_units_rather_than_dropping_them():
    """The whole run, with room for four items at a time. Everything the manager was ever
    handed must be placed, queued or held — never unaccounted for."""
    a = _mgr([PutQueueSpec('tight', accepts=ANY, staging=4)])
    mgr = a.mgr
    admitted, placed = [], []
    orig_admit, orig_place = mgr._admit, mgr._execute_placement

    def cap_admit(unit, source, _o=orig_admit):
        admitted.append(unit)
        return _o(unit, source)

    def cap_place(unit, bin_, *, source=None, score=None, score_rank=None, policy=None,
                  _o=orig_place):
        placed.append(unit)
        _o(unit, bin_, source=source)
    mgr._admit, mgr._execute_placement = cap_admit, cap_place

    cs.run_meso(a, n_batches=6, seed=42)

    assert len(admitted) > 200, f'only {len(admitted)} arrivals — nothing was stressed'
    assert mgr.held_depth > 0, 'the staging limit never bit; this test proves nothing'
    # A unit is placed, still queued, or held. Repacks split one into several, so the
    # accounting is one-way: nothing may DISAPPEAR.
    accounted = len(placed) + len(mgr._stock_queue) + mgr.held_depth
    assert accounted >= len(admitted), (
        f'{len(admitted)} arrived but only {accounted} are placed/queued/held')


def test_queue_depth_counts_what_is_held():
    """A staging limit must not make the backlog look like it evaporated."""
    a = _mgr([PutQueueSpec('tight', accepts=ANY, staging=2)])
    mgr = a.mgr
    cs.run_meso(a, n_batches=3, seed=42)
    assert mgr.held_depth > 0
    assert mgr.queue_depth == len(mgr._stock_queue) + mgr.held_depth
    assert mgr.queue_depth > len(mgr._stock_queue), 'held units are invisible in the depth'


def test_an_unbounded_queue_holds_nothing():
    """The default configuration must be untouched by any of this."""
    a = _mgr()
    cs.run_meso(a, n_batches=4, seed=42)
    assert a.mgr.held_depth == 0
    assert a.mgr.queue_depth == len(a.mgr._stock_queue)


# ── nothing jumps the queue ───────────────────────────────────────────────────────

def test_a_held_item_keeps_its_arrival_stamp():
    """Waiting on the dock makes a unit OLDER. Stamping at admission instead would make the
    longest-waiting item the newest thing in the warehouse the moment it got in."""
    a = _mgr([PutQueueSpec('tight', accepts=ANY, staging=1)], n_skus=120)
    mgr = a.mgr
    mgr.put_queues.queues[0].items.clear()
    mgr._held.clear()

    units = [_u() for _ in range(4)]
    for u in units:
        mgr._admit(u, 'intake')
    assert len(mgr._stock_queue) == 1 and mgr.held_depth == 3
    stamps = [mgr._stock_queue[0].age] + [it.age for it in mgr._held]
    assert stamps == sorted(stamps), f'arrival stamps are not monotonic: {stamps}'

    # Free the space; the OLDEST held item takes it, not the newest.
    mgr.put_queues.queues[0].items.clear()
    assert mgr._admit_held() == 1
    assert mgr._stock_queue[0].age == stamps[1]
    assert mgr.held_depth == 2


def test_retry_stops_at_the_first_blocked_item_rather_than_scanning_past_it():
    """Letting a younger item slip into a gap an older one could not use is the same
    inversion the age stamp exists to prevent."""
    a = _mgr([PutQueueSpec('tight', accepts=ANY, staging=1)], n_skus=120)
    mgr = a.mgr
    mgr.put_queues.queues[0].items.clear()
    mgr._held.clear()
    for _ in range(5):
        mgr._admit(_u(), 'intake')
    mgr.put_queues.queues[0].items.clear()

    ages = [it.age for it in mgr._held]
    assert mgr._admit_held() == 1, 'more than one item entered a queue with room for one'
    assert mgr._stock_queue[0].age == ages[0], 'a younger held item overtook an older one'
    assert [it.age for it in mgr._held] == ages[1:]


def test_two_queues_block_independently():
    """One stream backing up must not stall another. The retry marks a queue blocked, not
    the whole dock."""
    a = _mgr([PutQueueSpec('pallets', accepts=(PALLET,), staging=1),
              PutQueueSpec('rest', accepts=ANY)], n_skus=120)
    mgr = a.mgr
    for q in mgr.put_queues:
        q.items.clear()
    mgr._held.clear()

    mgr._admit(_u(PALLET), 'intake')                 # fills `pallets`
    mgr._admit(_u(PALLET), 'intake')                 # held
    mgr._admit(_u(SINGLETON), 'intake')              # held behind it? no — different queue
    assert mgr.held_depth == 1, 'the singleton was blocked by the pallet queue'
    assert len(mgr.put_queues['rest']) == 1
    assert len(mgr.put_queues['pallets']) == 1


def test_the_refusal_is_counted():
    """`blocked` is the only trace a refusal leaves. Without it the backpressure is real
    and invisible, which is worse than not modelling it."""
    a = _mgr([PutQueueSpec('tight', accepts=ANY, staging=1)], n_skus=120)
    mgr = a.mgr
    mgr.put_queues.queues[0].items.clear()
    mgr._held.clear()
    for q in mgr.put_queues:
        q.drain_counters()
    for _ in range(4):
        mgr._admit(_u(), 'intake')
    snap = mgr.put_queues.queues[0].drain_counters()
    assert snap['blocked'] == 3 and snap['admitted'] == 1


class _FakeUnit:
    __slots__ = ('unit_category', 'order', 'quantity')

    def __init__(self, cat):
        self.unit_category, self.order, self.quantity = cat, None, 1


def _u(cat=PALLET):
    return _FakeUnit(cat)
