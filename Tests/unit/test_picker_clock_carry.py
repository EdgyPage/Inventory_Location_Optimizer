"""test_picker_clock_carry.py — a picker's clock does not have to restart at every batch.

Today's model asserts that every picker re-synchronises at every batch boundary: each one's
clock is reborn at `0.0`, so batch 7's picker 3 and batch 0's picker 3 both start at zero and
`t` alone cannot order two events from different batches. That is a fiction — nothing in the
warehouse resets at a batch boundary — and it is the reason there is no run-level timeline.

`start_times` removes it. `start_times[p]` is picker `p`'s clock when this batch begins;
absent (every construction that does not track clocks) it is `0.0`, which is the pre-clock
model **exactly**. That default is what makes the change provable: the byte-identity harness
still reproduces every published number.

Both simulations take it, because they are kept byte-for-byte in lockstep and a clock in one
but not the other is precisely how that lockstep rots.

Run:  python -m pytest Tests/unit/test_picker_clock_carry.py -q
"""
from __future__ import annotations

import inspect
import types

import pytest

from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.picking.Workload_Builder import Task
from Warehouse.picking.fast_pick import DeferredPickSimulation


# ── fixture, in the local SimpleNamespace style the other lockstep tests use ──────

def _order(sku, vol=100):
    return types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol)


def _bin(x, y, sku, qty=5):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(1, x, y),
        storage=types.SimpleNamespace(order=_order(sku), quantity=qty))


@pytest.fixture
def tasks_2_aisles():
    """A FACTORY, not a fixed list: two aisles of unequal work, rebuilt on every call.

    Each test runs several simulations, and a simulation DEPLETES the bins it picks from.
    Sharing one set of bins meant the second, third and fourth run saw a progressively
    emptier warehouse. That was invisible while the picker loops read `task.items[sku]`
    and ignored bin stock entirely; once a pick is capped at what the bin holds, the runs
    diverge — which is the bug this fixture was accidentally relying on.
    """
    def build():
        return [Task(1, [_bin(50, 0, 1), _bin(150, 0, 2)], {1: 1, 2: 1}),
                Task(2, [_bin(30, 12, 3)], {3: 2})]
    return build


def _cfg(n=2, scheduler='round_robin'):
    return PickConfig(num_pickers=n, x_speed=3.0, y_speed=2.0, scheduler=scheduler)


def _both(build, cfg, start_times=None):
    """The same batch through both sims, each on its OWN fresh bins."""
    a = PickSimulation(build(), cfg, start_times=start_times).run()
    b = DeferredPickSimulation(build(), cfg, start_times=start_times).run()
    return a, b


def _times(events, kind='done'):
    return sorted(e.time for e in events if e.event_type == kind)


# ── the default is today's model, exactly ─────────────────────────────────────────

def test_omitting_start_times_starts_every_picker_at_zero(tasks_2_aisles):
    cfg = _cfg()
    a, _b = _both(tasks_2_aisles, cfg)
    firsts = {}
    for e in a:
        firsts.setdefault(e.picker_id, e.time)
    assert set(firsts.values()) == {0.0}


def test_an_all_zero_start_is_the_same_as_no_start(tasks_2_aisles):
    cfg = _cfg()
    a, _ = _both(tasks_2_aisles, cfg)
    b, _ = _both(tasks_2_aisles, cfg, start_times=[0.0, 0.0])
    assert [(e.event_type, e.time, e.picker_id) for e in a] == \
           [(e.event_type, e.time, e.picker_id) for e in b]


# ── a carried clock shifts that picker and nothing else ───────────────────────────

def test_a_started_picker_is_shifted_by_exactly_its_own_offset(tasks_2_aisles):
    cfg = _cfg()
    base, _ = _both(tasks_2_aisles, cfg)
    moved, _ = _both(tasks_2_aisles, cfg, start_times=[100.0, 0.0])

    by_id_base = {}
    by_id_moved = {}
    for e in base:
        by_id_base.setdefault(e.picker_id, []).append(e.time)
    for e in moved:
        by_id_moved.setdefault(e.picker_id, []).append(e.time)

    assert by_id_moved[0] == pytest.approx([t + 100.0 for t in by_id_base[0]])
    assert by_id_moved[1] == pytest.approx(by_id_base[1])          # untouched


def test_the_work_done_is_unchanged_by_when_it_starts(tasks_2_aisles):
    """A shifted clock must not change what was picked — only when."""
    cfg = _cfg()
    base, _ = _both(tasks_2_aisles, cfg)
    moved, _ = _both(tasks_2_aisles, cfg, start_times=[100.0, 250.0])
    key = lambda evs: sorted((e.event_type, e.picker_id, e.sku, e.quantity) for e in evs)
    assert key(base) == key(moved)


# ── the two sims stay in lockstep under a carried clock ───────────────────────────

@pytest.mark.parametrize('start_times', [None, [0.0, 0.0], [100.0, 0.0], [7.5, 91.25]])
@pytest.mark.parametrize('scheduler', ['round_robin', 'lpt'])
def test_both_simulations_agree(tasks_2_aisles, start_times, scheduler):
    cfg = _cfg(scheduler=scheduler)
    a, b = _both(tasks_2_aisles, cfg, start_times=start_times)
    assert _times(a) == pytest.approx(_times(b))
    assert len(a) == len(b)


# ── the tolerant edges ────────────────────────────────────────────────────────────

def test_a_short_start_list_reads_zero_past_its_end(tasks_2_aisles):
    """A crew can grow between batches; a new picker starting at the origin is honest."""
    cfg = _cfg()
    a, _ = _both(tasks_2_aisles, cfg, start_times=[50.0])
    firsts = {}
    for e in a:
        firsts.setdefault(e.picker_id, e.time)
    assert firsts[0] == pytest.approx(50.0)
    assert firsts[1] == pytest.approx(0.0)


def test_an_empty_start_list_is_treated_as_absent(tasks_2_aisles):
    cfg = _cfg()
    a, _ = _both(tasks_2_aisles, cfg, start_times=[])
    assert min(e.time for e in a) == 0.0


# ── the accessor is shared, not duplicated ────────────────────────────────────────

def test_both_simulations_use_the_same_start_accessor():
    """`_start_at` lives on the mixin both inherit; two copies of a clock rule is how the
    two loops drift apart."""
    from Warehouse.picking.Pick import _ProgressAPIMixin
    assert hasattr(_ProgressAPIMixin, '_start_at')
    for cls in (PickSimulation, DeferredPickSimulation):
        assert cls._start_at is _ProgressAPIMixin._start_at


def test_neither_loop_hardcodes_a_zero_start():
    for fn in (PickSimulation._simulate_picker,):
        src = inspect.getsource(fn)
        assert 'time: float = t0' in src
    from Warehouse.picking import fast_pick
    src = inspect.getsource(fast_pick._simulate_picker_deferred)
    assert 't              = t0' in src
