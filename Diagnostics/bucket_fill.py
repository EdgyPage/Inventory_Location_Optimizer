"""bucket_fill.py — read-only diagnostic for the queue-blowup / sub-target-fill bug.

Localises the per-(handling, category, size, unit_type) bucket mismatch behind a deep
`_stock_queue` coexisting with global fill below target.  Two views:

  SIZING  — per bucket: built capacity (bins), the `stock_plan` SPREAD footprint that
            initial stock places, and the DEFAULT-PACK footprint a dumb-JIT warehouse
            would need (bucket_requirements with stock_plan stripped).  Plus default-pack
            timing, since retiring stock_plan puts that cost back on the hot path.

  RUNTIME — runs a short single-process FIFO sim (mirrors strategy_runner's loop) and
            snapshots per-bucket {occupied, free, queued units} + the global fill / queue
            trajectory, flagging buckets >95% full (overflow) and <40% full (waste).

Nothing here mutates production data; it only reads DBs and runs an in-memory sim.

Usage
-----
    python Diagnostics/bucket_fill.py [profile_label_or_inv_db] [--max-skus N]
        [--batches N] [--max-bins N] [--min-bins N]
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import tempfile
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path[:0] = [os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization')]

import run_simulation as rs
from Aisle_Storage import Aisle
from Inventory_Management import Inventory_Manager
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, Task
from Pick import PickConfig, DEFAULT_HEIGHT_BRACKETS
from fast_pick import DeferredPickSimulation
from generation.generate_inventory import load_inventory_from_db
from strategies import STRATEGY_BY_KEY, StrategyContext
from Workload import WorkloadParams

Bucket = tuple   # (handling, category, size, unit_type)


# ── bucketing helpers ───────────────────────────────────────────────────────────

def _bin_bucket(b) -> Bucket:
    return (b.handling_type, b.storage_type, b.storage_size, b.unit_type)


def _unit_bucket(u) -> Bucket:
    shc = u.order.storage_handle_config
    return (shc.handling, shc.category, u.storage_size, u.unit_category)


def _capacity_by_bucket(warehouse) -> dict[Bucket, int]:
    cap: dict[Bucket, int] = defaultdict(int)
    for b in warehouse.bins:
        cap[_bin_bucket(b)] += 1
    return dict(cap)


def _occupied_by_bucket(warehouse) -> dict[Bucket, int]:
    occ: dict[Bucket, int] = defaultdict(int)
    for b in warehouse.bins:
        if b.storage is not None:
            occ[_bin_bucket(b)] += 1
    return dict(occ)


def _queued_by_bucket(mgr) -> dict[Bucket, int]:
    q: dict[Bucket, int] = defaultdict(int)
    for u in list(mgr._stock_queue):
        q[_unit_bucket(u)] += 1
    return dict(q)


def _fmt_bucket(b: Bucket) -> str:
    h, c, s, u = b
    return f'{h[:4]:<4} {c[:5]:<5} {s[:11]:<11} {u[:9]:<9}'


# ── sizing view ─────────────────────────────────────────────────────────────────

def sizing_view(inv_db: str, allowlist: set, warehouse, planned_cartons: list, log) -> None:
    cap = _capacity_by_bucket(warehouse)

    # SPREAD footprint: planned orders carry stock_plan -> viable_storage_units reproduces it.
    t0 = time.perf_counter()
    spread = Inventory_Manager.bucket_requirements(planned_cartons)
    t_spread = time.perf_counter() - t0

    # DEFAULT-PACK footprint: same SKUs, original equilibrium, NO stock_plan -> dumb-JIT packing.
    orig = load_inventory_from_db(inv_db)
    subset = [c for c in orig.orders if c.sku in allowlist] if allowlist else orig.orders
    for c in subset:                       # ensure no plan sneaks in from the DB
        c.stock_plan = None
    t0 = time.perf_counter()
    default = Inventory_Manager.bucket_requirements(subset)
    t_default = time.perf_counter() - t0

    total_bins = sum(cap.values())
    print('\n' + '=' * 96)
    print('SIZING VIEW  — capacity vs stock_plan SPREAD vs default-pack (dumb-JIT) demand')
    print('=' * 96)
    print(f'{"bucket":<32}{"capacity":>9}{"spread":>9}{"sprd%":>7}'
          f'{"default":>9}{"dflt%":>7}')
    print('-' * 96)
    for b in sorted(cap, key=lambda k: -cap[k]):
        c = cap[b]
        sp, df = spread.get(b, 0), default.get(b, 0)
        print(f'{_fmt_bucket(b):<32}{c:>9}{sp:>9}{sp / c * 100 if c else 0:>6.0f}%'
              f'{df:>9}{df / c * 100 if c else 0:>6.0f}%')
    print('-' * 96)
    sp_tot, df_tot = sum(spread.values()), sum(default.values())
    print(f'{"TOTAL":<32}{total_bins:>9}{sp_tot:>9}{sp_tot / total_bins * 100:>6.0f}%'
          f'{df_tot:>9}{df_tot / total_bins * 100:>6.0f}%')
    print(f'\nGlobal expected fill — stock_plan spread: {sp_tot / total_bins:.1%}   '
          f'default-pack: {df_tot / total_bins:.1%}   (target {rs._INITIAL_FILL:.0%})')
    n = max(1, len(subset))
    print(f'Packing cost (bucket_requirements over {n:,} SKUs): '
          f'stock_plan spread {t_spread*1e3:.0f}ms ({t_spread/n*1e6:.1f}µs/SKU)  vs  '
          f'default-pack {t_default*1e3:.0f}ms ({t_default/n*1e6:.1f}µs/SKU)  '
          f'→ {t_default/max(t_spread,1e-9):.1f}× slower')


# ── runtime view ─────────────────────────────────────────────────────────────────

def _build_pick_cfg(cfg: dict) -> PickConfig:
    return PickConfig(
        num_pickers      = rs.K_PICKERS,
        x_speed          = cfg.get('x_speed', 4.0),
        y_speed          = cfg.get('y_speed', 2.0),
        pick_intercept   = cfg.get('pick_intercept', 1.0),
        pick_weight_coef = cfg.get('pick_weight_coef', 1.1),
        pick_volume_coef = cfg.get('pick_volume_coef', 1e-3),
        pick_weight_fn   = cfg.get('pick_weight_fn', 'log'),
        pick_volume_fn   = cfg.get('pick_volume_fn', 'log'),
        cart_swap_coef   = cfg.get('cart_swap_coef', 10.0),
        height_brackets  = cfg.get('height_brackets', DEFAULT_HEIGHT_BRACKETS),
    )


def _setup_strategy(mgr, strat, planned_inv, affinity, wp) -> None:
    """Mirror strategy_runner's uniform-stock arm: uniform initial stock, arm aisle
    state, then build() swaps in the strategy's (possibly ranked-wave) placement."""
    freq_by_sku = {c.sku: c.demand.relative_frequency    for c in planned_inv.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate for c in planned_inv.orders}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.relative_frequency
                   for c in planned_inv.orders if c.sku in affinity._sku_to_idx}
    ctx = StrategyContext(affinity=affinity, wp=wp, freq_by_idx=freq_by_idx,
                          freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku,
                          beta=1.0, orders=planned_inv.orders)
    mgr.enqueue_all(planned_inv.orders)              # uniform initial stock
    if strat.needs_affinity:
        mgr._affinity = affinity
        mgr.init_lift_state(affinity)
    if strat.needs_demand:
        mgr.init_demand_state(planned_inv, wp)
    if strat.uses_aisle_index:
        mgr.init_travel_costs(wp)
    strat.build(mgr, ctx)


def _diagnose_unplaced(mgr, warehouse, log) -> None:
    """For each queued (unplaced) unit, check whether free bins exist in its bucket —
    if so, the placement (not capacity) is the cause."""
    free_by_bucket: dict[Bucket, int] = defaultdict(int)
    for b in warehouse.bins:
        if b.storage is None:
            free_by_bucket[_bin_bucket(b)] += 1
    q_with_free: dict[Bucket, int] = defaultdict(int)
    q_no_free:   dict[Bucket, int] = defaultdict(int)
    for u in list(mgr._stock_queue):
        bk = _unit_bucket(u)
        (q_with_free if free_by_bucket.get(bk, 0) > 0 else q_no_free)[bk] += 1
    # Spill-up check: for queued PALLET units whose exact tier is full, do LARGER
    # pallet tiers (same handling/category) have free bins?  Per-unit placement spills
    # up there; the ranked wave (single-tier snapshot) does not.
    _ORDER = ['small', 'medium', 'large', 'extra_large']
    q_spill_up = 0
    spill_buckets: dict[Bucket, tuple[int, int]] = {}
    for u in list(mgr._stock_queue):
        bk = _unit_bucket(u)
        h, c, s, ut = bk
        if ut != 'pallet' or free_by_bucket.get(bk, 0) > 0 or s not in _ORDER:
            continue
        bigger = sum(free_by_bucket.get((h, c, bs, 'pallet'), 0)
                     for bs in _ORDER[_ORDER.index(s) + 1:])
        if bigger > 0:
            q_spill_up += 1
            cur = spill_buckets.get(bk, (0, 0))
            spill_buckets[bk] = (cur[0] + 1, bigger)

    tot_free = sum(q_with_free.values())
    tot_nofr = sum(q_no_free.values())
    print('\n' + '-' * 96)
    print('UNPLACED-UNIT DIAGNOSIS  (why queued units are not placed)')
    print('-' * 96)
    print(f'  queued units whose EXACT tier HAS free bins : {tot_free:,}   '
          f'<- placement failure (snapshot/grouping)')
    print(f'  queued units whose exact tier is FULL but a '
          f'LARGER pallet tier has room : {q_spill_up:,}   <- ranked wave cannot spill UP')
    print(f'  queued units with no exact-tier and no larger-tier room : '
          f'{tot_nofr - q_spill_up:,}   <- genuine capacity')
    if q_with_free:
        print('  worst exact-tier placement-failure buckets (queued / free in tier):')
        for bk in sorted(q_with_free, key=lambda k: -q_with_free[k])[:6]:
            print(f'    {_fmt_bucket(bk):<32} queued={q_with_free[bk]:>6}  free={free_by_bucket[bk]:>6}')
    if spill_buckets:
        print('  worst spill-up buckets (queued in full tier / free bins in larger tiers):')
        for bk in sorted(spill_buckets, key=lambda k: -spill_buckets[k][0])[:6]:
            qd, big = spill_buckets[bk]
            print(f'    {_fmt_bucket(bk):<32} queued={qd:>6}  larger-tier free={big:>6}')


def runtime_view(planned_inv, warehouse, affinity, batch_cfg, n_batches: int,
                 strategy: str | None, log) -> None:
    mgr = Inventory_Manager(warehouse)
    mgr._seed = rs.SEED_WORLD
    pick_cfg = _build_pick_cfg(rs.REGRESSION_CONFIGS[0] if getattr(rs, 'REGRESSION_CONFIGS', None) else {})
    strat = STRATEGY_BY_KEY.get(strategy) if strategy else None
    if strat is not None and strat.stock_mode == 'policy':
        log.warning(f'strategy {strategy} is policy-stocked; this probe only models uniform '
                    f'initial stock — results approximate.')
    if strat is not None:
        wp = WorkloadParams.from_pick_config(pick_cfg)
        _setup_strategy(mgr, strat, planned_inv, affinity, wp)
    else:
        mgr.enqueue_all(planned_inv.orders)        # plain FIFO uniform
    mgr.pop_churn()

    total_bins = len(warehouse.bins)
    tag = f'strategy={strategy} ({mgr.placement.name})' if strat else 'FIFO uniform'
    print('\n' + '=' * 96)
    print(f'RUNTIME VIEW  — {tag}, {n_batches} batches  ({total_bins:,} bins)')
    print('=' * 96)
    print(f'{"batch":>6}{"fill":>8}{"queue_u":>9}{"reorders":>9}')
    for i in range(n_batches):
        triggered = mgr.check_reorders()
        batch = Batch(batch_cfg, planned_inv, affinity=affinity,
                      rng=random.Random(rs.SEED_BATCHES + i))
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if tasks:
            DeferredPickSimulation(tasks, pick_cfg, manager=mgr).run()
        if i % max(1, n_batches // 10) == 0 or i == n_batches - 1:
            fill = len(mgr._unavailable) / total_bins
            print(f'{i+1:>6}{fill:>7.1%}{len(mgr._stock_queue):>9}{len(triggered):>9}')

    # Final per-bucket breakdown.
    cap = _capacity_by_bucket(warehouse)
    occ = _occupied_by_bucket(warehouse)
    q   = _queued_by_bucket(mgr)
    print('\n' + '-' * 96)
    print('FINAL per-bucket occupancy + queue  (flags: ! >95% full   . <40% full)')
    print('-' * 96)
    print(f'{"bucket":<32}{"cap":>8}{"occ":>8}{"fill%":>7}{"queued_u":>10}  flag')
    print('-' * 96)
    overflow = empty = 0
    for b in sorted(cap, key=lambda k: (-q.get(k, 0), -cap[k])):
        c, o, qd = cap[b], occ.get(b, 0), q.get(b, 0)
        pct = o / c if c else 0.0
        flag = '!' if pct > 0.95 else ('.' if pct < 0.40 else ' ')
        if pct > 0.95:
            overflow += 1
        if pct < 0.40:
            empty += 1
        print(f'{_fmt_bucket(b):<32}{c:>8}{o:>8}{pct:>6.0%}{qd:>10}  {flag}')
    print('-' * 96)
    occ_tot, q_tot = sum(occ.values()), sum(q.values())
    print(f'{"TOTAL":<32}{total_bins:>8}{occ_tot:>8}{occ_tot/total_bins:>6.0%}{q_tot:>10}')
    print(f'\nGlobal fill {occ_tot/total_bins:.1%}   queued units {q_tot:,}   '
          f'overflow buckets(>95%) {overflow}   wasted buckets(<40%) {empty}')
    if q_tot:
        _diagnose_unplaced(mgr, warehouse, log)


# ── driver ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('profile', nargs='?', default=None,
                    help='profile label, inventory .db path, or omit for latest in PROFILE_INPUT_DIR')
    ap.add_argument('--max-skus', type=int, default=3000)
    ap.add_argument('--batches', type=int, default=30)
    ap.add_argument('--max-bins', type=int, default=None)
    ap.add_argument('--min-bins', type=int, default=None)
    ap.add_argument('--strategy', default=None,
                    help="strategy key to replicate (e.g. uni_rank_popularity_norsl); "
                         "omit for plain FIFO uniform")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    log = logging.getLogger('bucket_fill')

    pairs = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)
    if not pairs:
        log.error(f'No DB pairs under {rs._DEFAULT_PROFILES_DIR}')
        sys.exit(1)
    if args.profile and os.path.isfile(args.profile):
        label, inv_db, aff_db = 'custom', args.profile, pairs[0][2]
    elif args.profile:
        match = [p for p in pairs if args.profile in p[0]]
        label, inv_db, aff_db = (match or pairs)[0]
    else:
        label, inv_db, aff_db = pairs[0]
    log.info(f'Profile: {label}\n  inv: {inv_db}\n  aff: {aff_db}'
             f'\n  max_skus={args.max_skus}  batches={args.batches}'
             f'  max_bins={args.max_bins}  min_bins={args.min_bins}')

    base = tempfile.mkdtemp(prefix='bucketfill_')
    shared = rs.build_shared_assets(
        inv_db, aff_db, log,
        max_skus=args.max_skus, max_bins=args.max_bins, min_bins=args.min_bins,
        composition=None,
        warehouse_db_path=os.path.join(base, label, 'warehouse.db'),
    )

    planned_inv = load_inventory_from_db(shared['planned_inv_db'])
    Aisle.next_aisle_id = 1
    random.seed(rs.SEED_WORLD)
    warehouse = Warehouse_Builder().from_config(shared['warehouse_cfg']).build()

    sizing_view(inv_db, shared.get('sku_allowlist') or set(),
                warehouse, planned_inv.orders, log)
    runtime_view(planned_inv, warehouse, shared['affinity_store'],
                 shared['batch_cfg'], args.batches, args.strategy, log)


if __name__ == '__main__':
    main()
