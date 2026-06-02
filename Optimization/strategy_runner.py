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
from Affinity_Store import AffinityStore
from fast_pick import DeferredPickSimulation
from generate_inventory import load_inventory_from_db
from Inventory_Management import (
    Inventory_Manager,
    build_trip_minimizing_assignment_fn,
    build_trip_maximizing_assignment_fn,
)
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, Task
from Simulation_Analytics import extract_batch_stats, extract_task_stats
from Picking_Data import save_batch_stats, save_task_stats


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
    for s in ('A', 'B', 'C'):
        p = os.path.join(run_dir, f'_ckpt_{s}.pkl')
        if os.path.exists(p):
            os.remove(p)


# ── overstock helper ───────────────────────────────────────────────────────────

def _stock_to_target_fill(manager, inventory, target: float) -> int:
    """Demand-weighted overstock until *manager* reaches *target* fill rate.

    Loops up to 20 rounds, clearing the queue between each round so that
    cartons incompatible with remaining bins don't block subsequent draws.
    Each round samples proportionally to demand.frequency so fast-moving
    SKUs accumulate extra bin locations first.
    """
    total_bins  = len(manager.warehouse.bins)
    target_bins = round(target * total_bins)
    weights = [c.demand.frequency for c in inventory.cartons]
    total_w = sum(weights)
    norm_w  = [w / total_w for w in weights]
    added   = 0

    for _ in range(20):
        current = len(manager.unavailable)
        if current >= target_bins:
            break
        needed = target_bins - current
        sample = random.choices(inventory.cartons, weights=norm_w, k=needed * 3)
        before = len(manager.unavailable)
        manager.enqueue_all(sample, quantity=1)
        manager._queue.clear()
        added += len(manager.unavailable) - before
        if len(manager.unavailable) == before:
            break

    return added


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
    strategy = args['strategy']
    log      = logging.getLogger(f'worker-{strategy}')

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
    target_fill   = args['target_fill']
    warehouse_cfg = args['warehouse_cfg']
    pick_cfg      = args['pick_cfg']
    wp            = args['wp']
    load_params   = args['load_params']
    batch_cfg     = args['batch_cfg']

    log.info('=' * 60)
    log.info(f'Strategy {strategy}  run_id={run_id}  batches {start_i}->{n_batches}')
    log.info(f'  pick  w={pick_cfg.pick_weight_coef}  v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  cart={pick_cfg.cart_swap_coef}')
    log.info(f'  load  lambda={load_params.lambda_}  k={load_params.k}  gamma={load_params.gamma}')
    log.info(f'  seeds  world={seed_world}  batches={seed_batches}')
    log.info(f'  target_fill={target_fill:.0%}  checkpoint_every={checkpoint}')

    # ── inventory ─────────────────────────────────────────────────────────────
    log.info(f'Loading inventory: {inv_db}')
    t0        = time.perf_counter()
    inventory = load_inventory_from_db(inv_db)
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

    # ── warehouse ─────────────────────────────────────────────────────────────
    total_bins = warehouse_cfg.total_aisles * 500
    log.info(f'Building warehouse: {warehouse_cfg.total_aisles} aisles / {total_bins:,} bins...')
    t0 = time.perf_counter()
    Aisle.next_aisle_id = 1
    random.seed(seed_world)
    warehouse = Warehouse_Builder().from_config(warehouse_cfg).build()
    log.info(f'  Built  ({time.perf_counter()-t0:.1f}s)')

    # ── initial stock (uniform, same for A/B/C) ───────────────────────────────
    log.info(f'Initial stock: {n_skus:,} SKUs -> 1 bin each...')
    t0 = time.perf_counter()
    random.seed(seed_world + 100)
    mgr = Inventory_Manager(warehouse, affinity=(affinity if strategy != 'A' else None))
    mgr.enqueue_all(inventory.cartons, quantity=1)
    base_filled = len(mgr.unavailable)
    log.info(f'  {base_filled:,} / {len(warehouse.bins):,} bins filled  '
             f'({base_filled / len(warehouse.bins):.1%})  ({time.perf_counter()-t0:.1f}s)')

    # ── dense fill to target ───────────────────────────────────────────────────
    log.info(f'Overstocking to {target_fill:.0%} fill...')
    t0    = time.perf_counter()
    added = _stock_to_target_fill(mgr, inventory, target=target_fill)
    post_fill = len(mgr.unavailable) / len(warehouse.bins)
    log.info(f'  +{added:,} bins  ->  {len(mgr.unavailable):,} filled  '
             f'({post_fill:.1%})  ({time.perf_counter()-t0:.1f}s)')

    # ── lift + demand state and assignment function (B / C only) ──────────────
    freq_by_sku: dict[int, float] = {}
    qty_by_sku:  dict[int, float] = {}
    freq_by_idx: dict[int, float] = {}

    if strategy in ('B', 'C'):
        log.info('Building lift state...')
        t0 = time.perf_counter()
        mgr.init_lift_state(affinity)
        n_lift = sum(1 for v in mgr._aisle_lift_sum.values() if v > 0)
        log.info(f'  {len(mgr._aisle_lift_sum)} aisles  {n_lift} with lift>0  '
                 f'({time.perf_counter()-t0:.1f}s)')

        log.info('Building demand state...')
        t0 = time.perf_counter()
        mgr.init_demand_state(inventory)
        log.info(f'  {len(mgr._aisle_demand_sum)} aisles populated  '
                 f'({time.perf_counter()-t0:.2f}s)')

        freq_by_sku = {c.sku: c.demand.frequency     for c in inventory.cartons}
        qty_by_sku  = {c.sku: c.demand.quantity_rate  for c in inventory.cartons}
        freq_by_idx = {
            affinity._sku_to_idx[c.sku]: c.demand.frequency
            for c in inventory.cartons if c.sku in affinity._sku_to_idx
        }

    if strategy == 'B':
        mgr.assignment_fn = build_trip_minimizing_assignment_fn(
            affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0)
        log.info('  assignment_fn = trip_minimizing')
    elif strategy == 'C':
        mgr.assignment_fn = build_trip_maximizing_assignment_fn(
            affinity, wp,
            mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
            freq_by_idx, freq_by_sku, qty_by_sku, beta=1.0)
        log.info('  assignment_fn = trip_maximizing')
    else:
        log.info('  assignment_fn = uniform_random')

    # ── RNG fast-forward (resume only) ────────────────────────────────────────
    random.seed(seed_batches)
    if start_i > 0:
        log.info(f'Fast-forwarding RNG through {start_i} batches...')
        t0 = time.perf_counter()
        for _ in range(start_i):
            Batch(batch_cfg, inventory, affinity=affinity)
        log.info(f'  RNG at batch {start_i}  ({time.perf_counter()-t0:.1f}s)')

    # ── simulation loop ───────────────────────────────────────────────────────
    log.info(f'Simulation loop [DeferredPickSimulation + ThreadPoolExecutor]: '
             f'batches {start_i} -> {n_batches}')
    pb: list = []
    pt: list = []
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
        triggered      = mgr.check_reorders()
        reorders_ckpt += len(triggered)

        batch = Batch(batch_cfg, inventory, affinity=affinity)
        tasks = Task.from_batch(batch, warehouse, manager=mgr)
        if not tasks:
            skipped += 1
            continue

        sim             = DeferredPickSimulation(tasks, pick_cfg, manager=mgr)
        events          = sim.run()
        p1_sum_ckpt    += sim.phase1_time
        p2_sum_ckpt    += sim.phase2_time

        bs = extract_batch_stats(events, batch_id=i, k_pickers=k_pickers, run_id=run_id)
        ts = extract_task_stats(events, tasks, batch_id=i, affinity=affinity, wp=wp, run_id=run_id)
        pb.append(bs)
        pt.extend(ts)
        last_dur        = bs.duration
        dur_sum_ckpt   += bs.duration
        dur_count_ckpt += 1

        if len(pb) >= checkpoint:
            t_s0 = time.perf_counter()
            save_batch_stats(db_path, run_id, pb)
            save_task_stats(db_path, run_id, pt)
            save_worker_checkpoint(run_dir, strategy, i + 1)
            t_save = time.perf_counter() - t_s0

            wall      = time.perf_counter() - t_loop
            ckpt_wall = time.perf_counter() - t_ckpt
            cum_rate  = (i + 1 - start_i) / wall
            ckpt_rate = dur_count_ckpt / ckpt_wall
            avg_dur   = dur_sum_ckpt / dur_count_ckpt if dur_count_ckpt else 0.0
            cur_fill  = len(mgr.unavailable) / len(warehouse.bins)
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

            pb.clear(); pt.clear()
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
