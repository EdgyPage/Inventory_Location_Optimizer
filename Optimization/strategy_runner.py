"""strategy_runner.py — concurrent strategy worker for run_simulation.py.

Separates all parallel/CPU machinery from the configuration, analysis, and
plotting logic in run_simulation.py.

Public API
----------
_run_strategy_worker(args) -> dict
    Simulate one assignment strategy end-to-end (one process in the flat pool
    owned by run_simulation._run_workers_flat).

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

import logging
import logging.handlers
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
from generation.generate_inventory import load_inventory_from_db
from Inventory_Management import Inventory_Manager
from Capacity_Reloader import RELOADERS
from strategies import STRATEGY_BY_KEY, StrategyContext
from Warehouse_Builder import Warehouse_Builder
from Workload_Builder import Batch, Task
from batch_precompute import load_batches, batch_fingerprint
from Simulation_Analytics import (
    extract_batch_stats, extract_task_stats, extract_picker_events, extract_picks,
    build_pre_snapshot, snapshot_bin_inventory, snapshot_aisle_metrics,
)
from Picking_Data import (
    save_batch_stats, save_task_stats, save_picker_events, save_picks,
    save_bin_inventory, save_aisle_metrics, save_reorder_queue,
    save_bin_scores, save_sku_scores,
    keyframe_db_path, init_keyframe_db, save_bin_keyframe,
)
from cost_model import sec_per_inch, height_multiplier


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
    batches_path        = args.get('batches_path')
    batches_fingerprint = args.get('batches_fingerprint')

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
        inventory.orders = [c for c in inventory.orders if c.sku in sku_allowlist]
    n_skus    = len(inventory.orders)
    log.info(f'  {n_skus:,} SKUs  ({time.perf_counter()-t0:.2f}s)')

    # Precompute per-unit labor cost once per worker (config-dependent: uses this run's
    # pick coefficients).  expected_popularity/expected_labor are Order properties that
    # derive from this + demand, so the hot ranked-wave order/balance never re-takes logs.
    for c in inventory.orders:
        c.compute_labor_cost(wp.pick_intercept, wp.pick_weight_coef, wp.pick_volume_coef,
                             wp.pick_weight_fn, wp.pick_volume_fn)

    # ── affinity ──────────────────────────────────────────────────────────────
    log.info(f'Loading affinity: {aff_db}')
    t0       = time.perf_counter()
    affinity = AffinityStore(aff_db)
    n_aff    = affinity._matrix.nnz if affinity._matrix is not None else 0
    mb       = 0.0 if affinity._matrix is None else (
        affinity._matrix.data.nbytes + affinity._matrix.indices.nbytes +
        affinity._matrix.indptr.nbytes) / 1_048_576
    log.info(f'  {n_aff:,} entries  {mb:.0f} MB  ({time.perf_counter()-t0:.1f}s)')

    # ── shared precomputed batch sequence (dedup of sampling across arms) ───────
    # The parent precomputed this family's batch list once (a pure function of inv+aff+batch_cfg+seed).
    # Verify it was built for THIS worker's exact inputs by recomputing the fingerprint from our own
    # inventory+affinity; on any miss/mismatch, leave batches=None and sample inline in the loop
    # (bit-identical result — just not deduplicated).
    batches = None
    if batches_path and batches_fingerprint:
        try:
            own_fp = batch_fingerprint(inventory, batch_cfg, seed_batches, n_batches, affinity)
            if own_fp != batches_fingerprint:
                log.warning('  precomputed-batch fingerprint mismatch -> sampling inline')
            else:
                batches = load_batches(batches_path, batches_fingerprint)
                if batches is not None and len(batches) < n_batches:
                    batches = None                       # short list (shouldn't happen) -> inline
        except Exception as exc:                          # noqa: BLE001 — never block the run
            log.warning(f'  precomputed-batch load failed ({exc!r}) -> sampling inline')
    log.info(f'  batches: {"precomputed/shared" if batches is not None else "inline sampling"}')

    # ── strategy + frequency maps (needed BEFORE stocking for custom layouts) ──
    # freq_by_sku ranks SKUs for the optimal stock layout and for re-slotting; built
    # for every strategy (cheap) since these don't depend on placement.
    strat = STRATEGY_BY_KEY[strategy]
    freq_by_sku = {c.sku: c.demand.relative_frequency     for c in inventory.orders}
    qty_by_sku  = {c.sku: c.demand.quantity_rate  for c in inventory.orders}
    freq_by_idx = {affinity._sku_to_idx[c.sku]: c.demand.relative_frequency
                   for c in inventory.orders if c.sku in affinity._sku_to_idx}
    ctx = StrategyContext(
        affinity=affinity, wp=wp,
        freq_by_idx=freq_by_idx, freq_by_sku=freq_by_sku, qty_by_sku=qty_by_sku,
        beta=1.0, orders=inventory.orders)

    # ── warehouse ─────────────────────────────────────────────────────────────
    log.info(f'Building warehouse: {warehouse_cfg.total_aisles} aisles...')
    t0 = time.perf_counter()
    Aisle.next_aisle_id = 1
    random.seed(seed_world)
    warehouse  = Warehouse_Builder().from_config(warehouse_cfg).build()
    total_bins = len(warehouse.bins)   # density-aware: actual count after physical expansion
    log.info(f'  Built {total_bins:,} bins  ({time.perf_counter()-t0:.1f}s)')

    # ── initial stock ───────────────────────────────────────────────────────────
    # stock_mode='uniform' (uni_*): random fill via the manager's default placement,
    #   THEN arm aisle state (init_lift_state/init_demand_state[/init_travel_costs])
    #   over that layout, THEN build() the reorder placement.
    # stock_mode='policy' (opt_*): arm per-SKU maps + travel index and build() the
    #   placement FIRST, then fill the whole inventory THROUGH that policy so the
    #   warehouse starts at the strategy's own ideal layout, then rebuild authoritative
    #   aisle state.  init_lift_state/init_demand_state clear their dicts in place, so
    #   the references build() captured stay valid across the post-stock rebuild.
    t0 = time.perf_counter()
    random.seed(seed_world + 100)
    mgr = Inventory_Manager(warehouse, affinity=None)
    mgr._seed = seed_world   # keys the reorder-qty noise (deterministic, off the global stream)

    def _arm_aisle_state() -> None:
        """Rebuild per-aisle affinity + demand/labor state from the placed bins."""
        if strat.needs_affinity:
            mgr._affinity = affinity   # enable incremental lift/count maintenance
            mgr.init_lift_state(affinity)
        if strat.needs_demand:
            mgr.init_demand_state(inventory, wp)   # wp ⇒ also seed the labor twin

    if strat.stock_mode == 'policy':
        log.info(f'Initial stock: {n_skus:,} SKUs  via own policy ({strat.key})...')
        # Per-SKU products must exist before placement (the labor wave reads
        # _sku_pick_load_product); aisle sums seed to 0 over the empty warehouse and
        # accumulate incrementally as the policy places.
        if strat.needs_affinity:
            mgr._affinity = affinity
        if strat.needs_demand:
            mgr.init_demand_state(inventory, wp)
        if strat.uses_aisle_index:
            mgr.init_travel_costs(wp)   # NOTE(cluster): _aisle_index is maintained
                                        # incrementally by _index_add/remove during the fill
        strat.build(mgr, ctx)
        mgr.enqueue_all(inventory.orders)   # placed by the strategy's own policy
        _arm_aisle_state()                   # authoritative rebuild over the final layout
    else:
        log.info(f'Initial stock: {n_skus:,} SKUs  uniform placement...')
        mgr.enqueue_all(inventory.orders)   # quantity read from order.equilibrium_qty
        _arm_aisle_state()
        if strat.uses_aisle_index:
            mgr.init_travel_costs(wp)
        strat.build(mgr, ctx)

    base_filled = len(mgr._unavailable)
    log.info(f'  {base_filled:,} / {len(warehouse.bins):,} bins filled  '
             f'({base_filled / len(warehouse.bins):.1%})  ({time.perf_counter()-t0:.1f}s)')
    log.info(f'  strategy={strat.key} ({strat.label})  placement={mgr.placement.name}'
             f'{" (ranked)" if mgr.placement.is_ranked else ""}'
             f'  stock={strat.stock_mode}')

    # ── capacity reloader: evict-and-requeue re-slot, budget = % of an XL pallet
    # aisle's bin capacity.  The named variant comes from the strategy (default
    # 'rebalance'); re-placement is the manager's own placement policy — the
    # post-eviction drain (in check_reorders) uses mgr.placement.
    reloader = None
    if strat.reslot_frac > 0:
        reloader = RELOADERS[getattr(strat, 'reloader', 'rebalance') or 'rebalance'](
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

    # ── static per-run scores (saved once, before the loop) ────────────────────
    # Geometry/config-fixed scores the assignment functions compute: the viewer reads
    # these instead of recomputing.  bin layout score = travel D + golden-zone height;
    # map_pref/_map_target only exist for the optimal-map arms (else NULL/absent).
    if start_i == 0:
        _xp, _yp = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)
        _brk     = getattr(wp, 'height_brackets', ())
        _pref    = mgr._bin_pref            # {} unless this is a map/map_rank arm
        bin_rows = []
        for _b in warehouse.bins:
            _d = _xp * _b.x_phys + _yp * _b.y_phys
            _m = height_multiplier(_brk, _b.y_phys)
            bin_rows.append((_b.location[0], _b.bayX, _b.bayY,
                             _d, _m, _d + _m, _pref.get(id(_b))))
        save_bin_scores(db_path, run_id, bin_rows)
        _tgt = mgr._map_target              # {} unless this is a map/map_rank arm
        sku_rows = [
            (c.sku, _tgt.get(c.sku), c.labor_cost, c.handle_var,
             c.expected_popularity, c.expected_labor,
             getattr(c, 'equilibrium_qty', 1), getattr(c, 'reorder_point', 1),
             getattr(c, 'lead_time_mean', 0.0))
            for c in inventory.orders
        ]
        save_sku_scores(db_path, run_id, sku_rows)
        log.info(f'  Saved scores: {len(bin_rows):,} bins, {len(sku_rows):,} SKUs'
                 + ('  (incl. optimal-map pref/target)' if _pref else ''))

    # ── RNG streams ───────────────────────────────────────────────────────────
    # Batches use a dedicated per-batch stream seeded `seed_batches + i` (built in the
    # loop below), so batch i is identical across arms and resume needs no fast-forward.
    # The global `random` here drives only the loop's placement (group C) and reorder
    # noise is keyed separately (mgr._seed); seed it from seed_world for per-arm
    # reproducibility, keeping it independent of the batch stream.
    random.seed(seed_world + 200)
    if start_i > 0:
        log.info(f'Resuming at batch {start_i} (per-batch RNG seed; no fast-forward needed)')

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
    pq: list = []   # reorder-queue contents per batch (lead + stock), for the replay viewer
    lift_cache: dict = {}   # memoize sum_lift(frozenset(task_skus)) across batches (O(k^2)/task)
    skipped        = 0
    reorders_ckpt  = 0
    dur_sum_ckpt   = 0.0
    dur_count_ckpt = 0
    p1_sum_ckpt    = 0.0
    p2_sum_ckpt    = 0.0
    # ── per-section wall timers (diagnostic): where each checkpoint's wall goes ──
    t_reord_ckpt   = 0.0   # reloader.reload + check_reorders + pop_churn + tracked_sigma_fd
    t_build_ckpt   = 0.0   # Batch(...) + Task.from_batch(...)  (= smpl + task below)
    t_sample_ckpt  = 0.0   # Batch(...) order-sampling only (the precompute/dedup target)
    t_task_ckpt    = 0.0   # Task.from_batch(...) only (sequential — reads live placement)
    t_pre_ckpt     = 0.0   # build_pre_snapshot + snapshot_aisle_metrics + keyframe write
    t_sim_ckpt     = 0.0   # DeferredPickSimulation construct + run (p1/p2 = internal split)
    t_extract_ckpt = 0.0   # extract_batch/task/picker/picks
    t_inv_ckpt     = 0.0   # snapshot_bin_inventory
    last_dur       = 0.0
    t_loop         = time.perf_counter()
    t_ckpt         = time.perf_counter()

    for i in range(start_i, n_batches):
        _t = time.perf_counter()
        if reloader is not None:
            # Evict targeted pallets into the queue; check_reorders' ranked drain
            # (below) re-places them + reorders in priority order.
            reloader.reload(mgr, freq_by_sku, opt_x, opt_y)
        triggered      = mgr.check_reorders()
        reorders_ckpt += len(triggered)
        # Layout-quality snapshot AFTER re-slot + reorder, BEFORE this batch's picks.
        batch_rm, batch_rp = mgr.pop_churn()
        batch_sigma        = mgr.tracked_sigma_fd()    # O(1) incremental (see enable_sigma_fd)
        # Replay viewer: snapshot the standing replenishment queues at batch start (after
        # check_reorders).  lead = in-transit (with batches-to-arrival), stock = packed but
        # not yet binned (with its bin tier).  Aggregated by (sku, remaining_lead) for lead
        # and (sku, unit_type, storage_size) for stock to keep the table compact.
        _rq: dict = {}
        for _sku, _qty, _rem in mgr._lead_queue:
            _k = ('lead', _sku, _rem, None, None)
            _rq[_k] = _rq.get(_k, 0) + _qty
        for _u in mgr._stock_queue:
            _k = ('stock', _u.order.sku, 0, _u.unit_category, _u.storage_size)
            _rq[_k] = _rq.get(_k, 0) + _u.quantity
        for (_kind, _sku, _rem, _ut, _ss), _qty in _rq.items():
            pq.append((i, _kind, _sku, _qty, _rem, _ut, _ss))
        _now = time.perf_counter(); t_reord_ckpt += _now - _t; _t = _now

        # Batch i is a pure function of (inventory, affinity, config, seed_batches+i), so every arm of
        # this warehouse family sees the identical sequence.  It is precomputed ONCE per family and
        # shared (see batch_precompute); `batches` is None only when that list is unavailable, in which
        # case we sample inline here — bit-identical, just not deduplicated across arms.
        batch    = (batches[i] if batches is not None
                    else Batch(batch_cfg, inventory, affinity=affinity,
                               rng=random.Random(seed_batches + i)))
        _now = time.perf_counter(); _dt = _now - _t; t_sample_ckpt += _dt; t_build_ckpt += _dt; _t = _now
        tasks    = Task.from_batch(batch, warehouse, manager=mgr)
        _now = time.perf_counter(); _dt = _now - _t; t_task_ckpt += _dt; t_build_ckpt += _dt; _t = _now

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
        _now = time.perf_counter(); t_pre_ckpt += _now - _t; _t = _now

        if not tasks:
            skipped += 1
            continue

        sim             = DeferredPickSimulation(tasks, pick_cfg, manager=mgr)
        events          = sim.run()
        p1_sum_ckpt    += sim.phase1_time
        p2_sum_ckpt    += sim.phase2_time
        _now = time.perf_counter(); t_sim_ckpt += _now - _t; _t = _now

        bs  = extract_batch_stats(events, batch_id=i, k_pickers=k_pickers, run_id=run_id)
        bs.sigma_fd           = batch_sigma
        bs.reload_moves       = batch_rm
        bs.reorder_placements = batch_rp
        # Put-away honesty: standing backlog + in-transit pipeline after this batch's
        # reorder/restock pass (a strategy that defers placement carries a high queue).
        bs.queue_depth        = mgr.queue_depth
        bs.lead_queue_depth   = mgr.lead_queue_depth
        bs.in_transit_qty     = mgr.in_transit_qty
        ts  = extract_task_stats(events, tasks, batch_id=i, affinity=affinity, wp=wp,
                                 run_id=run_id, lift_cache=lift_cache)
        pev = extract_picker_events(events, batch_id=i, run_id=run_id)
        picks_b = extract_picks(events, batch_id=i, run_id=run_id)
        _now = time.perf_counter(); t_extract_ckpt += _now - _t; _t = _now

        inv = snapshot_bin_inventory(mgr, pre_snap, batch_id=i, run_id=run_id,
                                     full_snapshot=(i == start_i))
        _now = time.perf_counter(); t_inv_ckpt += _now - _t
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
            save_reorder_queue(db_path, run_id, pq)
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
                f'  lead_q={mgr.lead_queue_depth}({mgr.in_transit_qty}u)'
                f'  p1={p1_sum_ckpt:.2f}s ({p1_frac:.0f}%)'
                f'  p2={p2_sum_ckpt:.2f}s'
                f'  wall={wall:.0f}s'
                f'  db={t_save:.2f}s'
                # per-section breakdown of this checkpoint's batch-loop wall
                f'  | reord={t_reord_ckpt:.1f}s build={t_build_ckpt:.1f}s'
                f' (smpl={t_sample_ckpt:.1f}s task={t_task_ckpt:.1f}s)'
                f' pre={t_pre_ckpt:.1f}s sim={t_sim_ckpt:.1f}s'
                f' extr={t_extract_ckpt:.1f}s inv={t_inv_ckpt:.1f}s'
            )

            pb.clear(); pt.clear(); pe.clear(); pk.clear(); pi.clear(); pm.clear(); pq.clear()
            reorders_ckpt  = 0
            dur_sum_ckpt   = 0.0
            dur_count_ckpt = 0
            p1_sum_ckpt    = 0.0
            p2_sum_ckpt    = 0.0
            t_reord_ckpt   = 0.0
            t_build_ckpt   = 0.0
            t_sample_ckpt  = 0.0
            t_task_ckpt    = 0.0
            t_pre_ckpt     = 0.0
            t_sim_ckpt     = 0.0
            t_extract_ckpt = 0.0
            t_inv_ckpt     = 0.0
            t_ckpt         = time.perf_counter()

    if pb:
        log.info(f'  Flushing final {len(pb)} batches to DB...')
        save_batch_stats(db_path, run_id, pb)
        save_task_stats(db_path, run_id, pt)
        save_picker_events(db_path, run_id, pe)
        save_picks(db_path, run_id, pk)
        save_bin_inventory(db_path, run_id, pi)
        save_aisle_metrics(db_path, run_id, pm)
        save_reorder_queue(db_path, run_id, pq)

    elapsed = time.perf_counter() - t_loop
    done    = n_batches - start_i - skipped
    log.info('=' * 60)
    log.info(f'Strategy {strategy} DONE  batches={done}  skipped={skipped}  '
             f'wall={elapsed:.1f}s  rate={done/elapsed:.2f}/s  last_dur={last_dur:.0f}')
    log.info('=' * 60)

    # Release large per-job state before the worker returns / is recycled.  The pool
    # may run this worker again (max_tasks_per_child > 1), so drop the inventory,
    # affinity CSR, warehouse, manager state, and the lift memo before the next job
    # so RSS doesn't ratchet across jobs in a reused process.
    lift_cache.clear()
    del (inventory, affinity, warehouse, mgr, ctx, reloader,
         freq_by_sku, qty_by_sku, freq_by_idx, batches)
    import gc
    gc.collect()

    return {
        'strategy': strategy,
        'run_id'  : run_id,
        'elapsed' : elapsed,
        'done'    : done,
        'skipped' : skipped,
        'last_dur': last_dur,
    }
