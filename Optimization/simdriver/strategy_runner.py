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
can pickle it by reference in Windows spawn mode.  The child resolves it by
qualified name (Optimization.simdriver.strategy_runner._run_strategy_worker): spawn's
prepare() propagates the parent's sys.path — which the entry script seeded
with the repo root — before anything is unpickled, so this module needs no
sys.path bootstrap of its own.
"""

from __future__ import annotations

import gc
import logging
import logging.handlers
import os
import pickle
import random
import sys
import time

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import viable_storage_units as _vsu

# Minimum empty bins to preserve per (handling, category, size, unit_type) bucket
# during overstock fill so reorder units always find a slot during simulation.
_OVERSTOCK_MIN_HEADROOM: int = 10
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.picking.fast_pick import DeferredPickSimulation
from Warehouse.generation.generate_inventory import load_inventory_from_db
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.placement.Capacity_Reloader import RELOADERS
from Warehouse.operations import Crew as _Crew, Mode as _Mode, Role as _Role
from Warehouse.kernel.cost_model import SpeedProfile as _SpeedProfile
from Optimization.metrics import work_events as _work_events
from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS as _DEFAULT_SHIFT_SECONDS
from Optimization.config.strategies import STRATEGY_BY_KEY, StrategyContext
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from Warehouse.picking.Workload_Builder import Batch, Task
from Optimization.simdriver.batch_precompute import load_batches, batch_fingerprint
from Optimization.metrics.bin_recorder import BinRecorder
from Optimization.metrics.Simulation_Analytics import (
    extract_batch_stats, extract_task_stats, extract_picker_events, extract_picks,
    fused_pre_snapshot, snapshot_aisle_metrics,
)
from Optimization.persistence.Picking_Data import (
    save_checkpoint_bundle,
    save_bin_scores, save_sku_scores,
    keyframe_db_path, init_keyframe_db, save_bin_keyframe,
)
from Warehouse.kernel.cost_model import sec_per_inch, height_multiplier


# ── memory observability helpers ─────────────────────────────────────────────

# Per-arm GC accounting (SIM_GC_DETAIL=1 only — the callback costs ~5% wall when armed).
# Module-level so the worker WRAPPER's finally can always remove the callback; the impl
# resets the state at arm start.  Safe as module state: production workers are fresh
# processes (recycling pinned at 1), and in-process callers invoke arms sequentially.
_GC_STATE: dict = {'pause_s': 0.0, 'gen': [0, 0, 0], 't0': 0.0}

# Process-default GC thresholds, captured at import so the wrapper's finally can restore
# them for in-process callers even if the impl died between set_threshold and its own
# restore (the batch loop raises the gen-2 trigger while the startup graph is frozen).
_GC_THRESHOLD_DEFAULT = gc.get_threshold()


def _gc_cb(phase, info, _st=_GC_STATE):
    if phase == 'start':
        _st['t0'] = time.perf_counter()
    else:
        _st['pause_s'] += time.perf_counter() - _st['t0']
        _st['gen'][info.get('generation', 0)] += 1


def _timed_build(strat, mgr, ctx) -> float:
    """Run the strategy's build hook and return its wall seconds.

    SETUP, not loop time.  The batch loop's clock (`t_loop` .. `elapsed`) starts long
    after this, so every section column in runtime_metrics excludes it — which is why the
    Map family's offline address-map solve was, for a long time, an unmeasured cost that
    the published pages could only describe qualitatively.  It is a real number now, and
    `runtime_metrics.OUTSIDE_TOTAL` is where it says it is not part of `total_s`.

    For rules with no build step this is a few microseconds of function call; the number
    is still recorded, because "measured, and it was nothing" is a different statement
    from "never measured".
    """
    t0 = time.perf_counter()
    strat.build(mgr, ctx)
    return time.perf_counter() - t0


def _map_lap_pct(mgr) -> float | None:
    """Share of assigned UNITS the optimal map solved exactly, or None for a non-map arm.

    Units rather than classes: at catalogue scale the classes small enough for the exact
    solver hold a tiny slice of the inventory, so a class-weighted figure would read far
    more favourably than the placement it produced.
    """
    stats = getattr(mgr, '_map_lap_stats', None)
    if not stats or not stats.get('units'):
        return None
    return stats['lap_units'] / stats['units']


def _peak_rss_mib() -> float | None:
    """This process's peak working-set (high-water RSS) in MiB, or None if unreadable.

    Stdlib-only by design — psutil is deliberately not a production dependency.  Peak (as
    opposed to current) RSS is only knowable from the OS: Windows tracks it in
    PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize; POSIX reports it as ru_maxrss (KiB on
    Linux, bytes on macOS — normalised here).  One syscall, called once per arm."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD),
                            ('PageFaultCount', wintypes.DWORD),
                            ('PeakWorkingSetSize', ctypes.c_size_t),
                            ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t),
                            ('PeakPagefileUsage', ctypes.c_size_t)]

            k32 = ctypes.windll.kernel32
            # restype/argtypes are load-bearing on 64-bit: GetCurrentProcess returns the
            # pseudo-handle -1, which truncates to an invalid handle under the default
            # c_int restype and makes the query silently return 0.
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(_PMC), wintypes.DWORD]
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                           ctypes.byref(pmc), pmc.cb):
                return pmc.PeakWorkingSetSize / (1024 * 1024)
            return None
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru / 1024 if sys.platform.startswith('linux') else ru / (1024 * 1024)
    except Exception:                                     # noqa: BLE001 — never sink an arm
        return None


# ── checkpoint helpers ────────────────────────────────────────────────────────

# Windows holds a just-written file open for a few hundred ms often enough to matter at this
# frequency; 5 attempts over ~1.5 s covers every occurrence seen, without masking a real fault.
_CKPT_REPLACE_ATTEMPTS = 5
_CKPT_REPLACE_DELAY    = 0.1     # seconds, multiplied by the attempt number

def save_worker_checkpoint(run_dir: str, strategy: str, next_batch_id: int) -> None:
    # Atomic: write to a temp file then os.replace, so a crash mid-write can never leave a
    # truncated checkpoint that would mis-resume (mirrors batch_precompute.write_batches).
    #
    # THE RETRY IS NOT DEFENSIVE PADDING.  This fires once per checkpoint per arm, on the external
    # results drive, with up to 18 workers running — and on Windows `os.replace` raises
    # PermissionError (WinError 5) whenever anything holds the destination open for even a moment,
    # which a scanner or the indexer routinely does just after a file is written.  Measured on a
    # 6-batch tiny sweep: 2 of 8 arms died this way, and because the exception propagates out of
    # the worker it took the whole ARM with it, not just the checkpoint.  The wreckage then failed
    # three later smoketest stages — the orphaned `.pkl.tmp.<pid>` showed up as an undeclared path,
    # the un-finalized `_ckpt_*.pkl` as an artifact the run shape must not have, and the two lost
    # arms as `channel-run count 6 != expected 8`.
    #
    # It still RAISES if every attempt fails: a checkpoint that silently did not land would let a
    # resume restart from an earlier batch and re-emit bin-log rows under fresh `seq` values,
    # duplicating them against the (run_id, batch_id, seq) primary key.  Losing the arm is better
    # than corrupting its log, so the failure stays fatal — the retry only removes the transient.
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'wb') as f:
        pickle.dump({'next_batch_id': next_batch_id}, f)
    try:
        for attempt in range(_CKPT_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # ONLY PermissionError.  A full disk or a disconnected drive is not a lock that
                # clears, and retrying it five times only delays a fault that must surface now.
                if attempt == _CKPT_REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_CKPT_REPLACE_DELAY * (attempt + 1))   # linear back-off; locks are brief
    finally:
        # However we leave, never leave the temp behind: it outlives the run and
        # `runschema.preflight.verify` reports it as a path template no consumer knows about.
        # After a successful replace there is nothing here to remove.
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_worker_checkpoint(run_dir: str, strategy: str) -> int:
    path = os.path.join(run_dir, f'_ckpt_{strategy}.pkl')
    if not os.path.exists(path):
        return 0
    with open(path, 'rb') as f:
        return pickle.load(f).get('next_batch_id', 0)


def reset_strategy_db(run_dir: str, db_path: str, strategy: str) -> None:
    """Discard a partially-run strategy's outputs so it restarts bit-identically from batch 0
    (strategy-level resume granularity).  Removes its sim_<key>.db, that db's keyframe sibling,
    and its checkpoint.  Runs in the PARENT before any worker reopens the file (Windows-safe)."""
    for p in (db_path, keyframe_db_path(db_path),
              os.path.join(run_dir, f'_ckpt_{strategy}.pkl')):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


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

    Thin wrapper whose ONLY job is the finally: the impl freezes the startup object graph
    for the duration of the batch loop (see the gc.freeze block there), and the thaw +
    GC-callback removal must happen on EVERY exit path.  Production workers die with
    their process either way; the guarantee exists for in-process callers (the calltree
    fullfid tier, e2e tests) where a mid-arm raise would otherwise leave the HOST process
    with a permanently frozen heap and a leaked gc callback.  Both cleanups are no-ops on
    the happy path (the impl already thawed and removed).
    """
    try:
        return _run_strategy_worker_impl(args)
    finally:
        gc.unfreeze()
        gc.set_threshold(*_GC_THRESHOLD_DEFAULT)
        try:
            gc.callbacks.remove(_gc_cb)
        except ValueError:
            pass


def _run_strategy_worker_impl(args: dict) -> dict:
    """One assignment strategy end-to-end — the body behind _run_strategy_worker.

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
    cell      = args.get('cell')
    # Name the logger so EVERY worker line (the %(name)s column) carries the cell + arm — a
    # multi-cell run's interleaved output is then attributable to its cell at a glance.  Nested
    # path (no cell / no job_index) keeps the old name.
    if cell:
        log = logging.getLogger(f'{cell} {strategy}')
    elif job_index is not None:
        log = logging.getLogger(f'j{job_index}/{job_total} {strategy}')
    else:
        log = logging.getLogger(f'worker-{strategy}')

    # ── GC observability ───────────────────────────────────────────────────────
    # Collection COUNTS come from gc.get_stats() deltas — two O(1) reads, genuinely free,
    # always on.  Pause SECONDS need a gc.callbacks hook, and that hook fires on every
    # gen-0 collection: measured +4.9% wall on allocation-heavy code (2026-08-19 micro-
    # benchmark, 3,898 collections in a 0.22s storm), which two tiny-run samples confirmed
    # at ~+5% whole-run.  NOT free ⇒ default OFF, enabled per investigation session via
    # SIM_GC_DETAIL=1 (the live-object census rides the same gate).  State + callback are
    # module-level so the `_run_strategy_worker` wrapper's finally can clean up on EVERY
    # exit path (in-process callers: calltree fullfid, e2e tests); reset per arm here.
    _gc_detail = os.environ.get('SIM_GC_DETAIL', '') == '1'
    _gc_stats0 = gc.get_stats()
    _GC_STATE.update(pause_s=0.0, t0=0.0)
    _GC_STATE['gen'] = [0, 0, 0]
    if _gc_detail:
        gc.callbacks.append(_gc_cb)

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
    # Defaulted so a caller that predates the actor model (a test, a bench harness)
    # still runs: a crew on foot, an eight-hour shift.
    _pick_mode     = _Mode.of(args.get('pick_mode') or 'foot')
    _shift_seconds = args.get('shift_seconds') or _DEFAULT_SHIFT_SECONDS
    warehouse_cfg = args['warehouse_cfg']
    pick_cfg      = args['pick_cfg']
    wp            = args['wp']
    load_params   = args['load_params']
    batch_cfg     = args['batch_cfg']
    batches_path        = args.get('batches_path')
    batches_fingerprint = args.get('batches_fingerprint')
    # One-worker-per-channel: when set, this worker simulates ONLY the given regime's SKUs
    # (store or fulfillment) over the shared warehouse, with the channel's pick cost + batch
    # stream + output DB.  None ⇒ the whole inventory in one stream (store-only, unchanged).
    channel_regime      = args.get('channel_regime')

    cell_pos = args.get('cell_pos')
    gjob     = args.get('gjob')
    log.info('=' * 60)
    if job_tag is not None:
        # per-arm line with LOCAL (this-cell) + GLOBAL (whole-run) progress counters
        _prog = f'Job {job_index}/{job_total}'
        if cell_pos:
            _prog += f'  [cell {cell_pos} · global {gjob}]'
        log.info(f'{_prog}  {job_tag}')
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
    if channel_regime is not None:
        from Warehouse.kernel.regime import regime_of
        inventory.orders = [c for c in inventory.orders if regime_of(c) == channel_regime]
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
    log.info(f'  {n_aff:,} entries  {mb:.0f} MB  ({time.perf_counter()-t0:.1f}s)  '
             f'rss_peak={_peak_rss_mib() or 0:.0f}M')

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
        beta=1.0, orders=inventory.orders,
        # k = expected distinct SKUs per batch (mean_fraction·N); the Rank_cartlabor cart
        # term uses it to convert expected demand mass into expected per-task aisle volume.
        expected_batch_skus=batch_cfg.mean_fraction * batch_cfg.inventory_size)

    # ── warehouse ─────────────────────────────────────────────────────────────
    log.info(f'Building warehouse: {warehouse_cfg.total_aisles} aisles...')
    t0 = time.perf_counter()
    Aisle.next_aisle_id = 1
    random.seed(seed_world)
    warehouse  = Warehouse_Builder().from_config(warehouse_cfg).build()
    total_bins = len(warehouse.bins)   # density-aware: actual count after physical expansion
    log.info(f'  Built {total_bins:,} bins  ({time.perf_counter()-t0:.1f}s)  '
             f'rss_peak={_peak_rss_mib() or 0:.0f}M')

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
    # Record every bin mutation.  Installed BEFORE any stocking so the initial fill is captured
    # too; wraps the manager INSTANCE, so Warehouse/ is untouched.  This is the term the record
    # used to be missing — the retired bin_inventory logged picks and never restocks (see
    # bin_recorder's docstring).
    bin_rec = BinRecorder(run_id)
    bin_rec.attach(mgr)

    # Velocity zoning (per-channel; default off ⇒ byte-identical): band the SKUs by velocity and
    # the aisles by geometry BEFORE any stocking, so _candidates routes hot SKUs to shallow aisles.
    _zcfg = args.get('velocity_zoning') or {}
    if _zcfg.get('enabled'):
        mgr.configure_zoning(True, int(_zcfg.get('n_bands', 3)), inventory.orders,
                             mode=_zcfg.get('mode', 'equal'), abc=_zcfg.get('abc'))
        log.info(f'  velocity zoning ON  (mode={_zcfg.get("mode","equal")} n_bands={mgr._zoning_bands})')

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
        t_precompute = _timed_build(strat, mgr, ctx)
        mgr.enqueue_all(inventory.orders)   # placed by the strategy's own policy
        _arm_aisle_state()                   # authoritative rebuild over the final layout
    else:
        log.info(f'Initial stock: {n_skus:,} SKUs  uniform placement...')
        mgr.enqueue_all(inventory.orders)   # quantity read from order.equilibrium_qty
        _arm_aisle_state()
        if strat.uses_aisle_index:
            mgr.init_travel_costs(wp)
        t_precompute = _timed_build(strat, mgr, ctx)
    map_lap_pct = _map_lap_pct(mgr)

    # Fill rate is over THIS channel's regime bins: a per-channel worker only stocks its own
    # regime's units, so dividing by the whole (mixed) warehouse would understate fill by the
    # other regime's empty share.  channel_regime None (store-only) => the whole warehouse.
    base_filled = len(mgr._unavailable)
    if channel_regime is not None:
        from Warehouse.kernel.regime import regime_of
        denom = sum(1 for b in warehouse.bins if regime_of(b) == channel_regime)
        unit  = f'{channel_regime} bins'
    else:
        denom, unit = len(warehouse.bins), 'bins'
    log.info(f'  {base_filled:,} / {denom:,} {unit} filled  '
             f'({base_filled / max(denom, 1):.1%})  ({time.perf_counter()-t0:.1f}s)  '
             f'rss_peak={_peak_rss_mib() or 0:.0f}M')
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

    # ── the two crews, and the axis they share ─────────────────────────────────
    # The pick crew's mode comes from the channel's PickerProfile ('store_machine' is a
    # machine pool, 'fulfillment_walker' is on foot).  The put crew is ONE walker: put-away
    # is not yet a swept axis, and a size nothing varies should be a stated default rather
    # than a knob nobody turns.  uids are allocated pick-crew first, so a picker's uid and
    # its dense picker_id coincide -- which keeps `work_events.actor_uid` readable against
    # `picker_events.picker_id` for the single-crew case that every existing analysis assumes.
    _pick_crew = _Crew(role=_Role.PICK, mode=_pick_mode, speed=pick_cfg.speed, size=k_pickers)
    _pick_workers = _pick_crew.workers(0)
    _pc = args.get('put_crew') or {'size': 1, 'mode': 'foot', 'x_speed': 2.0, 'y_speed': 4.0}
    _put_crew = _Crew(role=_Role.PUT, mode=_Mode.of(_pc['mode']),
                      speed=_SpeedProfile(_pc['x_speed'], _pc['y_speed']), size=_pc['size'])
    _put_workers = _put_crew.workers(_pick_crew.next_uid(0))
    # Put-away now costs seconds.  ADDITIVE: it moves no pick result (same items, same
    # order, same instants); it records durations and rows.  See enable_putaway_timing.
    mgr.enable_putaway_timing(_put_crew.speed)

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
        # Saved and logged — nothing reads these again, but the locals would otherwise
        # stay alive for the whole arm (~120 MB of row tuples pinned for nothing).
        del bin_rows, sku_rows

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
    we: list = []   # merged cross-stream rows (picks + put-away) on the absolute axis
    pk: list = []   # individual pick records
    pm: list = []   # aisle metrics snapshots
    pq: list = []   # reorder-queue contents per batch (lead + stock), for the replay viewer
    lift_cache: dict = {}   # memoize sum_lift(frozenset(task_skus)) across batches (O(k^2)/task)
    skipped        = 0
    # This arm's absolute clock: where the NEXT batch begins.  Batches are sequential
    # waves -- batch i+1's work is released when batch i completes -- so the whole crew
    # starts a batch together, at `arm_clock`, and the axis is the running sum of the batch
    # makespans (exactly Warehouse.kernel.timeline.epochs).
    #
    # Deliberately uniform rather than per-picker.  A per-picker carry, where whoever
    # finishes early starts the next wave early, is a MODELLING change (it removes the
    # barrier) and it has a failure mode: a picker who draws no task keeps its clock
    # frozen while the others advance, so the crew's clocks drift apart without bound and
    # `batch_start_time` sticks at 0 forever.  The `start_times` seam supports it when
    # someone wants it; this is not that commit.
    #
    # A resumed arm restarts at 0.0 -- the finish times before the resume boundary are not
    # in the checkpoint.  Durations and labor are unaffected (both are spans); the absolute
    # axis of a resumed run starts over mid-run.
    arm_clock: float = 0.0
    # Where the PUT crew finished.  It is continuous and independent of the pick
    # crew's waves: one putter placing a wave's restock takes longer than 25 pickers
    # take to pick it, so its work overruns the next wave's release and must resume
    # where it stopped rather than restarting.  Without this the same worker is doing
    # two batches at the same instant.
    put_clock: float = 0.0
    reorders_ckpt      = 0   # distinct SKUs reordered this checkpoint window (N)
    units_ordered_ckpt = 0   # units ordered this window (U = Σ reorder qty)
    placed_ckpt        = 0   # units placed this window (P = reorder placements)
    dur_sum_ckpt   = 0.0
    dur_count_ckpt = 0
    p1_sum_ckpt    = 0.0
    p2_sum_ckpt    = 0.0
    # ── per-section wall timers (diagnostic): where each checkpoint's wall goes ──
    t_reord_ckpt   = 0.0   # reloader.reload + check_reorders + pop_churn + tracked_sigma_fd
    t_build_ckpt   = 0.0   # Batch(...) + Task.from_batch(...)  (= smpl + task below)
    t_sample_ckpt  = 0.0   # Batch(...) order-sampling only (the precompute/dedup target)
    t_task_ckpt    = 0.0   # Task.from_batch(...) only (sequential — reads live placement)
    t_pre_ckpt     = 0.0   # fused_pre_snapshot + snapshot_aisle_metrics + keyframe write
    t_sim_ckpt     = 0.0   # DeferredPickSimulation construct + run (p1/p2 = internal split)
    t_extract_ckpt = 0.0   # extract_batch/task/picker/picks
    t_inv_ckpt     = 0.0   # bin accounting: the conservation ledger (was: snapshot_bin_inventory)

    # ── conservation ledger ───────────────────────────────────────────────────
    # The runtime proof that the bin-mutation log is complete.  Over the whole arm,
    #
    #     units placed − units evicted − units picked  ==  units sitting in bins right now
    #
    # and the right-hand side is read from the WAREHOUSE, not from any counter the recorder
    # keeps — so a mutation that bypasses the log breaks it, which is the entire point.  It is
    # asserted cumulatively rather than as a per-batch delta because the cumulative form needs
    # no special case for the first measured batch (a resumed arm re-stocks from empty) and
    # localises a break just as well: the first batch that fails names the one that broke it.
    #
    # Measured at `fused_pre_snapshot` time — start of batch, after restock, before picks —
    # because that pass IS the occupied-bin walk, so the occupancy term accumulates inside
    # it rather than as a second walk of 396,500 bins.
    cons_picked   = 0      # units picked, cumulative over the arm
    cons_breaks   = 0      # batches that INTRODUCED an unaccounted-for unit
    cons_residual = 0      # last observed (ledger − occupancy); see the report rule below
    # Whole-arm section totals (never reset) → returned so the PARENT writes the runtime-metrics DB
    # (single writer, no SQLite contention).  These pinpoint hot sections (e.g. a reorder/reslot
    # dominance = the recurring valid-aisle recompute suspicion).
    t_reord_run = t_build_run = t_pre_run = t_sim_run = t_extract_run = t_inv_run = t_save_run = 0.0
    # Finer whole-arm splits, persisted since the runtime_metrics column add: the build
    # sub-split (smpl/task), the keyframe write (a sub-span of t_pre — overlay, not a new
    # partition member), and fast_pick's phase split (previously per-checkpoint only, the
    # final unflushed window silently discarded).
    t_sample_run = t_task_run = t_kf_run = p1_run = p2_run = 0.0
    t_kf_ckpt = 0.0
    last_dur       = 0.0

    # ── freeze the startup graph out of every future collection ──────────────
    # Everything alive here (warehouse ~400k bins, manager indexes, inventory, CSR,
    # accumulators) survives the whole arm; gen-2 passes re-walking it cost ~38s/arm at
    # 40k SKUs (measured, comparison_20260819_121157) and grow with heap size.  Collect
    # once so startup garbage isn't made immortal, then freeze the survivors into the
    # permanent generation.  Frozen containers stay mutable; loop-allocated objects are
    # tracked and collected normally; frozen objects act as GC roots for young referents.
    # Thaw is GUARANTEED by the `_run_strategy_worker` wrapper's finally (a mid-loop raise
    # inside an IN-PROCESS caller — calltree fullfid, e2e tests — must not leave the host
    # pytest process with a permanently frozen heap); the happy path unfreezes before the
    # end-of-arm census so `live_objects` keeps its meaning.
    #
    # Freeze alone BACKFIRES (measured, comparison_20260819_144916: pause 24.9→27.4s/arm,
    # gen-2 count 28→50): moving everything to the permanent generation empties the
    # collector's long-lived denominator, so the full-collection heuristic
    # (long_lived_pending > long_lived_total/4) passes on nearly every gen-1 overflow.
    # Each walk got 38% cheaper (0.89→0.55s — the freeze working as intended) but fired
    # ~1.8× as often.  The companion below restores a sane full-collection cadence by
    # raising the gen-2 trigger; the wrapper's finally restores the process default.
    gc.collect()
    gc.freeze()
    _gc_thresh = gc.get_threshold()
    gc.set_threshold(_gc_thresh[0], _gc_thresh[1], _gc_thresh[2] * 5)
    t_loop         = time.perf_counter()
    t_ckpt         = time.perf_counter()

    for i in range(start_i, n_batches):
        _t = time.perf_counter()
        bin_rec.begin_batch(i)
        if reloader is not None:
            # Evict targeted pallets into the queue; check_reorders' ranked drain
            # (below) re-places them + reorders in priority order.
            reloader.reload(mgr, freq_by_sku, opt_x, opt_y)
        triggered      = mgr.check_reorders()
        reorders_ckpt += len(triggered)
        # Layout-quality snapshot AFTER re-slot + reorder, BEFORE this batch's picks.
        batch_rm, batch_rp = mgr.pop_churn()
        # Standardized reorder/stock accounting: N skus reordered (triggered), U units ordered
        # (mgr.units_ordered), P units placed (batch_rp = reorder placements this batch).
        batch_uo            = mgr.units_ordered
        units_ordered_ckpt += batch_uo
        placed_ckpt        += batch_rp
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
        tasks    = Task.from_batch(batch, warehouse, manager=mgr, cart=pick_cfg.cart)
        _now = time.perf_counter(); _dt = _now - _t; t_task_ckpt += _dt; t_build_ckpt += _dt; _t = _now

        # One fused pass over the occupied bins (bin qtys before picks): the occupancy
        # term for the conservation ledger below always, keyframe row dicts only when
        # this batch writes one.  Replaces build_pre_snapshot, whose ~400k-dict was
        # built every batch but consumed past the pre_qty sum only on keyframe batches.
        # NOTE for bench_sections comparisons across this change: the occupancy sum
        # used to be timed under t_inv and now rides t_pre; t_kf no longer includes
        # building the row list (that is the fused pass), only the DB write.
        _want_kf = kf_db is not None and i % keyframe_interval == 0
        occupancy, kf_rows = fused_pre_snapshot(mgr, _want_kf)
        am = snapshot_aisle_metrics(mgr, batch_id=i, run_id=run_id)  # aisle state

        # Keyframe: full occupied-bin state at this batch's start (after reorders),
        # written every keyframe_interval batches so the player can jump here
        # without replaying deltas from batch 0.
        # t_kf is a SUB-SPAN of t_pre (the smpl/task-inside-build pattern): `_t` is not
        # touched, so t_pre still covers the whole stretch and the sections stay a
        # partition of the loop body (the calltree smoke test's invariant).
        if _want_kf:
            _k0 = time.perf_counter()
            save_bin_keyframe(kf_db, run_id, i, kf_rows)
            t_kf_ckpt += time.perf_counter() - _k0
        _now = time.perf_counter(); t_pre_ckpt += _now - _t; _t = _now

        # ── conservation ledger ────────────────────────────────────────────────
        # Σplaced − Σevicted − Σpicked must equal the units actually in bins.  `occupancy`
        # came from the fused pass above — the occupied-bin walk at this instant — so the
        # term costs nothing extra here.  Checked BEFORE the `not tasks` skip so an
        # empty batch is audited like any other.
        #
        # LOGGED, NEVER RAISED — deliberately.  A hard raise would kill a 20-minute arm (and
        # with it the sibling arms of a running comparison) for a diagnostic that is not a
        # safety interlock: every row involved is already on disk, so a break is fully
        # re-derivable after the fact from `bin_placement`/`bin_eviction`/`picks` themselves.
        # There is also one structurally possible BENIGN source of drift — fast_pick's Phase-2
        # clamp (`actual = min(mut.qty, bin_.storage.quantity)`) can record a pick event larger
        # than the depletion it applied when two pickers contend for one bin, which is rare but
        # is a scheduling coincidence, not a corrupt run.  Turning that into a run-killer would
        # be strictly worse than reporting it.
        #
        # The ledger is cumulative, so one bad batch leaves a residual that persists forever.
        # Reporting every batch after the first would be ~100 identical lines; reporting only
        # when the residual MOVES names exactly the batches that introduced unaccounted units.
        residual  = (bin_rec.units_placed - bin_rec.units_evicted - cons_picked) - occupancy
        if residual != cons_residual:
            cons_breaks += 1
            log.error(
                f'  CONSERVATION BROKEN at batch {i}: '
                f'placed={bin_rec.units_placed:,} − evicted={bin_rec.units_evicted:,} − '
                f'picked={cons_picked:,} = {bin_rec.units_placed - bin_rec.units_evicted - cons_picked:,} '
                f'but bins hold {occupancy:,} units '
                f'(new drift {residual - cons_residual:+,}; cumulative {residual:+,}). '
                f'A bin mutated outside bin_placement/bin_eviction/picks, so spatial '
                f'reconstruction for this arm is no longer exact — see '
                f'Optimization/metrics/bin_recorder.py.')
            cons_residual = residual
        _now = time.perf_counter(); t_inv_ckpt += _now - _t; _t = _now

        if not tasks:
            skipped += 1
            continue

        # The clock CARRIES.  Every picker starts this batch at the arm's current instant,
        # so the arm's events sit on one absolute axis instead of every batch restarting at
        # zero.  Because the offset is uniform, every batch statistic is a span measured
        # from it and is UNCHANGED -- which is what the offset-invariance work in
        # extract_batch_stats bought.
        sim             = DeferredPickSimulation(tasks, pick_cfg, manager=mgr,
                                                 start_times=[arm_clock] * k_pickers)
        events          = sim.run()
        p1_sum_ckpt    += sim.phase1_time
        p2_sum_ckpt    += sim.phase2_time
        _now = time.perf_counter(); t_sim_ckpt += _now - _t; _t = _now

        bs  = extract_batch_stats(events, batch_id=i, k_pickers=k_pickers, run_id=run_id)
        bs.sigma_fd           = batch_sigma
        bs.reload_moves       = batch_rm
        bs.reorder_placements = batch_rp                 # units PLACED this batch (P)
        bs.skus_reordered     = len(triggered)           # SKUs reordered this batch (N)
        bs.units_ordered      = batch_uo                 # units ORDERED this batch (U)
        # Put-away honesty: standing backlog + in-transit pipeline after this batch's
        # reorder/restock pass (a strategy that defers placement carries a high queue).
        # Next wave begins when this one completes: arm_clock += this batch's makespan.
        # A SKIPPED (empty) batch never reaches here and so does not advance it -- which is
        # also why the epoch cannot be recovered downstream by a cumsum over batch_stats
        # rows: a skipped batch writes no row at all.
        arm_clock             = bs.batch_start_time + bs.duration
        bs.queue_depth        = mgr.queue_depth
        bs.lead_queue_depth   = mgr.lead_queue_depth
        bs.in_transit_qty     = mgr.in_transit_qty
        ts  = extract_task_stats(events, tasks, batch_id=i, affinity=affinity, wp=wp,
                                 run_id=run_id, lift_cache=lift_cache)
        pev = extract_picker_events(events, batch_id=i, run_id=run_id)
        picks_b = extract_picks(events, batch_id=i, run_id=run_id)
        _now = time.perf_counter(); t_extract_ckpt += _now - _t; _t = _now

        # Close this batch's pick term.  `picks_b` is what the DB receives, so the ledger
        # audits the LOG rather than the manager's private counters — a pick the record
        # over- or under-states shows up here even though the sim itself is self-consistent.
        cons_picked += sum(p.quantity for p in picks_b)
        _now = time.perf_counter(); t_inv_ckpt += _now - _t
        pb.append(bs)
        pt.extend(ts)
        pe.extend(pev)
        # Both streams onto ONE axis.  Pick events already carry absolute times (the arm
        # handed every picker the batch epoch); the put crew's records run on its own clock
        # from 0 and are offset here.  Neither stream waits for the other -- they are
        # simulated independently and merged, which is this model's stated assumption.
        we.extend(_work_events.pick_rows(
            events, batch_id=i, batch_start=bs.batch_start_time, crew=_pick_workers,
            shift_seconds=_shift_seconds))
        if _put_workers is not None:
            _put_recs = mgr.drain_putaway_records()
            # The crew picks this wave's queue up when the wave is released OR when it
            # finishes the last one, whichever is later.
            _put_base = max(bs.batch_start_time, put_clock)
            we.extend(_work_events.put_rows(
                _put_recs, batch_id=i, batch_start=bs.batch_start_time,
                crew=_put_workers, shift_seconds=_shift_seconds, crew_start=_put_base))
            if _put_recs:
                put_clock = _put_base + _put_recs[-1][0] + _put_recs[-1][1]
        pk.extend(picks_b)
        pm.extend(am)
        last_dur        = bs.duration
        dur_sum_ckpt   += bs.duration
        dur_count_ckpt += 1

        if len(pb) >= checkpoint:
            t_s0 = time.perf_counter()
            _bp, _be = bin_rec.drain()
            save_checkpoint_bundle(
                db_path, run_id,
                batch_stats=pb, task_stats=pt, picker_events=pe, picks=pk,
                bin_placements=_bp, bin_evictions=_be,
                aisle_metrics=pm, reorder_queue=pq, work_events=we)
            save_worker_checkpoint(run_dir, strategy, i + 1)
            t_save = time.perf_counter() - t_s0

            wall      = time.perf_counter() - t_loop
            ckpt_wall = time.perf_counter() - t_ckpt
            cum_rate  = (i + 1 - start_i) / wall
            ckpt_rate = dur_count_ckpt / ckpt_wall
            avg_dur   = dur_sum_ckpt / dur_count_ckpt if dur_count_ckpt else 0.0
            cur_fill  = len(mgr._unavailable) / max(denom, 1)   # denom = THIS channel's regime bins
            p1_frac   = p1_sum_ckpt / (p1_sum_ckpt + p2_sum_ckpt + 1e-9) * 100

            log.info(
                f'  Batch {i+1:4d}/{n_batches}'
                f'  dur={bs.duration:6.0f}'
                f'  avg={avg_dur:6.0f}'
                f'  rate={ckpt_rate:.2f}/s ({cum_rate:.2f} cum)'
                f'  fill={cur_fill:.1%}'
                f'  q={mgr.queue_depth}'
                f'  reorder={reorders_ckpt}sku {units_ordered_ckpt}u ord {placed_ckpt}u plc'
                f'  lead_q={mgr.lead_queue_depth}({mgr.in_transit_qty}u)'
                f'  p1={p1_sum_ckpt:.2f}s ({p1_frac:.0f}%)'
                f'  p2={p2_sum_ckpt:.2f}s'
                f'  wall={wall:.0f}s'
                f'  db={t_save:.2f}s'
                # per-section breakdown of this checkpoint's batch-loop wall
                f'  | reord={t_reord_ckpt:.1f}s build={t_build_ckpt:.1f}s'
                f' (smpl={t_sample_ckpt:.1f}s task={t_task_ckpt:.1f}s)'
                f' pre={t_pre_ckpt:.1f}s sim={t_sim_ckpt:.1f}s'
                f' extr={t_extract_ckpt:.1f}s cons={t_inv_ckpt:.1f}s'
                # overlay metrics (kf ⊂ pre; gc overlaps every section) — appended AFTER
                # the partition tokens so bench_sections' unanchored _SEC_RE still matches
                f' kf={t_kf_ckpt:.1f}s gc={_GC_STATE["pause_s"]:.2f}s'
            )

            # fold this checkpoint window's section times into the whole-arm totals before reset
            t_reord_run   += t_reord_ckpt
            t_build_run   += t_build_ckpt
            t_pre_run     += t_pre_ckpt
            t_sim_run     += t_sim_ckpt
            t_extract_run += t_extract_ckpt
            t_inv_run     += t_inv_ckpt
            t_save_run    += t_save
            t_sample_run  += t_sample_ckpt
            t_task_run    += t_task_ckpt
            t_kf_run      += t_kf_ckpt
            p1_run        += p1_sum_ckpt
            p2_run        += p2_sum_ckpt

            pb.clear(); pt.clear(); pe.clear(); pk.clear(); pm.clear(); pq.clear()
            we.clear()
            reorders_ckpt      = 0
            units_ordered_ckpt = 0
            placed_ckpt        = 0
            dur_sum_ckpt   = 0.0
            dur_count_ckpt = 0
            p1_sum_ckpt    = 0.0
            p2_sum_ckpt    = 0.0
            t_reord_ckpt   = 0.0
            t_build_ckpt   = 0.0
            t_sample_ckpt  = 0.0
            t_task_ckpt    = 0.0
            t_kf_ckpt      = 0.0
            t_pre_ckpt     = 0.0
            t_sim_ckpt     = 0.0
            t_extract_ckpt = 0.0
            t_inv_ckpt     = 0.0
            t_ckpt         = time.perf_counter()

    # fold the final (unflushed) window's section times into the whole-arm totals
    t_reord_run   += t_reord_ckpt
    t_build_run   += t_build_ckpt
    t_pre_run     += t_pre_ckpt
    t_sim_run     += t_sim_ckpt
    t_extract_run += t_extract_ckpt
    t_inv_run     += t_inv_ckpt
    t_sample_run  += t_sample_ckpt
    t_task_run    += t_task_ckpt
    t_kf_run      += t_kf_ckpt
    p1_run        += p1_sum_ckpt
    p2_run        += p2_sum_ckpt
    if pb:
        log.info(f'  Flushing final {len(pb)} batches to DB...')
        _ts_final = time.perf_counter()
        _bp, _be = bin_rec.drain()
        save_checkpoint_bundle(
            db_path, run_id,
            batch_stats=pb, task_stats=pt, picker_events=pe, picks=pk,
            bin_placements=_bp, bin_evictions=_be,
            aisle_metrics=pm, reorder_queue=pq, work_events=we)
        t_save_run += time.perf_counter() - _ts_final

    # Final-checkpoint guard: a cleanly-finished arm's marker may sit at the last checkpoint
    # boundary (< n_batches) when n_batches isn't a multiple of `checkpoint` — the tail was
    # flushed above but the marker didn't advance.  Pin it to n_batches so a later --resume of
    # a not-yet-finalized group treats this arm as done (empty loop) instead of re-INSERTing
    # its tail rows.  Idempotent when the marker already reached n_batches.
    if n_batches > start_i:
        save_worker_checkpoint(run_dir, strategy, n_batches)

    elapsed = time.perf_counter() - t_loop
    done    = n_batches - start_i - skipped
    n_bins      = len(warehouse.bins)                      # runtime-metrics: warehouse size proxy
    regime_bins = denom                                    # this channel's regime bin count
    n_aisles    = len(getattr(warehouse, 'aisles', []) or [])
    log.info('=' * 60)
    log.info(f'Strategy {strategy} DONE  batches={done}  skipped={skipped}  '
             f'wall={elapsed:.1f}s  rate={done/elapsed:.2f}/s  last_dur={last_dur:.0f}')
    # State the ledger's verdict once per arm, either way: a silent pass is indistinguishable
    # from a check that never ran, and "the log is complete" is the claim the whole spatial
    # record rests on.  The failing form repeats at ERROR so it survives a log tail.
    if cons_breaks:
        log.error(f'Strategy {strategy} CONSERVATION: {cons_breaks} batch(es) broke the ledger; '
                  f'{cons_residual:+,} units unaccounted for at the end. The bin-mutation log '
                  f'for this arm is INCOMPLETE — spatial reconstruction will not be exact.')
    else:
        log.info(f'  conservation OK: placed {bin_rec.units_placed:,} − evicted '
                 f'{bin_rec.units_evicted:,} − picked {cons_picked:,} balanced against bin '
                 f'occupancy on every batch')
    log.info('=' * 60)

    # Thaw the startup graph BEFORE the release below: unfreezing returns the permanent
    # generation to the oldest gen, so the collect() actually reclaims the cyclic
    # warehouse/manager graph (bins↔aisles; BinRecorder wrappers close over mgr), keeping
    # the RSS-ratchet guard meaningful for in-process callers and any future recycling —
    # and keeping the live-object census below comparable across runs.  In production
    # (recycling pinned at 1) the process exits right after; this is for everyone else.
    gc.unfreeze()
    gc.set_threshold(*_gc_thresh)
    lift_cache.clear()
    # bin_rec goes with them: its wrappers close over the manager's bound methods, so holding
    # the recorder holds the whole manager (and through it the warehouse) alive.
    del (inventory, affinity, warehouse, mgr, ctx, reloader, bin_rec,
         freq_by_sku, qty_by_sku, freq_by_idx, batches)
    gc.collect()

    # ── memory observability, end-of-arm only (each a one-shot: ~free) ────────
    # Peak RSS comes from the OS (the process's high-water mark — the number that decides
    # whether N workers fit in RAM).  Gen-2 count = get_stats delta (always).  The
    # live-object census (an O(live) list build) rides the SIM_GC_DETAIL gate with the
    # pause hook; when off it records NULL, never a fabricated zero.
    try:
        gc.callbacks.remove(_gc_cb)
    except ValueError:
        pass
    gc_gen2 = gc.get_stats()[2]['collections'] - _gc_stats0[2]['collections']
    live_objects = len(gc.get_objects()) if _gc_detail else None
    peak_rss_mib = _peak_rss_mib()
    log.info(f'  memory: peak_rss={peak_rss_mib or 0:.0f}M  '
             f'gc_pause={_GC_STATE["pause_s"]:.2f}s  gen2={gc_gen2}  '
             f'live={live_objects if live_objects is not None else "-"}  '
             f'frozen_residual={gc.get_freeze_count()}')

    return {
        'strategy': strategy,
        'run_id'  : run_id,
        'elapsed' : elapsed,
        'done'    : done,
        'skipped' : skipped,
        'last_dur': last_dur,
        # Conservation verdict for this arm — 0 means the bin-mutation log balanced against
        # real bin occupancy on every batch.  Surfaced to the parent so a sweep can be judged
        # from the result dicts without grepping 34 worker logs.
        'cons_breaks'  : cons_breaks,
        'cons_residual': cons_residual,
        # ── runtime metrics: whole-arm section totals (s) + warehouse identity; the PARENT
        #    (supervisor._run_pool) inserts these into runtime_metrics.db at the run root ──
        'n_bins'    : n_bins,
        'regime_bins': regime_bins,
        'n_aisles'  : n_aisles,
        # SETUP spans, measured before the batch loop's clock starts — so they are NOT
        # part of `elapsed` and must never be stacked onto the section totals below.
        # runtime_metrics.OUTSIDE_TOTAL is the declaration of that separation.
        't_precompute': t_precompute,   # strat.build(): the map family's offline solve
        'map_lap_pct' : map_lap_pct,    # None on every non-map arm
        't_reord'   : t_reord_run,
        't_build'   : t_build_run,
        't_sample'  : t_sample_run,     # build sub-split: batch sampling
        't_task'    : t_task_run,       # build sub-split: task construction
        't_kf'      : t_kf_run,         # sub-span of t_pre: the keyframe sqlite write
        't_pre'     : t_pre_run,
        't_sim'     : t_sim_run,
        'p1_s'      : p1_run,           # fast_pick phase 1 (threaded picker compute)
        'p2_s'      : p2_run,           # fast_pick phase 2 (sequential mutation apply)
        't_extract' : t_extract_run,
        # end-of-arm memory observability (see the log line above; pause/census are
        # SIM_GC_DETAIL-gated — 0.0/None on a default run, by design)
        'gc_pause_s'  : _GC_STATE['pause_s'],
        'gc_gen2'     : gc_gen2,
        'peak_rss_mib': peak_rss_mib,
        'live_objects': live_objects,
        # runtime_metrics.inv_s.  Pre-log arms spent this on the bin_inventory snapshot; from
        # here on it is the conservation ledger, which is ~1000x cheaper.  The column keeps its
        # name so archived rows stay comparable to themselves — a renamed column would move the
        # runtime_metrics schema id for a relabelling.
        't_inv'     : t_inv_run,
        't_save'    : t_save_run,
    }

