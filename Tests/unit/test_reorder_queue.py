"""test_reorder_queue.py — the restock queue stays BOUNDED under a real pick loop.

What makes this file worth keeping
----------------------------------
Three other suites already assert queue behaviour, and all three fake the depletion:
`test_warehouse_sizing.test_restock_no_queue_growth` and `_run_batch_drain` empty bins by
hand and call `_notify_pick` directly; `test_equilibrium_reorder.test_fill_stability` never
runs a picker at all.  **This file is the only one that closes the loop through the pick
simulator** — `Batch` → `Task.from_batch` → `PickSimulation` → `_notify_pick` →
`check_reorders` → placement — on a warehouse sized by hand rather than by
`plan_warehouse`.  So it is the only place a regression in that chain (a pick that fails to
notify, a reorder that fires but never places) shows up as a growing queue.

The warehouse is deliberately UNDERSIZED
----------------------------------------
1,440 bins for 500 SKUs: the initial `enqueue_all` places 793 bins and leaves ~10,700 units
in the queue, and fill settles near 43%.  That is the point.  A warehouse with room to spare
cannot distinguish "the queue drains" from "the queue was never under pressure".  Here ~9,300
restock units flow through a permanently-saturated queue over 50 batches, and the property
under test is that they are ABSORBED rather than ACCUMULATED — the queue ends lower than it
started.  Nothing here asserts the queue empties; it cannot, and an assertion that it does
would be asserting the warehouse is big enough, not that the policy is stable.

Reorder policy under test (the RUN's declaration — ADR-0002)
-----------------------------------------------------------
The catalogue carries NO stock level.  A level is a run's declaration, so this file makes one
the way a run does — `simconfig/coverage.rescale_section`, the single production derivation,
at a declared lines-per-day:

    d_s             = lines_per_day * (freq_s / Σ freq) * E[q_s]        units of s per day
    L_s             = ceil(FLOOR_LINES * E[q_s])                        the line floor, >= 1
    equilibrium_qty = max(round(COVERAGE_DAYS * d_s), L_s)
    reorder_point   = min(Q-1, max(round(d_s * (lead_time + SAFETY_DAYS)), L_s))

`LINES_PER_DAY` is picked so the declared section sums to ~27,300 units of order-up-to
against the 1,440-bin warehouse below — within ~4% of the total the retired generator used
to author — so the pressure this file depends on is the pressure it always had.  A real run
gets its lines a day from the pair's fixed point (`simdriver/era_coverage.fixed_point`);
nothing here needs that geometry loop, only ONE declaration made the production way.

`_notify_pick` reads `order.reorder_point` directly and RAISES on a SKU no run has declared.
`check_reorders` fires an Order-Up-To reorder for `equilibrium_qty + pipeline - position`
units (the lead is 0 here, so the pipeline is 0).  Units that cannot be placed stay queued
(FIFO) until a bin opens.

    python -m pytest Tests/unit/test_reorder_queue.py -q

History: this file used to define a `check()` harness and a `run()` entry point with NO
`def test_` functions at all — pytest collected zero tests from it and its five assertions
were dead code in a module-level helper.  It is now real tests; do not re-introduce `check()`.
"""
from __future__ import annotations

import math
import random
from statistics import mean

import pytest

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.picking.Pick import PickConfig, PickSimulation
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.picking.Workload_Builder import Batch, BatchConfig, Task
from Warehouse.generation.generate_inventory import (
    DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
    build_inventory_with_profile,
)
from Warehouse.layout.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Optimization.simconfig import coverage as cov

# ── parameters ────────────────────────────────────────────────────────────────
# Sized so the whole fixture runs in ~2s.  SEED feeds every stream (see _run_50_batches).

SEED            = 42
N_SKUS          = 500
N_BATCHES       = 50
N_PICKERS       = 5
BATCH_MEAN_FRAC = 0.25
STABLE_WINDOW   = 20      # trailing batches used for the growth test
MIN_FILL        = 0.40

# The run's stock declaration (ADR-0002).  Coverage/safety/floor are the production defaults;
# LINES_PER_DAY is this file's knob — see the module docstring for why it is 250.
LINES_PER_DAY   = 250.0
COVERAGE_DAYS   = 10.0
SAFETY_DAYS     = 2.0
FLOOR_LINES     = 1.0
MAX_FLOOR_SHARE = 0.50    # the declaration must SIZE the section, not floor it

# Physical aisle dimensions: 6 pallet-column widths x 4 extra_large-height levels.
# With density expansion, singleton aisles get 3x more X bins and small-tier bins
# create 4x more Y levels than the physical slot count.
_AISLE_W = aisle_width_for(6)    # 6 x 48 = 288 physical units
_AISLE_H = aisle_height_for(4)   # 4 x 48 = 192 physical units

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_ALL_SIZES  = ['small', 'medium', 'large', 'extra_large']
_PALL_PROBS = [0.25, 0.25, 0.25, 0.25]

# 48 aisle types — (12 pallet + 12 singleton) x 2 handling types, matching run_simulation.
_AISLE_CFGS: list[AisleConfig] = []
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES,    _PALL_PROBS))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'pallet',    _AISLE_W, _AISLE_H, _ALL_SIZES,    _PALL_PROBS))
for _cat in _CATEGORIES:
    _AISLE_CFGS.append(AisleConfig('conveyable',     _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))
    _AISLE_CFGS.append(AisleConfig('non-conveyable', _cat, 'singleton', _AISLE_W, _AISLE_H, ['singleton'], None))


# ── warehouse builder ─────────────────────────────────────────────────────────

def _build_wh_cfg(n_skus: int) -> WarehouseConfig:
    """One replica of each of the 48 aisle types, plus extras only if n_skus needs them."""
    from Warehouse.layout.Aisle_Dimensions import SIZE_HEIGHTS, unit_bin_width as _ubw
    from Warehouse.layout.Aisle_Dimensions import SINGLETON_BIN_HEIGHT as _SBH
    n_types = len(_AISLE_CFGS)   # 48

    def _bins(cfg: AisleConfig) -> int:
        n_cols = _AISLE_W // _ubw(cfg.unit_type)
        if cfg.unit_type == 'singleton':
            return n_cols * (_AISLE_H // _SBH)
        probs  = cfg.size_probabilities or [1.0 / len(cfg.storage_sizes)] * len(cfg.storage_sizes)
        n_rows = sum(round(p * _AISLE_H) // SIZE_HEIGHTS[s] for s, p in zip(cfg.storage_sizes, probs))
        return n_cols * n_rows

    bins_per_aisle = _bins(_AISLE_CFGS[0])          # representative pallet aisle
    total_bins     = n_types * bins_per_aisle
    replicas       = max(1, math.ceil(n_skus / total_bins))
    total_aisles   = n_types * replicas
    weight         = replicas / total_aisles        # = 1/n_types; all types equal weight
    return WarehouseConfig(
        total_aisles  = total_aisles,
        aisle_splits  = [weight] * n_types,
        aisle_configs = _AISLE_CFGS,
    )


# ── the run, once ─────────────────────────────────────────────────────────────
#
# 50 batches of real picking is ~2s, so it runs ONCE for the whole module and every test
# below reads the same recorded trace.  Module scope also means the assertions describe one
# coherent run rather than six independently-seeded ones that could disagree.

class _Trace:
    """Everything the assertions below need, recorded from a single 50-batch run."""
    __slots__ = ('orders', 'declared', 'total_bins', 'init_queue', 'queue_depths', 'fill_rates',
                 'total_triggered', 'total_ordered', 'pallet_occupied', 'singleton_occupied')


@pytest.fixture(scope='module')
def run() -> _Trace:
    t = _Trace()

    # ── inventory ─────────────────────────────────────────────────────────────
    inventory = build_inventory_with_profile(
        num_skus           = N_SKUS,
        handling_splits    = [0.5, 0.5],
        category_splits    = [1 / 6] * 6,
        singleton_fraction = 0.5,
        dim_spec           = DEFAULT_DIM_SPEC,
        weight_spec        = DEFAULT_WEIGHT_SPEC,
        seed               = SEED,
    )
    t.orders = inventory.orders

    # ── the run's stock declaration ───────────────────────────────────────────
    # The catalogue authors no level (ADR-0002), so nothing below this line would work
    # without it: `enqueue_all` reads the order-up-to through `_equilibrium_qty`, which
    # RAISES on an undeclared SKU, and `_notify_pick` raises on a missing reorder point.
    # One call, the production one, pure over the orders.
    t.declared = cov.rescale_section(inventory.orders, LINES_PER_DAY,
                                     coverage_days = COVERAGE_DAYS,
                                     safety_days   = SAFETY_DAYS,
                                     floor_lines   = FLOOR_LINES)

    # ── warehouse ─────────────────────────────────────────────────────────────
    # Warehouse_Builder, Batch and PickSimulation all draw from the module-level
    # `random`, so each phase gets its own offset seed to keep them independent
    # and the whole trace reproducible.
    wh_cfg = _build_wh_cfg(len(inventory.orders))
    Aisle.next_aisle_id = 1              # class counter — reset or aisle ids leak between tests
    random.seed(SEED)
    warehouse    = Warehouse_Builder().from_config(wh_cfg).build()
    t.total_bins = len(warehouse.bins)

    # ── initial stock ─────────────────────────────────────────────────────────
    random.seed(SEED + 100)
    mgr = Inventory_Manager(warehouse)
    mgr.enqueue_all(inventory.orders)
    t.init_queue = mgr.queue_depth

    pick_cfg = PickConfig(
        num_pickers      = N_PICKERS,
        x_speed          = 1.0,
        y_speed          = 0.5,
        pick_intercept   = 1.0,
        pick_weight_coef = 1.1,
        pick_volume_coef = 1e-3,
        cart_swap_coef   = 10.0,
    )
    batch_cfg = BatchConfig(
        inventory_size = len(inventory.orders),
        mean_fraction  = BATCH_MEAN_FRAC,
        std_fraction   = 0.03,
    )

    t.queue_depths, t.fill_rates = [], []
    t.total_triggered = t.total_ordered = 0

    random.seed(SEED + 200)
    for _ in range(N_BATCHES):
        t.total_triggered += len(mgr.check_reorders())
        t.total_ordered   += mgr.units_ordered        # units this batch's OUP asked for

        batch = Batch(batch_cfg, inventory, affinity=None)
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if tasks:
            PickSimulation(tasks, pick_cfg, manager=mgr).run()

        t.queue_depths.append(mgr.queue_depth)
        t.fill_rates.append(len(mgr.unavailable) / t.total_bins)

    t.pallet_occupied = sum(1 for b in mgr._unavailable.values()
                            if b.storage is not None and b.unit_type == 'pallet')
    t.singleton_occupied = sum(1 for b in mgr._unavailable.values()
                               if b.storage is not None and b.unit_type == 'singleton')
    return t


# ── the reorder policy the run depends on ─────────────────────────────────────

def test_a_generated_catalogue_declares_no_stock_level():
    """The premise of the fixture above: nothing this file picks from arrives with a level.

    Its own catalogue is built here rather than read off the module fixture, which has
    already declared on those orders.  If a generator ever re-authored a level, the fixture's
    `rescale_section` call would silently become a no-op-looking overwrite and the queue
    pressure this file measures would be somebody else's number.
    """
    from Warehouse.inventory.inventory_common import UndeclaredStock, _equilibrium_qty

    fresh = build_inventory_with_profile(
        num_skus           = 8,
        handling_splits    = [0.5, 0.5],
        category_splits    = [1 / 6] * 6,
        singleton_fraction = 0.5,
        dim_spec           = DEFAULT_DIM_SPEC,
        weight_spec        = DEFAULT_WEIGHT_SPEC,
        seed               = SEED,
    )
    assert fresh.orders and not any(c.stock_declared() for c in fresh.orders)
    with pytest.raises(UndeclaredStock):
        _equilibrium_qty(fresh.orders[0])

    # ...and one declaration is all it takes to make the same SKU answerable.
    fresh.orders[0].declare_stock(7, 3)
    assert fresh.orders[0].stock_declared()
    assert _equilibrium_qty(fresh.orders[0]) == 7


def test_the_declared_levels_are_a_coherent_reorder_policy(run):
    """Every SKU must carry the three values `check_reorders` reads, with sane ones.

    `_notify_pick` reads `reorder_point` and `check_reorders` reads `equilibrium_qty`; a SKU
    missing either now raises rather than silently bleeding to zero, but a DECLARED level can
    still be incoherent: `reorder_point >= eq` fires a reorder on a full bin, every batch,
    forever, and `reorder_point < 1` never fires one at all.
    """
    assert all(c.stock_declared() for c in run.orders), 'the fixture declared no levels'

    bad_eq  = [(c.sku, c.equilibrium_qty) for c in run.orders if c.equilibrium_qty < 1]
    bad_rp  = [(c.sku, c.reorder_point, c.equilibrium_qty) for c in run.orders
               if not 1 <= c.reorder_point <= max(1, c.equilibrium_qty - 1)]
    bad_ebd = [(c.sku, c.expected_batch_demand) for c in run.orders
               if c.expected_batch_demand <= 0.0]

    assert not bad_eq,  f'{len(bad_eq)} SKUs with equilibrium_qty < 1, e.g. {bad_eq[:3]}'
    assert not bad_rp,  (f'{len(bad_rp)} SKUs violate 1 <= reorder_point <= equilibrium_qty-1, '
                         f'e.g. (sku, rp, eq) = {bad_rp[:3]}')
    assert not bad_ebd, (f'{len(bad_ebd)} SKUs with non-positive expected_batch_demand — the '
                         f'declared target collapses to the line floor, e.g. {bad_ebd[:3]}')


def test_the_declaration_sizes_the_section_rather_than_flooring_it(run):
    """A declaration that puts every SKU on its one-line floor is a warehouse sized from
    nothing — the failure the store section really hit once ("coverage in days floors the
    store section").  It would leave this file's queue trivially bounded for the most boring
    possible reason, so the coverage knob has to be doing the sizing.
    """
    st = run.declared
    assert st['floor_line_share'] <= MAX_FLOOR_SHARE, (
        f'{st["floor_line_share"]:.1%} of SKUs sit on their line floor at '
        f'{COVERAGE_DAYS} days / {LINES_PER_DAY} lines a day — the coverage term is not '
        f'sizing this section')
    assert st['sum_q'] > run.total_bins, (
        f'declared {st["sum_q"]} units against {run.total_bins} bins — the warehouse is not '
        f'undersized any more, so nothing below is under pressure')
    # Coverage is nominal AND real only above the floor; a tolerance, never ==.
    assert abs(st['realized_coverage_days'] - COVERAGE_DAYS) < 0.5, (
        f'SKUs above the floor carry {st["realized_coverage_days"]:.2f} days, not the '
        f'{COVERAGE_DAYS} declared')


# ── the headline property: the queue is bounded ───────────────────────────────

def test_the_queue_absorbs_restock_rather_than_accumulating_it(run):
    """~9,300 restock units flow through the queue in 50 batches and it ends LOWER.

    This is the whole reason the file exists.  If a placement path stopped draining — a
    reorder that enqueues but never places, a reclaimed bin that never returns to the
    index — the queue grows monotonically and this is the assertion that catches it.
    """
    final = run.queue_depths[-1]

    assert run.total_ordered > 0, (
        'no restock was ordered at all in 50 batches — the pick loop never depleted anything, '
        'so this run proves nothing about the queue')
    assert final <= run.init_queue, (
        f'queue grew over the run: started {run.init_queue}, ended {final} '
        f'({run.total_ordered} units ordered in between)')
    assert max(run.queue_depths) <= run.init_queue * 1.10, (
        f'queue peaked at {max(run.queue_depths)}, more than 10% above its initial '
        f'{run.init_queue} — restock is outrunning placement')


def test_the_queue_is_flat_over_the_trailing_window(run):
    """A slow leak would not trip the peak bound; comparing halves of the tail catches it.

    Split the last STABLE_WINDOW batches in two and compare means.  The run must be at
    steady state by then, so a second half more than 20% above the first is drift, not noise.
    """
    stable = run.queue_depths[-STABLE_WINDOW:]
    mid    = STABLE_WINDOW // 2
    first_half, second_half = mean(stable[:mid]), mean(stable[mid:])

    assert second_half <= first_half * 1.20, (
        f'queue drifting up over the last {STABLE_WINDOW} batches: '
        f'first-half mean {first_half:.1f} -> second-half mean {second_half:.1f}')


def test_reorders_actually_fire_during_the_pick_loop(run):
    """Guards against the failure mode where every assertion above passes vacuously.

    A pick loop that never triggers a reorder leaves the queue flat for the most boring
    possible reason.  5% of SKUs is a floor, not an expectation — the observed value is
    ~46% of SKUs restocked at least once across 50 batches.
    """
    min_expected = int(len(run.orders) * 0.05)
    assert run.total_triggered >= min_expected, (
        f'only {run.total_triggered} reorders fired across {N_BATCHES} batches '
        f'(expected >= {min_expected}) — picks are not reaching _notify_pick')


def test_fill_rate_holds_up_under_sustained_picking(run):
    """Bins must stay occupied.  A restock loop that fires but never lands empties the
    warehouse over 50 batches while the queue-growth tests above stay green."""
    final_fill = run.fill_rates[-1]
    assert final_fill >= MIN_FILL, (
        f'final fill {final_fill:.1%} below the {MIN_FILL:.0%} floor — restock is firing '
        f'but not landing in bins')
    assert min(run.fill_rates) >= MIN_FILL * 0.9, (
        f'fill dipped to {min(run.fill_rates):.1%} mid-run (final was {final_fill:.1%})')


def test_both_pallet_and_singleton_bins_are_occupied(run):
    """min-pallets packing must be active: bulk on pallets, remainder as singletons.

    If `viable_storage_units` regressed to a single unit family, one of these is zero and
    every other assertion in this file still passes — the queue would be just as bounded.
    """
    assert run.pallet_occupied > 0, (
        f'no pallet bin occupied (singleton={run.singleton_occupied}) — the bulk half of '
        f'min-pallets packing is not running')
    assert run.singleton_occupied > 0, (
        f'no singleton bin occupied (pallet={run.pallet_occupied}) — the remainder half of '
        f'min-pallets packing is not running')
