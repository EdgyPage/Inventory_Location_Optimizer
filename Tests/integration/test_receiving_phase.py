"""test_receiving_phase.py — a receiving crew, its day, and what it leaves on the floor.

The user's decision: inbound is its OWN crew. So merchandise no longer appears in a put queue
the instant its lead time elapses — it lands on a dock, and a crew with its own hours works
through it. What they do not reach stays for tomorrow.

The design turns on ONE choice, and most of this file is about its consequences: **the dock
intercepts inside `_admit`, after the stamp and before the put queue.**

  * After the stamp, so a pallet that waited three batches on the dock is three batches old
    when it finally gets floor space. Stamping at unload would make the longest-waiting
    merchandise the youngest thing in the warehouse.
  * Inside `_admit` rather than one level up in `_release_to_stock`, because that function
    credits `_queued_qty` AFTER its admit loop. The credit therefore survives the divert, and
    `position = on_hand + queued + deferred` is unchanged with no edit to either of the two
    places that compute it. Intercepting upstream would take merchandise out of
    `_deferred_qty` without putting it into `_queued_qty`, and the SKU would re-order every
    batch for as long as the dock was backed up — with nothing raising, because more orders
    look perfectly legitimate.

That last one is the most expensive defect this feature could have had, so it is tested
directly (`test_a_backed_up_dock_does_not_make_a_sku_reorder_forever`) rather than left to
follow from the design.

Run:  python -m pytest Tests/integration/test_receiving_phase.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from Inbound.dock import Dock, DockSpec
from Warehouse.layout.Storage_Primitive import viable_storage_units
from Inbound.unload import UnloadCost, unload_cost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


def _assets(size=1, n_skus=200):
    a = cs.build_assets(n_skus=n_skus, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=11, coverage=2.0, safety=0.4)
    a.mgr.enable_receiving(Dock(DockSpec(size=size)))
    return a


def _arrive(a, n_orders=30, qty=4):
    """Push real reorder arrivals through the real release path. Returns items admitted."""
    mgr = a.mgr
    n = 0
    for order in a.inventory.orders[:n_orders]:
        for unit in viable_storage_units(order.reorder(), qty):
            mgr._admit(unit, 'reorder')
            n += 1
    return n


def _queued(mgr):
    return sum(len(q.items) for q in mgr.put_queues) + len(mgr._held)


# ── 1. no crew is no change ───────────────────────────────────────────────────────

def test_without_a_crew_nothing_is_constructed_and_nothing_diverts():
    """The no-op is STRUCTURAL: no dock object exists, so there is nothing to be empty."""
    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=11, coverage=2.0, safety=0.4)
    assert a.mgr._dock is None
    assert a.mgr.dock_depth == 0
    assert a.mgr.receiving_seconds == 0.0
    assert a.mgr.drain_receiving_records() == []
    assert a.mgr.receiving_snapshot() == (0, 0, 0, 0.0)

    before = _queued(a.mgr)
    n = _arrive(a)
    assert _queued(a.mgr) == before + n, 'an admission went somewhere other than the queue'


def test_the_dock_only_intercepts_the_sources_it_declares():
    """Initial stocking is not a receipt and a re-slotted unit never left the building. If
    the dock took `'intake'`, building a warehouse would put its whole catalogue on a
    trailer."""
    a = _assets()
    d = a.mgr._dock
    assert (d.takes('reorder'), d.takes('intake'), d.takes('reslot')) == (True, False, False)

    order = a.inventory.orders[0]
    for unit in viable_storage_units(order.reorder(), 4):
        a.mgr._admit(unit, 'intake')
    assert a.mgr.dock_depth == 0, 'intake was diverted to the dock'


# ── 2. arrivals land on the dock, not in a bin ────────────────────────────────────

def test_a_reorder_arrival_lands_on_the_dock():
    a = _assets()
    n = _arrive(a)
    assert n > 0
    assert a.mgr.dock_depth == n
    assert _queued(a.mgr) == 0, 'merchandise reached a put queue without being unloaded'


def test_the_dock_depth_and_the_queue_depth_are_disjoint():
    """Both are counted in storage units and a reader sums them for the whole unbinned
    backlog. If they overlapped, that sum would double-count."""
    a = _assets()
    n = _arrive(a)
    assert a.mgr.dock_depth + _queued(a.mgr) == n
    a.mgr._receive(deadline=None)
    assert a.mgr.dock_depth == 0
    assert a.mgr.dock_depth + _queued(a.mgr) == n


# ── 3. the ledger, which is the expensive one ─────────────────────────────────────

def test_merchandise_on_the_dock_is_still_on_order():
    """`_release_to_stock` credits `_queued_qty` after its admit loop, so the divert keeps
    the credit. This is what makes the reorder ledger need no edit at all."""
    a = _assets()
    mgr = a.mgr
    o = a.inventory.orders[0]
    mgr._originals[o.sku] = o
    mgr._queued_qty.pop(o.sku, None)

    plans = mgr._release_to_stock(o.sku, 40)
    assert plans and mgr.dock_depth > 0, 'nothing reached the dock'
    assert mgr._queued_qty.get(o.sku, 0) == 40, (
        'merchandise on the dock left the position ledger — the SKU will re-order every '
        'batch for as long as the dock is backed up, and nothing will raise')


def test_a_backed_up_dock_does_not_make_a_sku_reorder_forever():
    """THE defect this design exists to avoid, driven through the real reorder machinery.

    A dock with a zero-length day never unloads. Across several batches the SKU's inventory
    position must still count the merchandise standing there, so `_fire_reorders` does not
    order it again and again.
    """
    a = _assets()
    mgr = a.mgr
    o = a.inventory.orders[0]
    mgr._originals[o.sku] = o
    mgr._current_quantities[o.sku] = 0
    mgr._depleted_skus.add(o.sku)

    ordered = []
    for _ in range(4):
        mgr.check_reorders(recv_deadline=0.0)      # the crew never starts
        ordered.append(mgr.units_ordered)
        mgr._depleted_skus.add(o.sku)              # keep asking, as a depleted SKU would
        mgr.drain_receiving_records()

    assert mgr.dock_depth > 0, 'nothing is backed up; the test proves nothing'
    assert sum(ordered[1:]) == 0, (
        f'the SKU kept re-ordering while its merchandise sat on the dock: {ordered}')


# ── 4. the crew's day ─────────────────────────────────────────────────────────────

def test_a_zero_length_day_unloads_nothing_and_loses_nothing():
    a = _assets()
    n = _arrive(a)
    a.mgr._receive(deadline=0.0)
    assert a.mgr.dock_depth == n
    assert a.mgr.receiving_seconds == 0.0
    depth, unloaded, cut, seconds = a.mgr.receiving_snapshot()
    assert (depth, unloaded, cut, seconds) == (n, 0, n, 0.0)


def test_what_the_whistle_stops_is_unloaded_the_next_day():
    """ROLLOVER — the point of the feature."""
    a = _assets()
    n = _arrive(a)
    a.mgr._receive(deadline=0.0)
    stopped = a.mgr.dock_depth
    assert stopped == n

    a.mgr.drain_receiving_records()                # a drain is a batch boundary
    a.mgr._receive(deadline=None)
    assert a.mgr.dock_depth == 0, 'the second day did not clear what the first left'
    # Every item is now in a put queue or held by a full one -- and nowhere else. Stated as
    # an equality rather than `>=` so a unit that vanished between the two would fail here
    # instead of being absorbed by an inequality.
    assert _queued(a.mgr) == stopped


def test_an_unload_may_run_past_the_whistle_once():
    """A START gate, like put-away's: the unload in progress when it blows finishes, so
    overtime is bounded by one unload per worker. A completion gate would need the duration
    before choosing to start, which is the wrong shape for work that is already committed
    the moment someone lifts a pallet."""
    a = _assets(size=1)
    _arrive(a)
    a.mgr._receive(deadline=1e-9)
    assert a.mgr._dock.unloaded == 1
    assert max(a.mgr._dock.clocks) > 1e-9, 'this is a completion gate, not a start gate'


def test_a_bigger_crew_clears_more_of_the_same_day():
    """The only thing crew size means here — size is the number of clocks."""
    done = {}
    for size in (1, 4):
        a = _assets(size=size)
        _arrive(a)
        a.mgr._receive(deadline=20.0)
        done[size] = a.mgr._dock.unloaded
    assert done[4] > done[1], f'crew size changed nothing: {done}'


def test_the_rollover_is_fifo():
    """The deque is in arrival order, so tomorrow's crew starts with the oldest thing on the
    floor. Checked on the arrival STAMP, which is what `put_queue_state.oldest_age` reports
    and what the whole age discipline exists to keep truthful."""
    a = _assets()
    _arrive(a)
    ages = [it.age for it in a.mgr._dock.items]
    assert ages == sorted(ages), 'the dock is not in arrival order'

    a.mgr._receive(deadline=5.0)
    left = [it.age for it in a.mgr._dock.items]
    gone = ages[:len(ages) - len(left)]
    assert left, 'the whole dock cleared; there is no rollover to check'
    assert gone, 'nothing was unloaded; there is no ordering to check'
    assert left == sorted(left), 'what is left is no longer in arrival order'
    assert max(gone) < min(left), (
        'a younger item was unloaded before an older one — the rollover is not FIFO, and '
        'the merchandise that has waited longest would keep being overtaken')


def test_the_age_stamp_is_the_arrival_not_the_unload():
    """A pallet that waits on the dock must arrive in the put queue OLD. Stamping at unload
    would turn the backlog into a priority inversion — the newest merchandise would look like
    the most urgent."""
    a = _assets()
    _arrive(a, n_orders=6)
    first_age = a.mgr._dock.items[0].age

    for _ in range(3):                              # three batches of waiting
        a.mgr._receive(deadline=0.0)
        a.mgr.drain_receiving_records()

    a.mgr._receive(deadline=None)
    queued_ages = [it.age for q in a.mgr.put_queues for it in q.items]
    assert queued_ages, 'nothing was unloaded'
    assert min(queued_ages) == first_age, 're-stamped on unload'


# ── 5. the counters ───────────────────────────────────────────────────────────────

def test_the_snapshot_resets_flows_and_keeps_the_level():
    a = _assets()
    n = _arrive(a)
    a.mgr._receive(deadline=None)
    depth, unloaded, cut, seconds = a.mgr.receiving_snapshot()
    assert (depth, unloaded, cut) == (0, n, 0)
    assert seconds > 0.0

    depth2, unloaded2, cut2, seconds2 = a.mgr.receiving_snapshot()
    assert (unloaded2, cut2, seconds2) == (0, 0, 0.0), 'a flow survived the snapshot'
    assert depth2 == depth, 'depth is a LEVEL and must not reset'


def test_receiving_seconds_are_not_folded_into_putaway_seconds():
    """`putaway_seconds` has been published. Widening what it counts would move an existing
    figure with nothing to point at."""
    from Warehouse.kernel.cost_model import SpeedProfile
    a = _assets()
    a.mgr.enable_putaway_timing(SpeedProfile(2.0, 4.0), size=1)
    put_before = a.mgr.putaway_seconds
    _arrive(a)
    a.mgr._receive(deadline=None)
    assert a.mgr.receiving_seconds > 0.0
    assert a.mgr.putaway_seconds == put_before, 'receiving leaked into the put-away total'


def test_the_records_carry_batch_local_times_and_drain():
    """The runner adds the batch epoch, because receiving happens at the TOP of a batch,
    before the pickers have run and before `batch_start_time` exists. A record that stamped
    an absolute time here would put the whole run on batch 0's axis."""
    a = _assets()
    _arrive(a)
    a.mgr._receive(deadline=None)
    recs = a.mgr.drain_receiving_records()
    assert recs, 'no records'
    assert recs[0][0] == 0.0, 'the first unload did not start at the batch-local zero'
    assert all(len(r) == 5 for r in recs)
    assert a.mgr._dock.clocks == [0.0] * a.mgr._dock.crew_size, 'the clock was not reset'
    assert a.mgr.drain_receiving_records() == []


def test_the_seconds_match_the_cost_model():
    """The records are the cost model's own output, not a second computation of it."""
    a = _assets()
    _arrive(a, n_orders=3)
    a.mgr._receive(deadline=None)
    recs = a.mgr.drain_receiving_records()
    want = sum(r[1] for r in recs)
    assert a.mgr.receiving_seconds == pytest.approx(want)


def test_an_unload_is_a_put_at_the_origin_with_no_height():
    """Inverts the trap into a claim: reusing `put_cost` with the dock at (0,0) would return
    ALMOST this number, which is why it looks like it works. The difference is that this
    function cannot silently acquire a travel term or a height bracket later — and, since
    ADR-0001, that it charges the per-item term once per PACK where a put charges it per
    unit: the two differ by exactly (quantity - 1) charges, and by nothing else."""
    from Warehouse.kernel.cost_model import SpeedProfile
    from Warehouse.operations.putaway import PutawayCost, put_cost
    put = PutawayCost()
    got = unload_cost(2.0, 800.0, 4, UnloadCost())
    same = put_cost(0.0, 0.0, 2.0, 800.0, 4, SpeedProfile(2.0, 4.0), put)
    assert same - got == pytest.approx((4 - 1) * put.per_item)
    assert unload_cost(2.0, 800.0, 1, UnloadCost()) == pytest.approx(
        put_cost(0.0, 0.0, 2.0, 800.0, 1, SpeedProfile(2.0, 4.0), put))
