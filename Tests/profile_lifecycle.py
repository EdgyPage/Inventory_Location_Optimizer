"""
profile_lifecycle.py — fine-grained lifecycle profiler for the batch simulation.

Two complementary modes
-----------------------
  wall      Per-manager wall-clock breakdown with monkey-patched affinity
            sub-timings.  Shows exactly how much of reorder_B/C is the
            assignment_fn, and how much of that is delta_lift_idxs.

  cprofile  cProfile around the full batch loop with pstats output, filtered
            to project modules so numpy/sqlite internals stay out of the top-N.

Zero source changes — Warehouse/ and Optimization/ are untouched.
AffinityStore methods and assignment_fn are patched on the fly and restored.

Usage
-----
    cd Tests
    python profile_lifecycle.py                            # both modes, defaults
    python profile_lifecycle.py --mode wall --batches 50
    python profile_lifecycle.py --mode cprofile --top-n 40
    python profile_lifecycle.py --skus 3000 --bins-per-aisle 150 --batches 100
    python profile_lifecycle.py --fill 0.95               # 95% full warehouse
    python profile_lifecycle.py --fill 0.99 --batches 30  # near-capacity stress test
"""
from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import random
import sys
import time
from collections import defaultdict

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_LOGS_DIR  = os.path.join(_HERE, 'logs')
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Optimization'))

import numpy as np

from Affinity_Store import AffinityStore
from Aisle_Storage import Aisle
from Inventory_Builder import Inventory
from Inventory_Management import Inventory_Manager, LoadParams, Placement
from Assignment_Functions import (
    build_cluster_minimizing_assignment_fn,
    build_cluster_maximizing_assignment_fn,
)
from Pick import PickConfig, PickSimulation
from Warehouse_Builder import Warehouse_Builder
from Workload import WorkloadParams
from Workload_Builder import Batch, BatchConfig, Task
from Simulation_Analytics import extract_batch_stats, extract_task_stats

# Reuse setup helpers from the existing benchmark — no duplication, no source changes
from perf_simulation import (
    _build_inventory,
    _build_affinity_store,
    _CATEGORIES,
)

_HANDLINGS = ['conveyable', 'non-conveyable']


# ── monkey-patch infrastructure ───────────────────────────────────────────────

# Every AffinityStore method we want to time individually
_AFF_METHODS = ('delta_lift_idxs', 'delta_lift', 'sum_lift', 'load_for_skus')


def _install_timers() -> tuple[dict[str, list], dict]:
    """Wrap AffinityStore class methods with per-call timers.

    Returns (counters, originals).  Each counter is [total_seconds, call_count].
    Call _remove_timers(originals) after the benchmark to restore the class.
    """
    counters: dict[str, list] = {
        k: [0.0, 0] for k in _AFF_METHODS + ('assign_B', 'assign_C')
    }
    originals: dict = {}

    for name in _AFF_METHODS:
        orig = getattr(AffinityStore, name)
        originals[name] = orig

        # Capture name and orig in the closure explicitly
        def _make_wrapper(n: str, fn):
            def _w(self, *args, **kwargs):
                t = time.perf_counter()
                r = fn(self, *args, **kwargs)
                counters[n][0] += time.perf_counter() - t
                counters[n][1] += 1
                return r
            _w.__name__ = f'_timed_{n}'
            return _w

        setattr(AffinityStore, name, _make_wrapper(name, orig))

    return counters, originals


def _remove_timers(originals: dict) -> None:
    for name, fn in originals.items():
        setattr(AffinityStore, name, fn)


def _wrap_assignment(manager: Inventory_Manager, key: str, counters: dict) -> None:
    """Wrap manager.placement.place_one in-place with a wall-clock counter."""
    orig = manager.placement.place_one

    def _timed(unit, candidates):
        t = time.perf_counter()
        r = orig(unit, candidates)
        counters[key][0] += time.perf_counter() - t
        counters[key][1] += 1
        return r

    # Preserve the coupling tag (placement.uses_aisle_index was cached at build time).
    _timed.uses_aisle_index = getattr(orig, 'uses_aisle_index', False)
    manager.placement.place_one = _timed


# ── shared asset builder ──────────────────────────────────────────────────────

def _set_equilibrium(cartons: list, lead_time: float = 2.0, supply_cv: float = 0.1) -> None:
    """Set OUP equilibrium fields on carton objects directly.

    Called only from the test script — no source files are modified.
    Keeps equilibrium_qty small (capped at 3) so warehouse size stays
    proportionate to n_skus rather than inflating with demand rates.

    lead_time : mean replenishment lag in batches (sampled by check_reorders)
    supply_cv : coefficient of variation of received quantity (0 = exact fill)
    """
    for c in cartons:
        expected = c.demand.frequency * c.demand.quantity_rate  # E[picks/batch]
        # Cap at 3 so each carton needs at most 3 bin slots; prevents warehouse
        # from ballooning when demand.quantity_rate is high.
        eq_qty = max(1, min(3, round(expected * 2)))
        c.expected_batch_demand = expected
        c.equilibrium_qty       = eq_qty
        c.reorder_point         = 1        # reorder as soon as stock hits 1 unit
        c.lead_time_mean        = lead_time
        c.supply_cv             = supply_cv


def _build_assets(
    n_skus: int,
    bins_per_aisle: int,
    n_pickers: int,
    seed: int,
    fill: float = 0.87,
) -> tuple:
    """Build inventory, three warehouses (A / B / C), pick config, affinity store.

    Uses Inventory_Manager.plan_warehouse() so warehouse sizing accounts for
    actual carton-type compatibility and the target fill is honoured.
    Equilibrium OUP fields are set on cartons in the test script so
    check_reorders uses realistic reorder thresholds throughout the run.
    enqueue_all is called with quantity=None so each carton stocks its own
    equilibrium_qty rather than a fixed override.
    """
    random.seed(seed)
    np.random.seed(seed)

    # Build exactly n_skus cartons; plan_warehouse sizes the warehouse around them
    # and samples down to target_fill, so no 2x pool is needed.
    pool_inv = _build_inventory(n_skus, seed)
    _set_equilibrium(pool_inv.cartons)

    # Physical aisle dimensions derived from bins_per_aisle
    n_cols   = max(1, bins_per_aisle // 20)
    aisle_w  = n_cols * 48
    aisle_h  = 20 * 48

    print(f'  plan_warehouse (pool={len(pool_inv.cartons):,} cartons, '
          f'target_fill={fill:.0%})...', end='', flush=True)
    t0 = time.perf_counter()
    plan = Inventory_Manager.plan_warehouse(
        pool_inv.cartons,
        categories   = _CATEGORIES,
        handlings    = _HANDLINGS,
        aisle_width  = aisle_w,
        aisle_height = aisle_h,
        target_fill  = fill,
        rng          = random.Random(seed + 1),
    )
    print(f' {time.perf_counter() - t0:.2f}s')
    print(f'  Plan: {plan.total_bins:,} bins  '
          f'{len(plan.sampled):,} sampled SKUs  '
          f'expected fill={plan.expected_fill:.1%}')

    sampled  = plan.sampled
    wh_cfg   = plan.warehouse_cfg
    inventory = Inventory(sampled)   # wrap for Batch / affinity store

    pick_cfg = PickConfig(
        num_pickers      = n_pickers,
        x_speed          = 1.0,
        y_speed          = 0.5,
        pick_intercept   = 1.0,
        pick_weight_coef = 1.1,
        pick_volume_coef = 1e-3,
        cart_swap_coef   = 10.0,
    )
    wp          = WorkloadParams.from_pick_config(pick_cfg)
    load_params = LoadParams(lambda_=1.0, k=1.0, gamma=1.5)

    print('  Building affinity store...', end='', flush=True)
    t0 = time.perf_counter()
    aff_store = _build_affinity_store(inventory, top_k=20, seed=seed)
    print(f' {time.perf_counter() - t0:.2f}s')

    def _make_wh(label: str, aff=None, fn_builder=None):
        Aisle.next_aisle_id = 1
        random.seed(seed)
        wh  = Warehouse_Builder().from_config(wh_cfg).build()
        mgr = Inventory_Manager(wh, affinity=aff) if aff else Inventory_Manager(wh)
        # Correct OUP sequence: stock first with uniform assignment, then arm
        # the load-aware fn so reorders during the batch loop use it.
        random.seed(seed + 1)
        t_enq = time.perf_counter()
        mgr.enqueue_all(sampled, quantity=None)   # reads carton.equilibrium_qty
        enq_s = time.perf_counter() - t_enq
        if aff and fn_builder:
            # Mirror the production worker (strategy_runner) for a cluster strategy:
            # lift + demand state, then arm the per-aisle travel-cost index, then build
            # the cluster fn bound to mgr._aisle_index.  This profiles the ACTUAL
            # production placement path (cmin/cmax) instead of the load_* fns that the
            # comparison pipeline never runs.
            mgr.init_lift_state(aff)              # scan placed bins for aisle state
            mgr.init_demand_state(inventory)     # per-aisle demand sums (cluster reads/commits)
            mgr.init_travel_costs(wp)            # precompute _D + per-aisle sorted index
            freq_by_sku = {c.sku: c.demand.frequency    for c in sampled}
            qty_by_sku  = {c.sku: c.demand.quantity_rate for c in sampled}
            freq_by_idx = {aff._sku_to_idx[c.sku]: c.demand.frequency
                           for c in sampled if c.sku in aff._sku_to_idx}
            mgr.placement = Placement('profiled', fn_builder(
                aff, wp,
                mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
                freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0,
                aisle_index=mgr._aisle_index,
            ))
        filled = len(mgr.unavailable)
        total  = len(wh.bins)
        print(f'    Warehouse {label}: {filled:,} / {total:,} bins '
              f'({filled / total:.1%})  enqueue={enq_s:.2f}s')
        return wh, mgr

    print('  Warehouse A (no affinity)...')
    wh_A, mgr_A = _make_wh('A')
    print('  Warehouse B (cluster-minimising + affinity, production path)...')
    wh_B, mgr_B = _make_wh('B', aff_store, build_cluster_minimizing_assignment_fn)
    print('  Warehouse C (cluster-maximising + affinity, production path)...')
    wh_C, mgr_C = _make_wh('C', aff_store, build_cluster_maximizing_assignment_fn)

    batch_cfg = BatchConfig(
        inventory_size = len(sampled),
        mean_fraction  = 0.25,
        std_fraction   = 0.03,
    )

    return (inventory,
            wh_A, mgr_A,
            wh_B, mgr_B,
            wh_C, mgr_C,
            pick_cfg, wp, batch_cfg, aff_store)


# ── wall-clock profiler ───────────────────────────────────────────────────────

def run_wall_profile(
    n_skus: int,
    bins_per_aisle: int,
    n_batches: int,
    n_pickers: int,
    seed: int,
    fill: float = 0.87,
) -> None:
    W = 74
    print(f'\n{"=" * W}')
    print(f'  Wall-Clock Profile')
    print(f'  SKUs={n_skus:,}  bins/aisle={bins_per_aisle}  '
          f'batches={n_batches}  pickers={n_pickers}  fill={fill:.0%}  seed={seed}')
    print(f'{"=" * W}')

    (inventory, wh_A, mgr_A, wh_B, mgr_B, wh_C, mgr_C,
     pick_cfg, wp, batch_cfg, aff_store) = _build_assets(n_skus, bins_per_aisle, n_pickers, seed, fill=fill)

    counters, originals = _install_timers()
    _wrap_assignment(mgr_B, 'assign_B', counters)
    _wrap_assignment(mgr_C, 'assign_C', counters)

    _t: dict[str, float] = defaultdict(float)
    skipped = 0
    random.seed(seed + 100)
    t_loop = time.perf_counter()

    for i in range(n_batches):

        # check_reorders — timed per manager so we can see B/C overhead
        t0 = time.perf_counter(); mgr_A.check_reorders(); _t['reorder_A'] += time.perf_counter() - t0
        t0 = time.perf_counter(); mgr_B.check_reorders(); _t['reorder_B'] += time.perf_counter() - t0
        t0 = time.perf_counter(); mgr_C.check_reorders(); _t['reorder_C'] += time.perf_counter() - t0

        # batch construction (shared — same batch fed to all three)
        t0 = time.perf_counter()
        batch = Batch(batch_cfg, inventory, affinity=None)
        _t['batch'] += time.perf_counter() - t0

        # Task.from_batch — per warehouse
        t0 = time.perf_counter(); ta = Task.from_batch(batch, wh_A, manager=mgr_A); _t['tasks_A'] += time.perf_counter() - t0
        t0 = time.perf_counter(); tb = Task.from_batch(batch, wh_B, manager=mgr_B); _t['tasks_B'] += time.perf_counter() - t0
        t0 = time.perf_counter(); tc = Task.from_batch(batch, wh_C, manager=mgr_C); _t['tasks_C'] += time.perf_counter() - t0

        if not ta or not tb or not tc:
            skipped += 1
            continue

        # PickSimulation.run — per warehouse
        t0 = time.perf_counter(); ea = PickSimulation(ta, pick_cfg, manager=mgr_A).run(); _t['sim_A'] += time.perf_counter() - t0
        t0 = time.perf_counter(); eb = PickSimulation(tb, pick_cfg, manager=mgr_B).run(); _t['sim_B'] += time.perf_counter() - t0
        t0 = time.perf_counter(); ec = PickSimulation(tc, pick_cfg, manager=mgr_C).run(); _t['sim_C'] += time.perf_counter() - t0

        # analytics stats (shared cost, counted once)
        t0 = time.perf_counter()
        extract_batch_stats(ea, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(eb, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(ec, batch_id=i, k_pickers=n_pickers)
        extract_task_stats(ea, ta, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(eb, tb, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(ec, tc, batch_id=i, affinity=aff_store, wp=wp)
        _t['stats'] += time.perf_counter() - t0

    _remove_timers(originals)

    done = n_batches - skipped
    wall = time.perf_counter() - t_loop

    # ── helpers ──────────────────────────────────────────────────────────────
    def _tot(s: float) -> str:
        return f'{s:.3f}s'

    def _per(s: float) -> str:
        return f'{s * 1000 / max(done, 1):.2f}ms'

    def _vs_a(phase: str, lbl: str) -> str:
        a = _t[f'{phase}_A']
        x = _t[f'{phase}_{lbl}']
        if a <= 0:
            return ''
        delta = (x - a) / a * 100
        if abs(delta) < 5:
            return ''
        sign = '+' if delta > 0 else ''
        return f'  ({sign}{delta:.0f}% vs A)'

    # ── per-manager table ─────────────────────────────────────────────────────
    print(f'\n  {done} batches in {wall:.1f}s  ({done / wall:.1f} batches/s)  skipped={skipped}')
    print(f'\n  Per-manager phases:')
    col = 20
    hdr = (f'  {"Phase":<{col}}'
           f'  {"A total":>8}  {"A/batch":>8}'
           f'  {"B total":>8}  {"B/batch":>8}'
           f'  {"C total":>8}  {"C/batch":>8}')
    sep = f'  {"-" * col}  {"--------":>8}  {"--------":>8}  {"--------":>8}  {"--------":>8}  {"--------":>8}  {"--------":>8}'
    print(hdr)
    print(sep)
    for phase in ('reorder', 'tasks', 'sim'):
        ta_v = _t[f'{phase}_A']
        tb_v = _t[f'{phase}_B']
        tc_v = _t[f'{phase}_C']
        print(f'  {phase:<{col}}'
              f'  {_tot(ta_v):>8}  {_per(ta_v):>8}'
              f'  {_tot(tb_v):>8}  {_per(tb_v):>8}{_vs_a(phase, "B")}'
              f'  {_tot(tc_v):>8}  {_per(tc_v):>8}{_vs_a(phase, "C")}')

    # ── shared phases ─────────────────────────────────────────────────────────
    print(f'\n  Shared phases:')
    print(f'  {"Phase":<{col}}  {"Total":>8}  {"Per-batch":>9}')
    print(f'  {"-" * col}  {"--------":>8}  {"---------":>9}')
    for phase in ('batch', 'stats'):
        v = _t[phase]
        print(f'  {phase:<{col}}  {_tot(v):>8}  {_per(v):>9}')

    # ── affinity sub-timings (from monkey-patch) ──────────────────────────────
    print(f'\n  Affinity sub-timings  (B+C assignment_fn + patched AffinityStore):')
    print(f'  {"Function":<22}  {"Total":>8}  {"Calls":>9}  {"Per-call":>10}')
    print(f'  {"-" * 22}  {"--------":>8}  {"---------":>9}  {"----------":>10}')
    for key in ('assign_B', 'assign_C') + _AFF_METHODS:
        elapsed, calls = counters[key]
        per_call = f'{elapsed / calls * 1e6:.1f}us' if calls > 0 else '—'
        calls_str = f'{calls:,}' if calls > 0 else '—'
        print(f'  {key:<22}  {_tot(elapsed):>8}  {calls_str:>9}  {per_call:>10}')

    # ── slowdown diagnosis ────────────────────────────────────────────────────
    print(f'\n  Diagnosis:')
    any_finding = False
    for phase in ('reorder', 'tasks', 'sim'):
        a_v = _t[f'{phase}_A']
        for lbl in ('B', 'C'):
            x_v = _t[f'{phase}_{lbl}']
            if a_v > 0 and x_v > a_v * 1.1:
                pct = (x_v - a_v) / a_v * 100
                severity = 'significant <-- investigate' if pct > 50 else 'minor'
                print(f'    {phase}_{lbl} is {pct:.0f}% slower than A  [{severity}]')
                any_finding = True

    for lbl in ('B', 'C'):
        assign_t  = counters[f'assign_{lbl}'][0]
        reorder_t = _t[f'reorder_{lbl}']
        if reorder_t > 0 and assign_t > 0:
            share = assign_t / reorder_t * 100
            note = '<-- assignment_fn dominates reorder' if share > 60 else ''
            print(f'    assign_{lbl} = {share:.0f}% of reorder_{lbl}  {note}')
            any_finding = True

    delta_t    = counters['delta_lift_idxs'][0]
    assign_bc  = counters['assign_B'][0] + counters['assign_C'][0]
    if assign_bc > 0 and delta_t > 0:
        share = delta_t / assign_bc * 100
        note = '<-- affinity CSR queries are the bottleneck' if share > 50 else ''
        print(f'    delta_lift_idxs = {share:.0f}% of assign_B+C combined  {note}')
        any_finding = True

    if not any_finding:
        print('    No significant slowdown detected at this scale.')

    print(f'\n{"=" * W}')


# ── cProfile profiler ─────────────────────────────────────────────────────────

# Modules whose functions are worth showing; everything else (numpy, sqlite, etc.)
# is filtered out so the top-N list stays readable.
_PROJECT_MODULES = {
    'Inventory_Management', 'Affinity_Store', 'Workload_Builder',
    'Pick', 'Simulation_Analytics', 'Workload', 'perf_simulation',
    'profile_lifecycle',
}


def run_cprofile(
    n_skus: int,
    bins_per_aisle: int,
    n_batches: int,
    n_pickers: int,
    seed: int,
    top_n: int,
    fill: float = 0.87,
) -> None:
    W = 74
    print(f'\n{"=" * W}')
    print(f'  cProfile Run')
    print(f'  SKUs={n_skus:,}  bins/aisle={bins_per_aisle}  '
          f'batches={n_batches}  pickers={n_pickers}  fill={fill:.0%}  top_n={top_n}')
    print(f'{"=" * W}')

    (inventory, wh_A, mgr_A, wh_B, mgr_B, wh_C, mgr_C,
     pick_cfg, wp, batch_cfg, aff_store) = _build_assets(n_skus, bins_per_aisle, n_pickers, seed, fill=fill)

    skipped = 0
    random.seed(seed + 100)

    pr = cProfile.Profile()
    pr.enable()

    for i in range(n_batches):
        mgr_A.check_reorders()
        mgr_B.check_reorders()
        mgr_C.check_reorders()

        batch = Batch(batch_cfg, inventory, affinity=None)

        ta = Task.from_batch(batch, wh_A, manager=mgr_A)
        tb = Task.from_batch(batch, wh_B, manager=mgr_B)
        tc = Task.from_batch(batch, wh_C, manager=mgr_C)

        if not ta or not tb or not tc:
            skipped += 1
            continue

        ea = PickSimulation(ta, pick_cfg, manager=mgr_A).run()
        eb = PickSimulation(tb, pick_cfg, manager=mgr_B).run()
        ec = PickSimulation(tc, pick_cfg, manager=mgr_C).run()

        extract_batch_stats(ea, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(eb, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(ec, batch_id=i, k_pickers=n_pickers)
        extract_task_stats(ea, ta, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(eb, tb, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(ec, tc, batch_id=i, affinity=aff_store, wp=wp)

    pr.disable()

    done = n_batches - skipped
    print(f'\n  Batches run: {done}  skipped: {skipped}')

    # ── filtered pstats output ────────────────────────────────────────────────
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf)
    stats.strip_dirs()
    stats.sort_stats('cumulative')
    # Pull enough rows that filtering still leaves top_n visible
    stats.print_stats(top_n * 8)
    raw = buf.getvalue().splitlines()

    print(f'\n  Top {top_n} project-module functions by cumulative time:')
    print(f'  (filter: {", ".join(sorted(_PROJECT_MODULES))})')
    print()

    header_printed = False
    printed = 0
    for line in raw:
        stripped = line.strip()
        if not stripped:
            continue
        # Print the column-header line (contains 'ncalls')
        if 'ncalls' in stripped and not header_printed:
            print('  ' + stripped)
            header_printed = True
            continue
        if not header_printed:
            continue
        if printed >= top_n:
            break
        # Show only lines from our project modules; skip stdlib / third-party
        if any(mod in stripped for mod in _PROJECT_MODULES):
            print('  ' + stripped)
            printed += 1

    if printed == 0:
        print('  (no matching lines — re-run with --mode cprofile and check module names)')

    # ── raw dump option hint ──────────────────────────────────────────────────
    print(f'\n  Tip: to see the unfiltered top-{top_n} (includes numpy/sqlite):')
    print(f'       python profile_lifecycle.py --mode cprofile --no-filter')

    print(f'\n{"=" * W}')


def run_cprofile_raw(
    n_skus: int,
    bins_per_aisle: int,
    n_batches: int,
    n_pickers: int,
    seed: int,
    top_n: int,
    fill: float = 0.87,
) -> None:
    """Like run_cprofile but prints the unfiltered pstats top-N."""
    W = 74
    print(f'\n{"=" * W}')
    print(f'  cProfile Run (unfiltered)')
    print(f'  SKUs={n_skus:,}  bins/aisle={bins_per_aisle}  '
          f'batches={n_batches}  pickers={n_pickers}  fill={fill:.0%}  top_n={top_n}')
    print(f'{"=" * W}')

    (inventory, wh_A, mgr_A, wh_B, mgr_B, wh_C, mgr_C,
     pick_cfg, wp, batch_cfg, aff_store) = _build_assets(n_skus, bins_per_aisle, n_pickers, seed, fill=fill)

    skipped = 0
    random.seed(seed + 100)

    pr = cProfile.Profile()
    pr.enable()

    for i in range(n_batches):
        mgr_A.check_reorders(); mgr_B.check_reorders(); mgr_C.check_reorders()
        batch = Batch(batch_cfg, inventory, affinity=None)
        ta = Task.from_batch(batch, wh_A, manager=mgr_A)
        tb = Task.from_batch(batch, wh_B, manager=mgr_B)
        tc = Task.from_batch(batch, wh_C, manager=mgr_C)
        if not ta or not tb or not tc:
            skipped += 1
            continue
        ea = PickSimulation(ta, pick_cfg, manager=mgr_A).run()
        eb = PickSimulation(tb, pick_cfg, manager=mgr_B).run()
        ec = PickSimulation(tc, pick_cfg, manager=mgr_C).run()
        extract_batch_stats(ea, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(eb, batch_id=i, k_pickers=n_pickers)
        extract_batch_stats(ec, batch_id=i, k_pickers=n_pickers)
        extract_task_stats(ea, ta, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(eb, tb, batch_id=i, affinity=aff_store, wp=wp)
        extract_task_stats(ec, tc, batch_id=i, affinity=aff_store, wp=wp)

    pr.disable()

    stats = pstats.Stats(pr, stream=sys.stdout)
    stats.strip_dirs()
    stats.sort_stats('cumulative')
    stats.print_stats(top_n)
    print(f'{"=" * W}')


# ── tee writer ───────────────────────────────────────────────────────────────

class _Tee:
    """Write to both a file and the original stdout simultaneously."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._file   = open(path, 'w', encoding='utf-8')
        self._stdout = sys.stdout
        sys.stdout   = self

    def write(self, data: str) -> int:
        try:
            self._stdout.write(data)
        except UnicodeEncodeError:
            self._stdout.write(data.encode('ascii', errors='replace').decode('ascii'))
        self._file.write(data)
        return len(data)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()

    def close(self) -> None:
        sys.stdout = self._stdout
        self._file.flush()
        self._file.close()

    def __enter__(self) -> '_Tee':
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _log_path(args: argparse.Namespace) -> str:
    import datetime
    ts   = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    slug = (f'{ts}'
            f'_mode-{args.mode}'
            f'_skus-{args.skus}'
            f'_bins-{args.bins_per_aisle}'
            f'_batches-{args.batches}'
            f'_fill-{int(args.fill * 100):02d}'
            f'_seed-{args.seed}')
    return os.path.join(_LOGS_DIR, f'{slug}.log')


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Fine-grained batch-simulation lifecycle profiler',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--mode',           choices=['wall', 'cprofile', 'both', 'raw'],
                        default='both',
                        help='wall=per-function timers; cprofile=Python profiler; '
                             'both=wall then cprofile; raw=unfiltered cprofile')
    parser.add_argument('--skus',           type=int,   default=2_000)
    parser.add_argument('--bins-per-aisle', type=int,   default=100)
    parser.add_argument('--batches',        type=int,   default=100)
    parser.add_argument('--pickers',        type=int,   default=10)
    parser.add_argument('--top-n',          type=int,   default=40,
                        help='Number of functions shown in cprofile output')
    parser.add_argument('--seed',           type=int,   default=42)
    parser.add_argument('--fill',           type=float, default=0.87,
                        help='Target initial shelf utilisation (0.01–0.99). '
                             '0.87 = default (15%% headroom), 0.95 = near-full, '
                             '0.99 = stress test. Fewer empty bins means placement '
                             'assignment_fn searches harder for compatible slots.')
    args = parser.parse_args()

    if not (0.01 <= args.fill <= 0.99):
        parser.error('--fill must be between 0.01 and 0.99')

    log = _log_path(args)
    kw = dict(
        n_skus         = args.skus,
        bins_per_aisle = args.bins_per_aisle,
        n_batches      = args.batches,
        n_pickers      = args.pickers,
        seed           = args.seed,
        fill           = args.fill,
    )

    with _Tee(log):
        print(f'# log: {log}')

        if args.mode in ('wall', 'both'):
            run_wall_profile(**kw)

        if args.mode in ('cprofile', 'both'):
            run_cprofile(**kw, top_n=args.top_n)

        if args.mode == 'raw':
            run_cprofile_raw(**kw, top_n=args.top_n)

    # Print path to stderr so it's visible even if stdout was redirected
    print(f'\nLog saved -> {log}', file=sys.stderr)


if __name__ == '__main__':
    main()
