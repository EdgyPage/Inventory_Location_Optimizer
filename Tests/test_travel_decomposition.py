"""Travel decomposition (B1): the sim stamps each second of travel into pick_travel /
non_pick_travel / cart_move, split by axis, so the split reconciles with task duration and
matches between the reference (Pick) and production (fast_pick) sims.  Two-way lanes, so
totals are unchanged from before the decomposition existed."""
import types

from Warehouse.Pick import PickConfig, PickSimulation
from Warehouse.fast_pick import DeferredPickSimulation
from Warehouse.Storage_Primitive import FulfillmentCart
from Warehouse.Workload_Builder import Task
from Warehouse.cost_model import sec_per_inch
from Optimization.Simulation_Analytics import (
    task_time_breakdown, task_travel_breakdown, task_travel_axes)


def _order(sku, vol):
    return types.SimpleNamespace(sku=sku, weight=10, volume=lambda: vol)


def _bin(x, y, sku, vol=100, qty=5):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(1, x, y),
        storage=types.SimpleNamespace(order=_order(sku, vol), quantity=qty))


def _two_pick_task(vol=100):
    """One aisle, first pick at (50,0), second at (150,0) → non-zero entry AND inter-pick."""
    return Task(1, [_bin(50, 0, 1, vol), _bin(150, 0, 2, vol)], {1: 1, 2: 1})


def _cfg(cart_swap_coef=0.0, cart=FulfillmentCart):
    return PickConfig(num_pickers=1, x_speed=4.0, y_speed=2.0,
                      pick_intercept=5.0, pick_weight_coef=0.2, pick_volume_coef=0.001,
                      cart_swap_coef=cart_swap_coef, cart=cart)


def test_entry_vs_interpick_classification():
    """Travel to the FIRST pick is aisle ENTRY (non_pick); travel between picks is INTER-PICK
    (pick).  Two picks at x=50 then x=150 → entry=50*x_pace, inter-pick=100*x_pace, no y."""
    xp = sec_per_inch(4.0)
    events = PickSimulation([_two_pick_task()], _cfg()).run()
    pick, nonpick, cart = task_travel_breakdown(events)
    assert cart == 0.0
    assert abs(nonpick - 50 * xp) < 1e-9   # entry to the first pick
    assert abs(pick - 100 * xp) < 1e-9     # sweep to the second pick
    tx, ty = task_travel_axes(events)
    assert abs(tx - 150 * xp) < 1e-9 and ty == 0.0   # all travel is on x here


def test_decomposition_reconciles_with_duration():
    """pick + non_pick + cart_move + handling == task duration (every second is accounted for),
    and pick+non_pick+cart equals the travel bucket of the gap-based task_time_breakdown."""
    events = PickSimulation([_two_pick_task(vol=15_000)], _cfg(cart_swap_coef=50.0)).run()
    pick, nonpick, cart = task_travel_breakdown(events)
    travel, handling, other = task_time_breakdown(events)
    assert cart == 50.0                                   # one swap (2nd pick overflows the cart)
    assert abs((pick + nonpick + cart) - travel) < 1e-9   # explicit split == gap-based travel
    duration = max(e.time for e in events)
    assert abs((pick + nonpick + cart + handling + other) - duration) < 1e-9


def test_pick_fastpick_decomposition_lockstep():
    """The production sim (fast_pick) stamps the identical decomposition as the reference sim."""
    ref = PickSimulation([_two_pick_task(vol=15_000)], _cfg(cart_swap_coef=7.0)).run()
    fast = DeferredPickSimulation([_two_pick_task(vol=15_000)], _cfg(cart_swap_coef=7.0)).run()
    assert task_travel_breakdown(ref) == task_travel_breakdown(fast)
    assert task_travel_axes(ref) == task_travel_axes(fast)
    assert abs(max(e.time for e in ref) - max(e.time for e in fast)) < 1e-9


def test_per_task_reset_measures_entry_from_entrance():
    """Per-task reset (B2): each aisle visit starts at the entrance, so task 2's entry is measured
    from (0,0) — NOT carried over from task 1's last position (the old cross-aisle artifact).
    Task 1 pick at x=100, task 2 pick at x=30 → entry = 100 + 30 = 130*x_pace (reset), not
    100 + |30-100| = 170*x_pace (artifact)."""
    xp = sec_per_inch(4.0)
    t1 = Task(1, [_bin(100, 0, 1)], {1: 1})
    t2 = Task(2, [_bin(30, 0, 2)], {2: 1})
    events = PickSimulation([t1, t2], _cfg()).run()
    pick, nonpick, cart = task_travel_breakdown(events)
    assert pick == 0.0 and cart == 0.0          # one pick per task → no inter-pick, no swap
    assert abs(nonpick - 130 * xp) < 1e-9        # 130 (reset) not 170 (artifact)
