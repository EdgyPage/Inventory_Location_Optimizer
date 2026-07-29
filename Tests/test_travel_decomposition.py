"""Travel decomposition (B1): the sim stamps each second of travel into pick_travel /
non_pick_travel / cart_move, split by axis, so the split reconciles with task duration and
matches between the reference (Pick) and production (fast_pick) sims.  Two-way lanes, so
totals are unchanged from before the decomposition existed."""
import types

from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.picking.fast_pick import DeferredPickSimulation
from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Warehouse.picking.Workload_Builder import Task
from Warehouse.kernel.cost_model import sec_per_inch
from Optimization.metrics.Simulation_Analytics import (
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


def _bin_a(x, sku, aisle_width, vol=100):
    """A bin that knows its aisle's far-end width (for the one-way exit)."""
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=0.0, location=(1, x, 0),
        aisle=types.SimpleNamespace(aisle_width=aisle_width),
        storage=types.SimpleNamespace(order=_order(sku, vol), quantity=5))


def test_one_way_total_x_per_visit_equals_aisle_width():
    """Under one-way lanes, x-travel per aisle visit = the aisle WIDTH (a per-aisle constant,
    independent of which columns are picked): entry 50 + inter-pick 100 + exit (300-150) = 300."""
    xp = sec_per_inch(4.0)
    L = 300
    task = Task(1, [_bin_a(50, 1, L), _bin_a(150, 2, L)], {1: 1, 2: 1})
    cfg = _cfg(); cfg.one_way = True
    events = PickSimulation([task], cfg).run()
    tx, ty = task_travel_axes(events)
    assert abs(tx - L * xp) < 1e-9, (tx, L * xp)        # total x == aisle width
    pick, nonpick, cart = task_travel_breakdown(events)
    assert abs(pick - 100 * xp) < 1e-9                   # inter-pick sweep 50→150
    assert abs(nonpick - (50 + 150) * xp) < 1e-9         # entry 50 + exit 150


def test_two_way_has_no_exit_segment():
    """Two-way (default) charges no exit: x-travel stops at the deepest pick (150), not aisle end."""
    xp = sec_per_inch(4.0)
    task = Task(1, [_bin_a(50, 1, 300), _bin_a(150, 2, 300)], {1: 1, 2: 1})
    events = PickSimulation([task], _cfg()).run()          # one_way defaults False
    tx, _ = task_travel_axes(events)
    assert abs(tx - 150 * xp) < 1e-9                       # 0→150 monotone, no exit to 300


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
