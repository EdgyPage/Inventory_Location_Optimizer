"""test_putaway_budget.py — the drain can be capped, and capping loses nothing.

Both drains were `while self._stock_queue:` — the whole queue, every batch, unbounded.
That is fine while put-away is a zero-duration phase, and wrong the moment a finite crew
and a finite number of dock doors decide how much can actually be moved in a batch.

`budget=None` is the old behaviour and every caller today passes it, so this is a seam and
not a change. What has to be pinned is the part a no-op test cannot reach: that a budget
actually stops the drain, that nothing is dropped when it does, and that the RANKED path
defers a whole wave rather than truncating one.

That last one is the real content. `place_wave` scores a group at once and mutates the
manager's aisle running balances while deciding, so each unit's bin depends on where the
earlier units in the same wave went. Truncating a wave mid-way would place units under a
balance that assumed the rest landed too — so an unaffordable group must be requeued
having never been scored at all.

Run:  python -m pytest Tests/unit/test_putaway_budget.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import Placement
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig


def _order(sku: int, qty: int = 4) -> Order:
    """A minimal placeable order — no DB, no profile generation."""
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height, c.weight = 8, 8, 6, 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty = qty
    c.reorder_point = 1
    c.stock_plan = None
    return c


def _mgr(seed: int = 0) -> Inventory_Manager:
    Aisle.next_aisle_id = 1
    random.seed(seed)
    w, h = aisle_width_for(4), aisle_height_for(4)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Inventory_Manager(Warehouse_Builder().from_config(cfg).build())


def _fill_queue(mgr, n_orders=6, qty=4) -> int:
    """Queue units WITHOUT draining — `enqueue` calls `_stock` itself, so the queue is
    loaded directly, which is also what `_release_to_stock` does."""
    from Warehouse.inventory.inventory_common import PutawayItem
    from Warehouse.layout.Storage_Primitive import viable_storage_units
    n = 0
    for sku in range(1, n_orders + 1):
        for unit in viable_storage_units(_order(sku, qty), qty):
            mgr._stock_queue.append(PutawayItem(unit, 'reorder'))
            n += 1
    return n


def _placed(mgr) -> int:
    return sum(1 for b in mgr.unavailable if b.storage is not None)


# ── the default is the old behaviour ─────────────────────────────────────────────

def test_no_budget_drains_everything():
    mgr = _mgr()
    n = _fill_queue(mgr)
    assert n > 0
    mgr._stock()
    assert mgr.queue_depth == 0, 'the unbudgeted drain must still empty the queue'


def test_the_budget_defaults_to_none_at_every_entry_point():
    """A caller that predates the budget must not suddenly be capped."""
    import inspect
    for fn in (Inventory_Manager._stock,
               Inventory_Manager._stock_per_unit,
               Inventory_Manager._stock_ranked):
        p = inspect.signature(fn).parameters['budget']
        assert p.default is None, f'{fn.__name__} defaults its budget to {p.default!r}'


# ── a budget stops the drain, and drops nothing ──────────────────────────────────

@pytest.mark.parametrize('budget', [0, 1, 3])
def test_a_budget_places_at_most_that_many_and_queues_the_rest(budget):
    mgr = _mgr()
    n = _fill_queue(mgr)
    assert n > budget, 'the fixture must queue more than the budget or this proves nothing'
    mgr._stock(budget)
    placed = _placed(mgr)
    assert placed <= budget
    assert placed + mgr.queue_depth == n, (
        f'{n} queued, {placed} placed, {mgr.queue_depth} left — a unit went missing')


def test_a_zero_budget_places_nothing_and_keeps_everything():
    mgr = _mgr()
    n = _fill_queue(mgr)
    mgr._stock(0)
    assert _placed(mgr) == 0
    assert mgr.queue_depth == n


def test_a_budgeted_drain_resumes_where_it_stopped():
    """The deferral is the same one a unit gets when no bin fits it, so a later call must
    simply carry on — that is what makes a per-batch cap usable at all."""
    mgr = _mgr()
    n = _fill_queue(mgr)
    mgr._stock(2)
    first = _placed(mgr)
    mgr._stock()                       # unbudgeted: finish the job
    assert _placed(mgr) > first
    assert mgr.queue_depth == 0


def test_a_budget_larger_than_the_queue_is_the_same_as_none():
    a, b = _mgr(1), _mgr(1)
    na, nb = _fill_queue(a), _fill_queue(b)
    assert na == nb
    a._stock()
    b._stock(10_000)
    assert _placed(a) == _placed(b)
    assert a.queue_depth == b.queue_depth == 0


# ── the ranked path defers a WHOLE wave, unscored ────────────────────────────────

class _SpyWave:
    """A `place_wave` that records which groups it was asked to score."""

    def __init__(self):
        self.scored: list[int] = []

    def __call__(self, units, candidates_fn):
        self.scored.append(len(units))
        # place nothing: the point is WHICH groups get scored, not where they land
        return [(u, None) for u in units]


def test_an_unaffordable_wave_is_never_scored():
    """THE property. `place_wave` mutates aisle running balances as it decides, so scoring
    a group whose units are then not placed would move a balance for work that never
    happened."""
    mgr = _mgr()
    _fill_queue(mgr, n_orders=6)
    spy = _SpyWave()
    mgr.placement = Placement('spy_ranked', mgr.placement.place_one, spy)
    mgr._stock_ranked(budget=0)
    assert spy.scored == [], (
        'a wave was scored with no budget to place it — its aisle-balance mutations '
        'already happened for units that stayed in the queue')
    assert mgr.queue_depth > 0, 'and the units must still be there'


def test_the_ranked_budget_is_spent_per_wave_not_per_unit():
    """Documented as a contract, not an accident: a wave is the atom."""
    import inspect
    src = inspect.getsource(Inventory_Manager._stock_ranked)
    assert 'A wave is the atom.' in src
    # the guard must come BEFORE the scoring call, or the hazard above is live
    guard = src.index('placed >= budget')
    call = src.index('place_wave(')
    assert guard < call, 'the budget check runs after the wave is scored'


def test_a_ranked_drain_with_no_budget_still_reaches_the_per_unit_fallback():
    """The fallback is what keeps the ranked queue bounded; the budget arithmetic must not
    have cut it off.

    Whitespace-normalized before matching. The literal form broke the first time the call
    grew an argument and wrapped across two lines — a formatting change reported as "the
    ranked drain no longer hands its stragglers to the per-unit path", which is a false
    alarm of exactly the kind that teaches people to edit the assertion rather than read it.
    """
    import inspect
    import re
    src = re.sub(r'\s+', ' ', inspect.getsource(Inventory_Manager._stock_ranked))
    assert '_stock_per_unit(None if budget is None else max(0, budget - placed),' in src, \
        'the ranked drain no longer hands its stragglers to the per-unit path'


# ── a repack must not consume budget ─────────────────────────────────────────────

def test_the_budget_counts_placements_not_pops():
    """A repack splits one unit into several and pushes them back. Charging the budget for
    that would make the cap depend on how badly the warehouse is packed rather than on how
    much the crew can move."""
    import inspect
    src = inspect.getsource(Inventory_Manager._stock_per_unit)
    # `placed` is incremented only in the branch that actually commits a placement
    commit = src.index('self._execute_placement(unit, bin_, source=item.source)')
    bump = src.index('placed += 1')
    assert commit < bump < src.index('else:', commit), \
        'placed is counted somewhere other than immediately after a commit'
