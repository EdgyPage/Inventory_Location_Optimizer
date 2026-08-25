"""test_batch_shortfall.py — demand that could not be reached is now sayable.

`Task.from_batch` clamps every SKU to what is actually on the shelf (`take = min(remaining,
available)`) and then **throws the remainder away**: `remaining` is a loop-local, overwritten
on the next SKU, read nowhere. So an arm that placed nothing for a SKU and an arm that placed
500 of it produced indistinguishable records — and every rate in `frames.py` divides by
`total_items`, which is what was *picked*, so failing to pick looked cheap.

Two things here, and they are deliberately separate:

* `from_batch_with_shortfall` reports `{sku: units_short}`. A second entry point rather than
  a wider return type — `from_batch` has ~30 call sites and none of them want the extra
  value.
* `BatchStats.items_demanded` records what the batch asked for, so `demanded − picked` is
  answerable from a run's own DB.

**Pre-simulation.** This is demand no bin could satisfy, not demand a picker ran out of time
for. Those are different failures with different fixes, and only this one is knowable before
the sim runs.

Run:  python -m pytest Tests/unit/test_batch_shortfall.py -q
"""
from __future__ import annotations

import types

import pytest

from Warehouse.picking.Workload_Builder import Task


class _Order:
    def __init__(self, sku, vol=100):
        self.sku = sku
        self.weight = 10
        self._vol = vol
        self.storage_handle_config = types.SimpleNamespace(
            handling='conveyable', category='food')

    def volume(self):
        return self._vol


class _Storage:
    __slots__ = ('order', 'quantity')

    def __init__(self, order, quantity):
        self.order = order
        self.quantity = quantity


class _Bin:
    """A plain class, not SimpleNamespace: `from_batch` uses bins as dict keys, and
    SimpleNamespace defines __eq__ and so is unhashable."""
    __slots__ = ('location', 'x_phys', 'y_phys', 'bayX', 'bayY', 'unit_type', 'storage')

    def __init__(self, aisle, x, y, sku, qty, unit_type='singleton'):
        self.location = (aisle, x, y)
        self.x_phys = float(x)
        self.y_phys = float(y)
        self.bayX = x
        self.bayY = y
        self.unit_type = unit_type
        self.storage = _Storage(_Order(sku), qty)


def _bin(aisle, x, y, sku, qty, unit_type='singleton'):
    return _Bin(aisle, x, y, sku, qty, unit_type)


class _Mgr:
    """Only what `from_batch` reads off a manager."""

    def __init__(self, singleton=None, pallet=None):
        self._sku_singleton_bins = singleton or {}
        self._sku_pallet_bins = pallet or {}


def _batch(items):
    return types.SimpleNamespace(items=dict(items))


_WH = types.SimpleNamespace(bins=[])


# ── the shortfall is what the shelf could not cover ───────────────────────────────

def test_fully_stocked_demand_reports_no_shortfall():
    mgr = _Mgr(singleton={1: [_bin(1, 0, 0, 1, 10)]})
    tasks, short = Task.from_batch_with_shortfall(_batch({1: 4}), _WH, manager=mgr)
    assert short == {}
    assert sum(t.items[1] for t in tasks) == 4


def test_a_partially_stocked_sku_reports_the_gap():
    mgr = _Mgr(singleton={1: [_bin(1, 0, 0, 1, 3)]})
    tasks, short = Task.from_batch_with_shortfall(_batch({1: 10}), _WH, manager=mgr)
    assert short == {1: 7}
    assert sum(t.items[1] for t in tasks) == 3


def test_a_sku_with_no_bins_at_all_reports_its_whole_demand():
    """The case that produced NO record whatsoever: the SKU simply vanished."""
    mgr = _Mgr()
    tasks, short = Task.from_batch_with_shortfall(_batch({42: 6}), _WH, manager=mgr)
    assert short == {42: 6}
    assert tasks == []


def test_shortfall_accumulates_across_skus_independently():
    mgr = _Mgr(singleton={1: [_bin(1, 0, 0, 1, 2)],
                          2: [_bin(1, 1, 0, 2, 50)]})
    _tasks, short = Task.from_batch_with_shortfall(_batch({1: 5, 2: 3, 3: 8}), _WH, manager=mgr)
    assert short == {1: 3, 3: 8}
    assert 2 not in short, 'a fully-satisfied SKU must not appear'


def test_it_draws_across_several_bins_before_declaring_a_shortfall():
    mgr = _Mgr(singleton={1: [_bin(1, 0, 0, 1, 2), _bin(1, 1, 0, 1, 3)]})
    _tasks, short = Task.from_batch_with_shortfall(_batch({1: 9}), _WH, manager=mgr)
    assert short == {1: 4}


def test_reserve_bins_count_before_the_shortfall_does():
    """Forward-pick drains first, but a pallet bin still satisfies demand."""
    mgr = _Mgr(singleton={1: [_bin(1, 0, 0, 1, 2)]},
               pallet={1: [_bin(2, 0, 0, 1, 5, unit_type='pallet')]})
    _tasks, short = Task.from_batch_with_shortfall(_batch({1: 10}), _WH, manager=mgr)
    assert short == {1: 3}


# ── the two entry points agree, and the plain one is unchanged ────────────────────

def test_the_two_entry_points_produce_the_same_tasks():
    """One drain serves both, so the ~30 existing call sites are unaffected."""
    def mk():
        return _Mgr(singleton={1: [_bin(1, 0, 0, 1, 3)], 2: [_bin(1, 2, 0, 2, 9)]})

    plain = Task.from_batch(_batch({1: 10, 2: 4}), _WH, manager=mk())
    measured, _ = Task.from_batch_with_shortfall(_batch({1: 10, 2: 4}), _WH, manager=mk())
    key = lambda ts: sorted((t.aisle_id, tuple(sorted(t.items.items()))) for t in ts)
    assert key(plain) == key(measured)


def test_the_plain_entry_point_still_takes_four_positional_args():
    """`from_batch(batch, warehouse, manager, cart)` is called ~30 ways; the accumulator is
    keyword-only-by-convention and last."""
    import inspect
    params = list(inspect.signature(Task.from_batch).parameters)
    assert params[:4] == ['batch', 'warehouse', 'manager', 'cart']
    assert params[4] == '_shortfall'
    assert inspect.signature(Task.from_batch).parameters['_shortfall'].default is None


# ── the manager-less fallback must agree, or a caller sees a different answer ─────

def test_the_fallback_branch_reports_the_same_shortfall():
    """The two selection paths already agree on WHICH bins drain; they must agree on what
    is left over too."""
    b = _bin(1, 0, 0, 1, 3)
    wh = types.SimpleNamespace(bins=[b])
    _t_idx, s_idx = Task.from_batch_with_shortfall(
        _batch({1: 10}), _WH, manager=_Mgr(singleton={1: [b]}))
    _t_fb, s_fb = Task.from_batch_with_shortfall(_batch({1: 10}), wh, manager=None)
    assert s_idx == s_fb == {1: 7}


# ── the column ────────────────────────────────────────────────────────────────────

def test_batch_stats_carries_the_demand_side():
    from Optimization.persistence.Picking_Data import BatchStats
    bs = BatchStats(run_id=1, batch_id=0, duration=1.0, num_tasks=1, total_items=3,
                    avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5)
    assert bs.items_demanded == 0, 'defaults to 0 so a pre-column vintage reads sanely'
    bs.items_demanded = 10
    assert bs.items_demanded - bs.total_items == 7


def test_the_column_is_not_in_the_guaranteed_surface():
    """It cannot be: that surface is the INTERSECTION over every vetted vintage, and this
    column is absent from all of them. It rides the optional-with-default path instead."""
    from Optimization.persistence.Picking_Data import REQUIRES, _BATCH_OPTIONAL
    assert 'items_demanded' not in REQUIRES.tables['batch_stats']
    assert _BATCH_OPTIONAL['items_demanded'] == 0


# ── what the column exposed on its first real run ─────────────────────────────────

def test_a_sku_in_two_bins_of_one_aisle_is_OVER_picked():
    """A PRE-EXISTING defect, pinned the day the demand column made it visible.

    `Task.from_batch` plans per BIN — it decides to take 5 from the first bin and 3 from
    the second — but records only the per-AISLE total on `task.items`. Both picker loops
    then read `task.items[sku]` once **per bin** with no running remainder
    (`Pick.py:441`, `fast_pick.py:126`), so a SKU occupying several bins in one aisle is
    picked once per bin.

    Measured on the e2e harness: **6.7% more units picked than demanded**, across all 272
    batch_stats rows. Every throughput figure divides by `total_items`, so every one of
    them is inflated by about that much.

    The two sims do not even agree on the size of the error: with demand 8 over two bins
    of 5, `PickSimulation` picks 16 (it does not cap at bin stock at all, so it picks more
    than physically exists) and `DeferredPickSimulation` picks 10 (capped at the snapshot,
    so it drains both bins). `Pick.py:278-281` half-documents this as a contention
    divergence; it is not — it happens with one picker and no contention whatsoever.

    NOT FIXED HERE. The fix is for `Task` to carry the per-bin plan it already computes,
    and it moves every arm's numbers, so it is the user's call and its own commit. This
    test fails the day someone makes it right, which is the day this note should go.
    """
    import types

    from Warehouse.picking.Pick import PickConfig, PickSimulation
    from Warehouse.picking.fast_pick import DeferredPickSimulation

    def build():
        bins = [_bin(1, 0, 0, 1, 5), _bin(1, 10, 0, 1, 5)]   # ONE aisle, two bins, 5 each
        wh = types.SimpleNamespace(bins=bins)
        return Task.from_batch(_batch({1: 8}), wh, manager=_Mgr(singleton={1: bins}))

    task = build()[0]
    assert task.items == {1: 8}, 'the per-aisle total, not the per-bin plan'
    assert len(task.path) == 2, 'two bins to be visited'

    def picked(sim_cls):
        evs = sim_cls(build(), PickConfig(num_pickers=1)).run()
        return sum(e.quantity or 0 for e in evs if e.event_type == 'pick')

    assert picked(PickSimulation) == 16, 'reference sim: 8 from each bin, uncapped'
    assert picked(DeferredPickSimulation) == 10, 'deferred sim: capped at bin stock'


def test_one_bin_per_sku_per_aisle_is_correct():
    """Non-vacuity for the above: the over-pick needs TWO bins. With one, both sims agree
    with demand — which is why this survived so long."""
    import types

    from Warehouse.picking.Pick import PickConfig, PickSimulation
    from Warehouse.picking.fast_pick import DeferredPickSimulation

    def build():
        bins = [_bin(1, 0, 0, 1, 10)]
        return Task.from_batch(_batch({1: 8}),
                               types.SimpleNamespace(bins=bins),
                               manager=_Mgr(singleton={1: bins}))

    for sim_cls in (PickSimulation, DeferredPickSimulation):
        evs = sim_cls(build(), PickConfig(num_pickers=1)).run()
        assert sum(e.quantity or 0 for e in evs if e.event_type == 'pick') == 8


def test_the_runner_records_what_the_batch_asked_for():
    import inspect

    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    assert 'bs.items_demanded     = sum(batch.items.values())' in src
    assert 'from_batch_with_shortfall' in src, 'the runner measures the shortfall'
