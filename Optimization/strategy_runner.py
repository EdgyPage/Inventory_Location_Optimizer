"""strategy_runner.py — concurrent strategy worker for run_comparison.py.

Separates all parallel/CPU machinery from the configuration, analysis, and
plotting logic in run_comparison.py.

Public API
----------
run_strategies_parallel(strategy_args, log) -> dict[str, dict]
    Launch A/B/C workers via ProcessPoolExecutor(3); stream log records to
    the main process in real time via a Manager().Queue + QueueListener.

save_worker_checkpoint(run_dir, strategy, next_batch_id)
load_worker_checkpoint(run_dir, strategy) -> int
    Per-strategy crash-recovery checkpoints written inside each worker.

Implementation notes
--------------------
_run_strategy_worker must be a module-level function so ProcessPoolExecutor
can pickle it by reference in Windows spawn mode.  Workers import this
module directly, so sys.path is initialised here to resolve Warehouse
imports without relying solely on the parent-process inheritance.
"""

from __future__ import annotations

import concurrent.futures
import logging
import logging.handlers
import multiprocessing
import os
import pickle
import random
import sys
import time

# ── path setup ────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_WAREHOUSE = os.path.normpath(os.path.join(_HERE, '..', 'Warehouse'))
for _p in (_WAREHOUSE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from Aisle_Storage import Aisle
from Storage_Primitive import viable_storage_units as _vsu

# Minimum empty bins to preserve per (handling, category, size, unit_type) bucket
# during overstock fill so reorder units always find a slot during simulation.
_OVERSTOCK_MIN_HEADROOM: int = 10
from Affinity_Store import AffinityStore
from fast_pick import DeferredPickSimulation
from generate_inventory import load_inventory_from_db
from Inventory_Management import Inventory_Manager
from Capacity_Reloader import RELOADERS
from strategies import STRATEGY_BY_KEY, StrategyContext
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, Task
from Simulation_Analytics import (
    extract_batch_stats, extract_task_stats, extract_picker_events, extract_picks,
    build_pre_snapshot, snapshot_bin_inventory, snapshot_aisle_metrics,
)
from Picking_Data import (
    save_batch_stats, save_task_stats, save_picker_events, save_picks,
    save_bin_inventory, save_aisle_metrics,
    keyframe_db_path, init_keyframe_db, save_bin_keyframe,
)


# ── checkpoint helpers ────────────────────────────────────────────────────────

def save_worker_checkpoint(run_dir: str, strategy: str, next_batch_id: int) -> None:
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    with open(path, 'wb') as f:
        pickle.dump({'next_batch_id': next_batch_id}, f)


def load_worker_checkpoint(run_dir: str, strategy: str) -> int:
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    if not os.path.exists(path):
        return 0
    with open(path, 'rb') as f:
        return pickle.load(f).get('next_batch_id', 0)


def _cleanup_checkpoints(run_dir: str) -> None:
    # checkpoints are per-strategy (_ckpt_<key>.pkl), so remove them all rather than
    # assuming the legacy A/B/C set.
    import glob
    for p in glob.glob(os.path.join(run_dir, '_ckpt_*.pkl')):
        try:
            os.remove(p)
        except OSError:
            pass


# ── strategy worker ───────────────────────────────────────────────────────────

def _run_strategy_worker(args: dict) -> dict:
    """Simulate one assignment strategy in its own process.

    Uses DeferredPickSimulation for parallel Phase-1 picker execution within
    each batch.  Log records travel through a multiprocessing.Queue to the
    QueueListener in the main process so they appear in real time.
    """
    # ── logging ───────────────────────────────────────────────────────────────
    log_queue = args['log_queue']
    root      = logging.getLogger()
    root.handlers = []
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.INFO)
    strategy  = args['strategy']
    job_index = args.get('job_index')
    job_total = args.get('job_total')
    job_tag   = args.get('job_tag')
    # Flat pool: tag every line with the job index so interleaved output is
    # distinguishable.  Nested path has no job_index → keep the old name.
    if job_index is not None:
        log = logging.getLogger(f'j{job_index}/{job_total} {strategy}')
    else:
        log = logging.getLogger(f'worker-{strategy}')

    # ── unpack ────────────────────────────────────────────────────────────────
    inv_db        = args['inv_db']
    aff_db        = args['aff_db']
    db_path       = args['db_path']
    run_dir       = args['run_dir']
    run_id        = args['run_id']
    start_i       = args['start_i']
    n_batches     = args['n_batches']
    k_pickers     = args['k_pickers']
    seed_world    = args['seed_world']
    seed_batches  = args['seed_batches']
    checkpoint    = args['checkpoint']
    max_skus      = args.get('max_skus')
    sku_allowlist = args.get('sku_allowlist')
    keyframe_interval = args.get('keyframe_interval') or 0
    warehouse_cfg = args['warehouse_cfg']
    pick_cfg      = args['pick_cfg']
    wp            = args['wp']
    load_params   = args['load_params']
    batch_cfg     = args['batch_cfg']

    log.info('=' * 60)
    if job_tag is not None:
        log.info(f'Job {job_index}/{job_total}  {job_tag}')
    log.info(f'Strategy {strategy}  run_id={run_id}  batches {start_i}->{n_batches}')
    log.info(f'  pick  w={pick_cfg.pick_weight_coef}  v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  cart={pick_cfg.cart_swap_coef}')
    log.info(f'  load  lambda={load_params.lambda_}  k={load_params.k}  gamma={load_params.gamma}')
    log.info(f'  seeds  world={seed_world}  batches={seed_batches}')
    log.info(f'  checkpoint_every={checkpoint}'
             + (f'  max_skus={max_skus:,}' if max_skus else ''))

    # ── inventory ─────────────────────────────────────────────────────────────
    log.info(f'Loading inventory: {inv_db}')
    t0        = time.perf_counter()
    inventory = load_inventory_from_db(inv_db, limit=max_skus)
    if sku_allowlist is not None:
        inventory.cartons = [c for c in inventory.cartons if c.sku in sku_allowlist]
    n_skus    = len(inventory.cartons)
    log.info(f'  {n_skus:,} SKUs  ({time.perf_counter()-t0:.2f}s)')

    # ── affinity ──────────────────────────────────────────────────────────────
    log.info(f'Loading affinity: {aff_db}')
    t0       = time.perf_counter()
    affinity = AffinityStore(aff_db)
    n_aff    = affinity._matrix.nnz if affinity._matrix is not None else 0
    mb       = 0.0 if affinity._matrix is None else (
        affinity._matrix.data.nbytes + affinity._matrix.indices.nbytes +
        affinity._matrix.indptr.nbytes) / 1_048_576
    log.info(f'  {n_aff:,} entries  {mb:.0f} MB  ({time.perf_counter()-t0:.1f}s)')

    # ── strategy + frequency maps (needed BEFORE stocking for custom layouts) ──
    # freq_by_sku ranks SKUs for the optimal stock layout and for re-slotting; built
    # for every strategy (cheap) since these don't depend on placement.
    strat = STRATEGY_BY_KEY[strategy]
    freq_by_sku = {c.sku: c.demand.frequency     for c in inventory.cartons}
    qty_by_sku  = {c.sku: c.demand.quantity_rate  for c in inventory.cartons}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.frequency
                   for c in inventory.cartons if c.sku in affinity._sku_to_idx}
    ctx = StrategyContext(
        affinity=affinity, wp=wp,
        freq_by_idx=freq_by_idx, freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku,
        beta=1.0)

    # ── warehouse ─────────────────────────────────────────────────────────────
    log.info(f'Building warehouse: {warehouse_cfg.total_aisles} aisles...')
    t0 = time.perf_counter()
    Aisle.next_aisle_id = 1
    random.seed(seed_world)
    warehouse  = Warehouse_Builder().from_config(warehouse_cfg).build()
    total_bins = len(warehouse.bins)   # density-aware: actual count after physical expansion
    log.info(f'  Built {total_bins:,} bins  ({time.perf_counter()-t0:.1f}s)')

    # ── initial stock: uniform by default, or the strategy's custom layout ──────
    # Most strategies start from the same uniform placement so batch differences
    # reflect only the reorder/re-slot behaviour.  A strategy may override stocking
    # with strat.stock (e.g. optimal_reslot stocks at the pure-global-W optimum).
    # affinity=None here keeps stocking placement affinity-agnostic; _aisle_sku_counts
    # is rebuilt below for affinity-needing strategies regardless of how we stocked.
    t0 = time.perf_counter()
    random.seed(seed_world + 100)
    mgr = Inventory_Manager(warehouse, affinity=None)
    if strat.stock is not None:
        log.info(f'Initial stock: {n_skus:,} SKUs  custom layout ({strat.key})...')
        strat.stock(mgr, ctx, inventory)
    else:
        log.info(f'Initial stock: {n_skus:,} SKUs  uniform placement...')
        mgr.enqueue_all(inventory.cartons)   # quantity read from carton.equilibrium_qty
    base_filled = len(mgr._unavailable)
    log.info(f'  {base_filled:,} / {len(warehouse.bins):,} bins filled  '
             f'({base_filled / len(warehouse.bins):.1%})  ({time.perf_counter()-t0:.1f}s)')

    # ── enable affinity/demand state and set assignment fns (per registry) ────
    # Strategies that need it now activate affinity/demand tracking so reorders can
    # use the trip-cost / co-occurrence objective; the strategy's build() then wires
    # the per-unit assignment_fn and (optional) ranked_assignment_fn.  The aisle state
    # is rebuilt over whatever initial placement we just made (uniform or optimal).
    if strat.needs_affinity:
        # Activate the affinity reference so _drain and _reclaim_empty_bins
        # update lift/count state during the simulation loop.
        mgr._affinity = affinity

        # Rebuild the FULL per-aisle affinity state from the placed bins via
        # init_lift_state — this populates _aisle_sku_sets AND _aisle_idx_sets
        # (plus counts, bin_sku, current_quantities, lift sums).  Those sets are
        # what the trip/cluster assignment fns read to score co-occurrence, so the
        # placement is aware of the INITIAL stock (uniform or optimal), not only of
        # SKUs that later reorders happen to place.  (A prior partial rebuild filled
        # only _aisle_sku_counts, leaving the sets empty → affinity blind to stock.)
        log.info('Rebuilding aisle affinity state from placed bins (init_lift_state)...')
        t0 = time.perf_counter()
        mgr.init_lift_state(affinity)
        n_lift = sum(1 for v in mgr._aisle_lift_sum.values() if v > 0)
        log.info(f'  {len(mgr._aisle_sku_sets)} aisles  {n_lift} with lift>0  '
                 f'({time.perf_counter()-t0:.1f}s)')

    if strat.needs_demand:
        log.info('Building demand state...')
        t0 = time.perf_counter()
        mgr.init_demand_state(inventory)
        log.info(f'  {len(mgr._aisle_demand_sum)} aisles populated  '
                 f'({time.perf_counter()-t0:.2f}s)')

    # Per-unit cluster strategies consume the pre-sorted per-aisle index (the Fix-1
    # fast path): arm it BEFORE build() so the cluster fn captures a populated
    # mgr._aisle_index and _drain passes candidates=None.  The _drain coupling guard
    # asserts _travel_costs_ready matches assignment_fn.uses_aisle_index, so this
    # arming can never silently diverge from how the fn was built.  Ranked (tmin/tmax/
    # rank) and FIFO strategies leave uses_aisle_index=False and stay on the scan path.
    if strat.uses_aisle_index:
        log.info('Arming per-aisle travel-cost index (init_travel_costs)...')
        t0 = time.perf_counter()
        mgr.init_travel_costs(wp)
        log.info(f'  _aisle_index built for {sum(len(v) for v in mgr._aisle_index.values())} '
                 f'bin-key buckets  ({time.perf_counter()-t0:.2f}s)')

    strat.build(mgr, ctx)
    log.info(f'  strategy={strat.key} ({strat.label})  '
             f'ranked_assignment_fn={"set" if mgr.ranked_assignment_fn else "None (FIFO)"}')

    # ── capacity reloader: evict-and-requeue re-slot, budget = % of an XL pallet
    # aisle's bin capacity.  The named variant comes from the strategy (default
    # 'rebalance'); its re-placement fns are the manager's reorder fns, since the
    # post-eviction ranked drain (in check_reorders) uses those.
    reloader = None
    if strat.reslot_frac > 0:
        reloader = RELOADERS[getattr(strat, 'reloader', 'rebalance') or 'rebalance'](
            assignment_fn=mgr.assignment_fn,
            ranked_assignment_fn=mgr.ranked_assignment_fn,
            move_limit_pct=strat.reslot_frac)
        cap = reloader.per_aisle_cap(warehouse)
        log.info(f'  reloader={reloader.name}  cap={cap} evictions/pallet-aisle/batch '
                 f'(={strat.reslot_frac:.3%} of XL-aisle bins)')

    # Discard initial-stock placement churn so batch-0 churn reflects only the loop.
    mgr.pop_churn()
    opt_x, opt_y = wp.x_speed, wp.y_speed   # speeds for sigma_fd / reload targeting
    # Seed the incremental Sigma f*D tracker once; per-batch reads are then O(1)
    # (maintained on placement/eviction/pick-empty) instead of a full bin scan.
    mgr.enable_sigma_fd(freq_by_sku, opt_x, opt_y)

    # ── RNG fast-forward (resume only) ────────────────────────────────────────
    random.seed(seed_batches)
    if start_i > 0:
        log.info(f'Fast-forwarding RNG through {start_i} batches...')
        t0 = time.perf_counter()
        for _ in range(start_i):
            Batch(batch_cfg, inventory, affinity=affinity)
        log.info(f'  RNG at batch {start_i}  ({time.perf_counter()-t0:.1f}s)')

    # ── keyframe DB (full bin snapshot every keyframe_interval batches) ───────
    kf_db = None
    if keyframe_interval > 0:
        kf_db = keyframe_db_path(db_path)
        init_keyframe_db(kf_db)
        log.info(f'  Keyframes every {keyframe_interval} batches → {kf_db}')

    # ── simulation loop ───────────────────────────────────────────────────────
    log.info(f'Simulation loop [DeferredPickSimulation + ThreadPoolExecutor]: '
             f'batches {start_i} -> {n_batches}')
    pb: list = []
    pt: list = []
    pe: list = []
    pk: list = []   # individual pick records
    pi: list = []   # bin inventory snapshots
    pm: list = []   # aisle metrics snapshots
    lift_cache: dict = {}   # memoize sum_lift(frozenset(task_skus)) across batches (O(k^2)/task)
    skipped        = 0
    reorders_ckpt  = 0
    dur_sum_ckpt   = 0.0
    dur_count_ckpt = 0
    p1_sum_ckpt    = 0.0
    p2_sum_ckpt    = 0.0
    last_dur       = 0.0
    t_loop         = time.perf_counter()
    t_ckpt         = time.perf_counter()

    for i in range(start_i, n_batches):
        if reloader is not None:
            # Evict targeted pallets into the queue; check_reorders' ranked drain
            # (below) re-places them + reorders in priority order.
            reloader.reload(mgr, freq_by_sku, opt_x, opt_y)
        triggered      = mgr.check_reorders()
        reorders_ckpt += len(triggered)
        # Layout-quality snapshot AFTER re-slot + reorder, BEFORE this batch's picks.
        batch_rm, batch_rp = mgr.pop_churn()
        batch_sigma        = mgr.tracked_sigma_fd()    # O(1) incremental (see enable_sigma_fd)

        batch    = Batch(batch_cfg, inventory, affinity=affinity)
        tasks    = Task.from_batch(batch, warehouse, manager=mgr)
        pre_snap = build_pre_snapshot(mgr)                         # bin qtys before picks
        am       = snapshot_aisle_metrics(mgr, batch_id=i, run_id=run_id)  # aisle state

        # Keyframe: full occupied-bin state at this batch's start (after reorders),
        # written every keyframe_interval batches so the player can jump here
        # without replaying deltas from batch 0.  Reuses the pre_snap already built.
        if kf_db is not None and i % keyframe_interval == 0:
            save_bin_keyframe(kf_db, run_id, i, [
                {'aisle_id': v['aisle_id'], 'bayX': v['bayX'], 'bayY': v['bayY'],
                 'sku': v['sku'], 'unit_type': v['unit_type'],
                 'storage_size': v['storage_size'], 'qty': v['pre_qty']}
                for v in pre_snap.values()
            ])

        if not tasks:
            skipped += 1
            continue

        sim             = DeferredPickSimulation(tasks, pick_cfg, manager=mgr)
        events          = sim.run()
        p1_sum_ckpt    += sim.phase1_time
        p2_sum_ckpt    += sim.phase2_time

        bs  = extract_batch_stats(events, batch_id=i, k_pickers=k_pickers, run_id=run_id)
        bs.sigma_fd           = batch_sigma
        bs.reload_moves       = batch_rm
        bs.reorder_placements = batch_rp
        ts  = extract_task_stats(events, tasks, batch_id=i, affinity=affinity, wp=wp,
                                 run_id=run_id, lift_cache=lift_cache)
        pev = extract_picker_events(events, batch_id=i, run_id=run_id)
        picks_b = extract_picks(events, batch_id=i, run_id=run_id)
        inv = snapshot_bin_inventory(mgr, pre_snap, batch_id=i, run_id=run_id,
                                     full_snapshot=(i == start_i))
        pb.append(bs)
        pt.extend(ts)
        pe.extend(pev)
        pk.extend(picks_b)
        pi.extend(inv)
        pm.extend(am)
        last_dur        = bs.duration
        dur_sum_ckpt   += bs.duration
        dur_count_ckpt += 1

        if len(pb) >= checkpoint:
            t_s0 = time.perf_counter()
            save_batch_stats(db_path, run_id, pb)
            save_task_stats(db_path, run_id, pt)
            save_picker_events(db_path, run_id, pe)
            save_picks(db_path, run_id, pk)
            save_bin_inventory(db_path, run_id, pi)
            save_aisle_metrics(db_path, run_id, pm)
            save_worker_checkpoint(run_dir, strategy, i + 1)
            t_save = time.perf_counter() - t_s0

            wall      = time.perf_counter() - t_loop
            ckpt_wall = time.perf_counter() - t_ckpt
            cum_rate  = (i + 1 - start_i) / wall
            ckpt_rate = dur_count_ckpt / ckpt_wall
            avg_dur   = dur_sum_ckpt / dur_count_ckpt if dur_count_ckpt else 0.0
            cur_fill  = len(mgr._unavailable) / len(warehouse.bins)
            p1_frac   = p1_sum_ckpt / (p1_sum_ckpt + p2_sum_ckpt + 1e-9) * 100

            log.info(
                f'  Batch {i+1:4d}/{n_batches}'
                f'  dur={bs.duration:6.0f}'
                f'  avg={avg_dur:6.0f}'
                f'  rate={ckpt_rate:.2f}/s ({cum_rate:.2f} cum)'
                f'  fill={cur_fill:.1%}'
                f'  q={mgr.queue_depth}'
                f'  reorders={reorders_ckpt}'
                f'  p1={p1_sum_ckpt:.2f}s ({p1_frac:.0f}%)'
                f'  p2={p2_sum_ckpt:.2f}s'
                f'  wall={wall:.0f}s'
                f'  db={t_save:.2f}s'
            )

            pb.clear(); pt.clear(); pe.clear(); pk.clear(); pi.clear(); pm.clear()
            reorders_ckpt  = 0
            dur_sum_ckpt   = 0.0
            dur_count_ckpt = 0
            p1_sum_ckpt    = 0.0
            p2_sum_ckpt    = 0.0
            t_ckpt         = time.perf_counter()

    if pb:
        log.info(f'  Flushing final {len(pb)} batches to DB...')
        save_batch_stats(db_path, run_id, pb)
        save_task_stats(db_path, run_id, pt)
        save_picker_events(db_path, run_id, pe)
        save_picks(db_path, run_id, pk)
        save_bin_inventory(db_path, run_id, pi)
        save_aisle_metrics(db_path, run_id, pm)

    elapsed = time.perf_counter() - t_loop
    done    = n_batches - start_i - skipped
    log.info('=' * 60)
    log.info(f'Strategy {strategy} DONE  batches={done}  skipped={skipped}  '
             f'wall={elapsed:.1f}s  rate={done/elapsed:.2f}/s  last_dur={last_dur:.0f}')
    log.info('=' * 60)

    return {
        'strategy': strategy,
        'run_id'  : run_id,
        'elapsed' : elapsed,
        'done'    : done,
        'skipped' : skipped,
        'last_dur': last_dur,
    }


# ── parallel dispatcher ────────────────────────────────────────────────────────

def run_strategies_parallel(
    strategy_args: list[dict],
    log          : logging.Logger,
) -> dict[str, dict]:
    """Run A/B/C workers in parallel via ProcessPoolExecutor(3).

    Log records from worker processes are forwarded in real time through a
    multiprocessing.Manager().Queue (picklable proxy) to a QueueListener
    in the main process, writing to the shared log file and stdout.
    Raises the first worker exception encountered via future.result().
    """
    mp_manager = multiprocessing.Manager()
    log_queue  = mp_manager.Queue(-1)
    listener   = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True
    )
    listener.start()
    log.info('  Log listener started — worker logs appear in real time')

    args_with_queue = [{**a, 'log_queue': log_queue} for a in strategy_args]
    results: dict[str, dict] = {}
    t_wall  = time.perf_counter()

    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_run_strategy_worker, a): a['strategy']
                for a in args_with_queue
            }
            for future in concurrent.futures.as_completed(futures):
                s   = futures[future]
                res = future.result()
                results[s] = res
                log.info(
                    f'  Worker {s} returned  done={res["done"]}  '
                    f'skipped={res["skipped"]}  wall={res["elapsed"]:.1f}s  '
                    f'last_dur={res["last_dur"]:.0f}'
                )
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')

    log.info(f'  All workers done  wall={time.perf_counter()-t_wall:.1f}s')

    if results:
        _cleanup_checkpoints(strategy_args[0]['run_dir'])

    return results
