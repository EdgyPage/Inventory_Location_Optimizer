"""test_day_cut.py — stopping a picker at the whistle, and carrying what it did not reach.

"if the work from pick or puts or inbound does not complete, I want the workload to rollover
to the next shift" — and mid-task, with "the partial state flattened to its atomic item,
quantity and inserted into the next batch to go through the existing task creation machinery."

WHERE THE CUT LANDS. Between bins, and only there. A pick is atomic in this model and so is
the walk to it, so there is nothing to halve; inventing a partial-handling model to cut
finer would be a bigger change than the feature. The check sits BEFORE the travel, so seconds
spent walking toward a bin the picker will not reach are never charged.

ONE DEFINITION OF THE CARRY. `Pick.carry_residue` is it — the PLANNED quantity at every bin
not reached, imported by both loops. Deliberately not "demand minus picked" computed later:
two definitions of one number is how a carry becomes either dead code or a double count, and
the consumer reads this rather than re-deriving it.

Run:  python -m pytest Tests/unit/test_day_cut.py -q
"""
from __future__ import annotations

import types

import pytest

from Optimization.metrics.Simulation_Analytics import (
    task_time_breakdown, task_travel_breakdown,
)
from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Warehouse.picking.Pick import PickConfig, PickSimulation, carry_residue
from Warehouse.picking.Workload_Builder import Task
from Warehouse.picking.fast_pick import DeferredPickSimulation

BOTH = (PickSimulation, DeferredPickSimulation)


def _order(sku, vol=100):
    return types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol)


def _bin(aisle, x, y, sku, qty=5, vol=100):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(aisle, x, y),
        storage=types.SimpleNamespace(order=_order(sku, vol), quantity=qty))


def _cfg(pickers=1, swap=0.0, one_way=False):
    return PickConfig(num_pickers=pickers, x_speed=4.0, y_speed=2.0, pick_intercept=5.0,
                      pick_weight_coef=0.2, pick_volume_coef=0.001, cart_swap_coef=swap,
                      cart=FulfillmentCart, one_way=one_way)


def _tasks(n_aisles=4, per_aisle=3):
    return [Task(a, [_bin(a, 40 * k + 20, 48 * (k % 3), a * 100 + k)
                     for k in range(per_aisle)],
                 {a * 100 + k: 2 for k in range(per_aisle)})
            for a in range(1, n_aisles + 1)]


def _planned_total(tasks):
    """Everything the batch scheduled, by the same definition the carry uses."""
    tot: dict = {}
    for t in tasks:
        carry_residue(t, 0, tot)
    return sum(tot.values())


def _span(cfg=None, tasks=None):
    cfg = cfg or _cfg(pickers=2)
    return max(e.time for e in PickSimulation(tasks or _tasks(), cfg).run())


# ── the cut is off unless asked for ───────────────────────────────────────────────

@pytest.mark.parametrize('Sim', BOTH)
def test_no_day_end_means_no_cut(Sim):
    """The default, and the reason this is opt-in: nothing changes until a caller asks."""
    sim = Sim(_tasks(), _cfg(pickers=2))
    events = sim.run()
    assert sim.carried == {}
    assert not [e for e in events if e.event_type == 'cut']


# ── conservation ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('Sim', BOTH)
@pytest.mark.parametrize('frac', [0.0, 0.1, 0.25, 0.5, 0.75, 0.99])
def test_picked_plus_carried_equals_planned(Sim, frac):
    """THE invariant. A cut that loses a unit deletes demand, and a cut that duplicates one
    invents it — either way the arm's conservation ledger breaks a batch later, far from the
    cause."""
    cfg = _cfg(pickers=2)
    planned = _planned_total(_tasks())
    sim = Sim(_tasks(), cfg, day_end=_span(cfg) * frac)
    events = sim.run()
    picked = sum(e.quantity or 0 for e in events if e.event_type == 'pick')
    assert picked + sum(sim.carried.values()) == planned, (
        f'{picked} picked + {sum(sim.carried.values())} carried != {planned} planned')


@pytest.mark.parametrize('Sim', BOTH)
def test_an_immediate_cut_carries_everything_and_picks_nothing(Sim):
    sim = Sim(_tasks(), _cfg(pickers=2), day_end=0.0)
    events = sim.run()
    assert not [e for e in events if e.event_type == 'pick']
    assert sum(sim.carried.values()) == _planned_total(_tasks())


@pytest.mark.parametrize('Sim', BOTH)
def test_a_day_longer_than_the_work_carries_nothing(Sim):
    cfg = _cfg(pickers=2)
    sim = Sim(_tasks(), cfg, day_end=_span(cfg) * 10)
    sim.run()
    assert sim.carried == {}


@pytest.mark.parametrize('Sim', BOTH)
def test_a_shorter_day_never_carries_less(Sim):
    """Monotonic in the obvious direction. A non-monotonic carry would mean the cut point
    changes WHICH work is skipped rather than only how much."""
    cfg = _cfg(pickers=2)
    span = _span(cfg)
    got = []
    for frac in (1.0, 0.75, 0.5, 0.25, 0.0):
        sim = Sim(_tasks(), cfg, day_end=span * frac)
        sim.run()
        got.append(sum(sim.carried.values()))
    assert got == sorted(got), f'carry is not monotonic as the day shortens: {got}'
    assert got[0] == 0 and got[-1] > 0


# ── the two loops agree, with the cut on ──────────────────────────────────────────

@pytest.mark.parametrize('frac', [0.0, 0.1, 0.25, 0.5, 0.75, 0.99, 1.0])
@pytest.mark.parametrize('pickers', [1, 3])
def test_both_loops_cut_identically(frac, pickers):
    """The event-by-event comparison, with the cut active — which is what it was written
    for. Two loops agreeing on totals but cutting at different bins would be invisible to
    every other lockstep guard in the repo."""
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
    from test_pick_stream_lockstep import _diff

    cfg = _cfg(pickers=pickers, swap=7.0)
    de = _span(cfg) * frac
    a = PickSimulation(_tasks(), cfg, day_end=de)
    b = DeferredPickSimulation(_tasks(), cfg, day_end=de)
    ea, eb = a.run(), b.run()
    problem = _diff(ea, eb, f'day_end={de:.3f}/{pickers}p')
    assert problem is None, problem
    assert a.carried == b.carried, f'{a.carried} vs {b.carried}'


# ── the cut event ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('Sim', BOTH)
def test_a_cut_task_emits_cut_and_not_task_end(Sim):
    """A truncated task must not be indistinguishable from a complete one. Every consumer
    that pairs task_start with task_end would otherwise count it as finished."""
    cfg = _cfg(pickers=1)
    events = Sim(_tasks(), cfg, day_end=_span(cfg, _tasks()) * 0.4).run()
    cuts = [e for e in events if e.event_type == 'cut']
    assert len(cuts) == 1, f'expected exactly one cut, got {len(cuts)}'
    cut_aisle = cuts[0].aisle_id
    ends = {e.aisle_id for e in events if e.event_type == 'task_end'}
    starts = {e.aisle_id for e in events if e.event_type == 'task_start'}
    assert cut_aisle in starts and cut_aisle not in ends
    assert len(starts) == len(ends) + 1, 'the cut task was also counted as ended'


@pytest.mark.parametrize('Sim', BOTH)
def test_a_cut_task_is_partially_done_not_untouched(Sim):
    """The cut has to land mid-task somewhere in this corpus, or every test above is really
    about whole-task boundaries and the mid-task claim is untested."""
    cfg = _cfg(pickers=1)
    span = _span(cfg, _tasks())
    seen_partial = False
    for frac in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        events = Sim(_tasks(), cfg, day_end=span * frac).run()
        for e in events:
            if e.event_type == 'cut' and 0 < e.bins_completed < e.total_bins:
                seen_partial = True
    assert seen_partial, 'no cut ever landed mid-task; the mid-task claim is untested'


@pytest.mark.parametrize('Sim', BOTH)
def test_the_cut_never_lands_mid_pick(Sim):
    """Between bins is the only legal instant. A cut that interrupted a pick would have to
    invent a partial-handling model, and the giveaway would be a `pick` with no `arrive`."""
    cfg = _cfg(pickers=2, swap=7.0)
    span = _span(cfg)
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        events = sorted(Sim(_tasks(), cfg, day_end=span * frac).run(),
                        key=lambda e: (e.picker_id, e.time))
        for pid in {e.picker_id for e in events}:
            seq = [e.event_type for e in events if e.picker_id == pid]
            for j, kind in enumerate(seq):
                if kind == 'pick':
                    assert 'arrive' in seq[:j], f'a pick with no arrive before it: {seq}'


# ── time is still fully accounted for ─────────────────────────────────────────────

@pytest.mark.parametrize('Sim', BOTH)
@pytest.mark.parametrize('frac', [0.25, 0.5, 0.75])
def test_the_decomposition_still_reconciles_across_a_cut(Sim, frac):
    """FIVE terms, not three. Handling is a fourth clock advance with no decomposition
    field, so `pick + nonpick + cart == duration` is false and always was. The cut event
    carries the pending travel accumulators precisely so no second is dropped."""
    cfg = _cfg(pickers=1, swap=7.0)
    events = Sim(_tasks(), cfg, day_end=_span(cfg, _tasks()) * frac).run()
    pick, nonpick, cart = task_travel_breakdown(events)
    travel, handling, other = task_time_breakdown(events)
    duration = max(e.time for e in events)
    assert abs((pick + nonpick + cart + handling + other) - duration) < 1e-9, (
        f'{pick + nonpick + cart + handling + other} != {duration}')


@pytest.mark.parametrize('Sim', BOTH)
def test_a_cut_charges_no_travel_toward_a_bin_it_never_reached(Sim):
    """The check precedes the travel. Charging the walk and then stopping would bill time
    for a trip that did not happen, and it would show up as a longer day than the day."""
    cfg = _cfg(pickers=1)
    span = _span(cfg, _tasks())
    for frac in (0.2, 0.45, 0.7):
        de = span * frac
        events = Sim(_tasks(), cfg, day_end=de).run()
        cuts = [e for e in events if e.event_type == 'cut']
        for c in cuts:
            assert c.time >= de, 'a cut fired before the day ended'
            prior = [e.time for e in events
                     if e.picker_id == c.picker_id and e.time <= c.time]
            assert c.time == max(prior), "the cut event is not the picker's last instant"


# ── the carry itself ──────────────────────────────────────────────────────────────

def test_carry_residue_is_the_plan_and_is_not_re_clamped_at_cut_time():
    """The carry is `task.planned`, which was already capped by bin capacity when the task
    was BUILT. What it must not do is clamp again against what the bin holds NOW: stock
    moves during a batch, and forgiving demand on that basis would delete work a restock is
    about to make pickable. The next batch re-derives its own bins.
    """
    t = Task(1, [_bin(1, 10, 0, 7, qty=4), _bin(1, 20, 0, 7, qty=4)], {7: 8})
    assert sum(t.planned) == 8, ('the fixture should plan its full demand', t.planned)

    # Another picker empties the first bin after the task was built.
    t.path[0].storage.quantity = 0
    got = carry_residue(t, 0, {})
    assert got == {7: 8}, (
        f'the carry was re-clamped against live stock: {got} vs the plan {t.planned}')


def test_carry_residue_skips_a_bin_with_no_stock():
    """An empty bin's planned quantity is the live-stock clamp's business
    (`unpicked_unavailable`), not the cut's (`unpicked_daycut`). Keeping the two causes
    apart is the whole point of the `carryover.reason` column."""
    b = _bin(1, 10, 0, 7, qty=1)
    t = Task(1, [b, _bin(1, 20, 0, 8, qty=5)], {7: 3, 8: 3})
    b.storage = None
    got = carry_residue(t, 0, {})
    assert 7 not in got and got.get(8)


def test_carry_residue_accumulates_into_a_shared_dict():
    """Several pickers merge into one carry, and the same SKU from two aisles adds."""
    into: dict = {}
    carry_residue(Task(1, [_bin(1, 10, 0, 5, qty=9)], {5: 2}), 0, into)
    carry_residue(Task(2, [_bin(2, 10, 0, 5, qty=9)], {5: 3}), 0, into)
    assert list(into) == [5] and into[5] > 3


@pytest.mark.parametrize('Sim', BOTH)
def test_a_task_the_picker_never_started_carries_whole(Sim):
    """Not a partial task — untouched work. With one picker and an immediate cut, every
    task after the first is unstarted."""
    cfg = _cfg(pickers=1)
    sim = Sim(_tasks(), cfg, day_end=0.0)
    sim.run()
    assert sum(sim.carried.values()) == _planned_total(_tasks())
