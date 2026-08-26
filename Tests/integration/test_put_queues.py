"""test_put_queues.py — one queue object, configured N times.

The store floor has at least two put-away streams that share nothing operationally: loose
singletons a person walks into a cart, and pallets that need a forklift. Fulfillment has a
third. Three classes would encode today's three streams as the shape of the code, so what
exists instead is one `PutQueue` and a `PutQueueSpec` carrying the axes that actually differ
— who works it, how much can be staged, and how much ordering freedom the assignment policy
gets.

The claim that makes the refactor safe is the first section here: ONE queue accepting
everything is the manager as it behaved before queues existed. Everything after that is
about the split configuration doing what it says.

Run:  python -m pytest Tests/integration/test_put_queues.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from Warehouse.inventory.put_queue import (
    ANY, FULFILLMENT, PALLET, SINGLETON, PutQueue, PutQueueSet, PutQueueSpec,
    single_queue, store_and_fulfillment,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


class _U:
    __slots__ = ('unit_category',)

    def __init__(self, cat):
        self.unit_category = cat


def _item(age=0, source='intake'):
    from Warehouse.inventory.inventory_common import PutawayItem
    return PutawayItem(object(), source, age)


# ── the default is the old manager ────────────────────────────────────────────────

def test_the_default_is_one_queue_that_takes_everything():
    qs = single_queue()
    assert qs.is_single and len(qs) == 1
    for cat in (PALLET, SINGLETON, FULFILLMENT, 'something_invented_later', None):
        assert qs.route(_U(cat)) is qs.queues[0]


def test_a_fresh_manager_has_the_default_queue_set():
    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_rank_labor_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    assert a.mgr.put_queues.is_single


def test_stock_queue_is_still_the_live_deque_under_one_queue():
    """Every existing consumer, mutation site and test reads `_stock_queue`. With one queue
    it must be THE deque, not a copy — a copy would swallow the drain's own writes."""
    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    assert a.mgr._stock_queue is a.mgr.put_queues.queues[0].items


# ── routing ───────────────────────────────────────────────────────────────────────

def test_the_three_stream_default_routes_by_category():
    qs = store_and_fulfillment()
    assert qs.route(_U(SINGLETON)).name == 'store_cart'
    assert qs.route(_U(PALLET)).name == 'store_pallet'
    assert qs.route(_U(FULFILLMENT)).name == 'fulfillment'
    assert not qs.is_single


def test_a_category_no_queue_accepts_raises_rather_than_vanishing():
    """A silent drop would take the unit out of the conservation ledger with nothing to
    point at — the failure mode that costs a day to find."""
    qs = store_and_fulfillment()
    with pytest.raises(LookupError, match='no put-away queue accepts'):
        qs.route(_U('trailer_load'))


def test_first_match_wins_so_spec_order_is_precedence():
    qs = PutQueueSet([PutQueueSpec('special', accepts=(PALLET,)),
                      PutQueueSpec('rest', accepts=ANY)])
    assert qs.route(_U(PALLET)).name == 'special'
    assert qs.route(_U(SINGLETON)).name == 'rest'


# ── the spec refuses incoherent configurations ────────────────────────────────────

@pytest.mark.parametrize('kw,msg', [
    ({'name': ''}, 'needs a name'),
    ({'accepts': ()}, 'nothing can ever enter'),
    ({'k_cap': 0}, 'k_cap'),
    ({'staging': 0}, 'staging'),
])
def test_an_incoherent_spec_is_refused(kw, msg):
    base = {'name': 'q', 'accepts': (PALLET,)}
    base.update(kw)
    with pytest.raises(ValueError, match=msg):
        PutQueueSpec(**base)


def test_duplicate_queue_names_are_refused():
    """The name is the DB discriminator; two rows claiming to be the same queue would make
    every per-queue number ambiguous."""
    with pytest.raises(ValueError, match='duplicate'):
        PutQueueSet([PutQueueSpec('a', accepts=(PALLET,)),
                     PutQueueSpec('a', accepts=(SINGLETON,))])


def test_a_queue_set_needs_at_least_one_queue():
    with pytest.raises(ValueError, match='at least one'):
        PutQueueSet([])


# ── staging refuses rather than drops ─────────────────────────────────────────────

def test_a_full_queue_refuses_and_counts_the_refusal():
    """Backpressure has to be visible. A refused admission leaves no trace anywhere else,
    so if `blocked` did not count it the whole mechanism would be invisible."""
    q = PutQueue(PutQueueSpec('tight', accepts=ANY, staging=2))
    assert q.admit(_item(0)) and q.admit(_item(1))
    assert not q.admit(_item(2))
    assert len(q) == 2 and q.blocked == 1 and q.admitted == 2


def test_an_unbounded_queue_never_refuses():
    q = PutQueue(PutQueueSpec('open', accepts=ANY))
    assert all(q.admit(_item(i)) for i in range(500))
    assert q.blocked == 0 and len(q) == 500


def test_room_reappears_as_the_queue_drains():
    q = PutQueue(PutQueueSpec('tight', accepts=ANY, staging=1))
    assert q.admit(_item(0)) and not q.admit(_item(1))
    q.items.popleft()
    assert q.has_room and q.admit(_item(2))


# ── what a snapshot reports ───────────────────────────────────────────────────────

def test_a_snapshot_reports_levels_and_flows_and_resets_only_the_flows():
    """Depth and oldest age are LEVELS — reading them must not zero them. The counters are
    flows and must reset, or the next batch double-counts this one."""
    q = PutQueue(PutQueueSpec('q', accepts=ANY, staging=3))
    for age in (5, 6, 7):
        q.admit(_item(age))
    q.admit(_item(8))                                    # refused
    q.placed = 2
    snap = q.drain_counters()
    assert snap == {'queue': 'q', 'depth': 3, 'oldest_age': 5, 'staging': 3,
                    'admitted': 3, 'placed': 2, 'blocked': 1, 'cart_swaps': 0, 'cut': 0}
    again = q.drain_counters()
    assert again['admitted'] == again['placed'] == again['blocked'] == 0
    assert again['cart_swaps'] == 0
    assert again['depth'] == 3 and again['oldest_age'] == 5


def test_oldest_age_is_none_on_an_empty_queue():
    assert PutQueue(PutQueueSpec('q', accepts=ANY)).oldest_age is None


# ── the split configuration on a real manager ─────────────────────────────────────

def test_a_split_manager_places_everything_it_is_given():
    """The end-to-end claim: swapping in three queues does not lose a unit. Conservation is
    the property that a routing bug breaks first and most quietly."""
    a = cs.build_assets(n_skus=400, bins_per_aisle=30, strategy='uni_rank_labor_norsl',
                        seed=42, coverage=2.0, safety=0.4)
    mgr = a.mgr
    mgr.put_queues = store_and_fulfillment()
    assert not mgr.put_queues.is_single

    placed = []
    orig = mgr._execute_placement

    def cap(unit, bin_, *, source=None, score=None, score_rank=None, policy=None, _o=orig):
        placed.append(unit)
        _o(unit, bin_, source=source)
    mgr._execute_placement = cap

    # `placed` is a FLOW that `queue_state_rows` drains, and the meso loop now snapshots
    # every batch exactly as the runner does -- so reading `q.placed` at the end returns
    # only whatever the LAST batch left. Accumulate the snapshots instead, the way
    # `test_putaway_backpressure` already does for `blocked`. Wrapped on the MANAGER
    # because `PutQueueSet` is slotted and its methods cannot be patched.
    per_queue: dict = {}
    orig_rows = mgr.queue_state_rows

    def cap_rows(batch_id, _o=orig_rows, _t=per_queue):
        rows = _o(batch_id)
        for r in rows:
            _t[r['queue']] = _t.get(r['queue'], 0) + r['placed']
        return rows
    mgr.queue_state_rows = cap_rows

    cs.run_meso(a, n_batches=5, seed=42)
    # ...plus what the final batch left undrained: the loop snapshots at the TOP of a
    # batch, so the last batch's placements have not been swept into a row yet.
    for q in mgr.put_queues:
        per_queue[q.name] = per_queue.get(q.name, 0) + q.placed

    assert len(placed) > 100, f'only {len(placed)} placements — nothing was exercised'
    assert sum(per_queue.values()) == len(placed), (per_queue, len(placed))
    used = {n for n, c in per_queue.items() if c}
    assert len(used) >= 2, (
        f'only {used} saw any work, so routing was never exercised: {per_queue}')
    # Store-only catalogue, so `fulfillment` legitimately sees nothing and the split is
    # cart-vs-pallet. Measured: store_pallet 1307, store_cart 1, fulfillment 0 — singletons
    # reach the queue only through the repack rescue at this scale.
    assert per_queue['fulfillment'] == 0, 'a store catalogue produced fulfillment units'
    assert per_queue['store_pallet'] > per_queue['store_cart']


def test_the_pallet_queue_is_strict_fifo_by_construction():
    """`k_cap=1` on pallets is the modelling claim, not a tuning knob: with almost no floor
    to lay them out on, pallets go away in arrival order whatever the policy would prefer."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    qs = store_and_fulfillment()
    mgr = type('M', (), {'putaway_window': None})()
    assert Inventory_Manager._window_for(mgr, qs['store_pallet']) == 1
    assert Inventory_Manager._window_for(mgr, qs['store_cart']) is None
    assert Inventory_Manager._window_for(mgr, qs['fulfillment']) is None


def test_the_multi_queue_view_is_age_ordered_and_read_only():
    """`_stock_queue` has three external consumers that iterate and measure it. With the
    streams split there is no one deque, and returning a merged COPY would let a caller
    mutate it and lose the write in silence."""
    a = cs.build_assets(n_skus=200, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    mgr = a.mgr
    mgr.put_queues = store_and_fulfillment()
    for i, cat in enumerate([SINGLETON, PALLET, SINGLETON, FULFILLMENT, PALLET]):
        mgr.put_queues.route(_U(cat)).items.append(_item(age=10 - i, source='intake'))

    view = mgr._stock_queue
    assert len(view) == 5 and bool(view)
    assert [it.age for it in view] == sorted(it.age for it in view), 'view is not age-ordered'
    assert not hasattr(view, 'append'), 'the view is mutable — a write would be lost'
    with pytest.raises(RuntimeError, match='streams are split'):
        mgr._stock_queue = []
