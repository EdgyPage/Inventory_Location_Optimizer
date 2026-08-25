"""test_pick_stream_lockstep.py — the two picker loops emit the SAME EVENTS, not just the
same totals.

Three lockstep guards already exist, and every one of them compares an AGGREGATE:

    test_travel_decomposition.py   the travel breakdown and axes — five sums
    test_scheduler.py              max(event time) — one number
    test_picker_clock_carry.py     done-time and event count

Two loops could stamp identical totals on differently-shaped event streams and pass all
three. That is not a hypothetical worry about the past: it is the specific thing a day-end
cut is most likely to do — stop a picker mid-task, redistribute which second lands in which
event, and leave every sum where it was.

So this file compares the streams ELEMENT BY ELEMENT: every field of every event, in order,
including all five travel accumulators.

WHY IT LANDS BEFORE THE CUT AND NOT AFTER. It has to pass on unchanged loops first, because
that run is the baseline. Written afterwards, a divergence it reported would be ambiguous —
the cut, or a difference that was always there and nothing looked for. It passes today, over
every combination of scheduler, lane model, picker count and start-time carry below, which
is the fact the cut will be measured against.

Floats are compared EXACTLY, against the repo's usual tolerance rule. The claim here is not
that two computations agree to within an epsilon; it is that they are the same arithmetic in
the same order, which is what "lockstep" means and what the decomposition test next door
already asserts with `==`.

Run:  python -m pytest Tests/unit/test_pick_stream_lockstep.py -q
"""
from __future__ import annotations

import random
import types

import pytest

from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.picking.Workload_Builder import Task
from Warehouse.picking.fast_pick import DeferredPickSimulation

#: Every field on a PickEvent that describes WHAT happened or WHEN. Anything added to the
#: event and not added here is silently outside the comparison, so the list is explicit
#: rather than derived from __dataclass_fields__ — a new field should have to be considered.
FIELDS = (
    'event_type', 'picker_id', 'time', 'aisle_id', 'sku', 'quantity', 'location',
    'bins_completed', 'total_bins', 'items_picked', 'total_items',
    'pick_travel_x', 'pick_travel_y', 'non_pick_travel_x', 'non_pick_travel_y', 'cart_move',
)


def _order(sku, vol):
    return types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol)


def _bin(aisle, x, y, sku, vol=100, qty=5):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(aisle, x, y),
        storage=types.SimpleNamespace(order=_order(sku, vol), quantity=qty))


def _cfg(*, pickers=1, swap=0.0, scheduler='round_robin', one_way=False):
    return PickConfig(num_pickers=pickers, x_speed=4.0, y_speed=2.0, pick_intercept=5.0,
                      pick_weight_coef=0.2, pick_volume_coef=0.001, cart_swap_coef=swap,
                      cart=FulfillmentCart, scheduler=scheduler, one_way=one_way)


def _row(e):
    return tuple(getattr(e, f) for f in FIELDS)


def _diff(ref, fast, label=''):
    """The first divergence, named. A bare `assert a == b` over forty tuples is unreadable,
    and an unreadable failure is one that gets muted rather than fixed."""
    a, b = [_row(e) for e in ref], [_row(e) for e in fast]
    if len(a) != len(b):
        return f'{label}: event COUNT differs — reference {len(a)}, production {len(b)}'
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        bad = [f'{f}: ref={xv!r} fast={yv!r}'
               for f, xv, yv in zip(FIELDS, x, y) if xv != yv]
        n = sum(1 for p, q in zip(a, b) if p != q)
        return (f'{label}: {n}/{len(a)} events differ; first at index {i} '
                f'({x[0]!r} by picker {x[1]}) — ' + '; '.join(bad))
    return None


# ── the fixtures, each shaped to exercise something the others do not ─────────────

def _two_picks(vol=100):
    """Entry travel AND inter-pick travel, on x only."""
    return [Task(1, [_bin(1, 50, 0, 1, vol), _bin(1, 150, 0, 2, vol)], {1: 1, 2: 1})]


def _with_height():
    """Both travel axes, across two height brackets."""
    return [Task(1, [_bin(1, 50, 96, 1), _bin(1, 150, 240, 2)], {1: 1, 2: 1})]


def _overflowing():
    """Volume large enough to force cart swaps, so `cart_move` is non-zero."""
    return [Task(1, [_bin(1, 50, 0, 1, 15_000), _bin(1, 150, 0, 2, 15_000)], {1: 1, 2: 1})]


def _multi_qty():
    """One SKU spread across two bins, so `planned` splits the demand per bin — the path the
    over-pick fix rewrote, and the one where a per-bin quantity can drift."""
    return [Task(1, [_bin(1, 50, 0, 1, qty=9), _bin(1, 150, 0, 1, qty=9)], {1: 12})]


def _many_aisles(n_aisles=6, per_aisle=4):
    return [Task(a, [_bin(a, 30 * k + 10, 48 * (k % 3), a * 100 + k)
                     for k in range(per_aisle)],
                 {a * 100 + k: 2 for k in range(per_aisle)})
            for a in range(1, n_aisles + 1)]


def _random(seed):
    rng = random.Random(seed)
    tasks = []
    for a in range(1, rng.randint(2, 7)):
        bins_, items = [], {}
        for k in range(rng.randint(1, 5)):
            sku = a * 100 + k
            bins_.append(_bin(a, rng.randrange(0, 400), rng.choice((0, 48, 96, 240)),
                              sku, vol=rng.choice((100, 900, 12_000)),
                              qty=rng.randint(1, 8)))
            items[sku] = rng.randint(1, 6)
        tasks.append(Task(a, bins_, items))
    return tasks


FIXTURES = {
    'two_picks': _two_picks,
    'with_height': _with_height,
    'overflowing': _overflowing,
    'multi_qty': _multi_qty,
    'many_aisles': _many_aisles,
}


# ── the comparison ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', sorted(FIXTURES))
@pytest.mark.parametrize('scheduler', ['round_robin', 'lpt'])
@pytest.mark.parametrize('pickers', [1, 3])
def test_the_two_loops_emit_identical_streams(name, scheduler, pickers):
    cfg = _cfg(pickers=pickers, swap=7.0, scheduler=scheduler)
    ref = PickSimulation(FIXTURES[name](), cfg).run()
    fast = DeferredPickSimulation(FIXTURES[name](), cfg).run()
    assert ref, 'the fixture produced no events'
    problem = _diff(ref, fast, f'{name}/{scheduler}/{pickers}p')
    assert problem is None, problem


@pytest.mark.parametrize('one_way', [False, True])
def test_both_lane_models_stay_in_lockstep(one_way):
    """`one_way` changes the exit-travel model, which is `non_pick_travel` — the accumulator
    a cut has to hand over correctly when it stops a picker between bins."""
    cfg = _cfg(pickers=2, swap=7.0, one_way=one_way)
    ref = PickSimulation(_many_aisles(), cfg).run()
    fast = DeferredPickSimulation(_many_aisles(), cfg).run()
    assert _diff(ref, fast, f'one_way={one_way}') is None, _diff(ref, fast)


@pytest.mark.parametrize('t0', [0.0, 1.0, 12345.678, 1e6])
def test_the_start_time_carry_stays_in_lockstep(t0):
    """Every picker is handed the batch epoch. A cut moves work across an epoch boundary, so
    an offset that shifted one loop's events differently from the other's would look exactly
    like a cut artefact later."""
    cfg = _cfg(pickers=2, swap=7.0)
    starts = [t0] * 2
    ref = PickSimulation(_many_aisles(), cfg, start_times=starts).run()
    fast = DeferredPickSimulation(_many_aisles(), cfg, start_times=starts).run()
    assert _diff(ref, fast, f't0={t0}') is None, _diff(ref, fast, f't0={t0}')


@pytest.mark.parametrize('seed', range(12))
def test_randomized_workloads_stay_in_lockstep(seed):
    cfg = _cfg(pickers=3, swap=7.0, scheduler='lpt')
    ref = PickSimulation(_random(seed), cfg).run()
    fast = DeferredPickSimulation(_random(seed), cfg).run()
    assert _diff(ref, fast, f'seed={seed}') is None, _diff(ref, fast, f'seed={seed}')


# ── the comparison is not vacuous ─────────────────────────────────────────────────

def test_the_fixtures_exercise_every_field_being_compared():
    """A stream of five `task_start` events would pass every assertion above and prove
    nothing. Each compared field has to actually vary somewhere in the corpus."""
    seen: dict = {f: set() for f in FIELDS}
    kinds: set = set()
    for name in FIXTURES:
        for pickers in (1, 3):
            for e in PickSimulation(FIXTURES[name](),
                                    _cfg(pickers=pickers, swap=7.0)).run():
                kinds.add(e.event_type)
                for f in FIELDS:
                    v = getattr(e, f)
                    seen[f].add(v if not isinstance(v, float) else round(v, 9))
    assert {'task_start', 'arrive', 'pick', 'task_end', 'done'} <= kinds, kinds
    assert 'cart_swap' in kinds, 'no fixture overflows a cart, so cart_move is never nonzero'
    flat = [f for f in FIELDS if len(seen[f]) < 2]
    assert not flat, f'these compared fields never vary, so comparing them proves nothing: {flat}'


def test_both_travel_axes_and_the_cart_are_all_nonzero_somewhere():
    """The five accumulators are the point of the comparison. A corpus where y-travel is
    always zero cannot tell a y-axis regression from silence."""
    tot = dict.fromkeys(
        ('pick_travel_x', 'pick_travel_y', 'non_pick_travel_x', 'non_pick_travel_y',
         'cart_move'), 0.0)
    for name in FIXTURES:
        for e in PickSimulation(FIXTURES[name](), _cfg(pickers=2, swap=7.0)).run():
            for f in tot:
                tot[f] += getattr(e, f)
    zero = [f for f, v in tot.items() if v == 0.0]
    assert not zero, f'never exercised: {zero}'


def test_the_comparison_reports_the_first_divergence_by_name():
    """The failure message is part of the test. An unreadable diff over forty tuples is one
    that gets muted rather than fixed."""
    ref = PickSimulation(_two_picks(), _cfg()).run()
    fast = PickSimulation(_two_picks(), _cfg()).run()
    assert _diff(ref, fast) is None
    fast[2].pick_travel_x += 1e-9           # one field, one event
    msg = _diff(ref, fast, 'probe')
    assert msg and 'pick_travel_x' in msg and 'index 2' in msg, msg
    fast.pop()
    assert 'COUNT differs' in _diff(ref, fast, 'probe')


def test_the_existing_guards_cannot_see_a_reshaped_stream():
    """The justification for this file, as a test rather than a claim.

    Redistribute one loop's `pick_travel_x` across its events — pool it and park the whole
    pool on the last event that carried any — so every SUM is bit-identical by construction
    and only the per-event shape moves. That is what stopping a picker mid-task does to a
    stream.

    Measured on the many-aisles fixture: 18 of 62 events reshaped, and the travel breakdown,
    the travel axes, the makespan and the event count ALL still agree. Those are the four
    things the three existing lockstep tests check between them.
    """
    from Optimization.metrics.Simulation_Analytics import (
        task_travel_axes, task_travel_breakdown)

    cfg = _cfg(pickers=2, swap=7.0)
    ref = PickSimulation(_many_aisles(), cfg).run()
    fast = DeferredPickSimulation(_many_aisles(), cfg).run()
    assert _diff(ref, fast) is None, 'the loops already differ; the premise is gone'

    carriers = [e for e in fast if e.pick_travel_x]
    assert len(carriers) > 3, 'too few carriers to reshape; the fixture got smaller'
    pool = 0.0
    for e in carriers[:-1]:
        pool += e.pick_travel_x
        e.pick_travel_x = 0.0
    carriers[-1].pick_travel_x += pool

    # Every aggregate the existing guards compare is untouched.
    assert task_travel_breakdown(ref) == task_travel_breakdown(fast)
    assert task_travel_axes(ref) == task_travel_axes(fast)
    assert max(e.time for e in ref) == max(e.time for e in fast)
    assert len(ref) == len(fast)

    # This one is not.
    msg = _diff(ref, fast, 'reshaped')
    assert msg is not None, 'the event-by-event comparison missed a reshaped stream'
    assert 'pick_travel_x' in msg
