"""
Warehouse assignment strategy simulation runner.

Runs A/B/C strategy workers for each (pair × regression-config) combination
and saves all results to per-strategy SQLite DBs.  Graph generation is a
separate step: run_analysis.py <base_dir>.

Modes:
  python run_simulation.py                          # new run, sequential
  python run_simulation.py --workers 15             # flat pool, no idle
  python run_simulation.py --resume <base_dir>      # resume a crashed run
"""

import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import multiprocessing
import os
import sys
import time
from datetime import datetime

# ── path setup: put the repo root on sys.path so package imports resolve when
#    this file is run as a script (python Optimization/run_simulation.py).
#    Running via `python -m Optimization.run_simulation` needs none of this.
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── Split modules + compatibility surface ─────────────────────────────────────
# Config/sweeps/logging live in sim_config, shared-asset build in sim_assets,
# resume + run_manifest in sim_manifest, tree walkers in runlayout.  The names are
# re-exported here because tests (rs.CONFIG, rs.REGRESSION_CONFIGS, ...) and
# Diagnostics/bucket_fill import them from run_simulation.  CONFIG binds the SAME
# dict object as sim_config.CONFIG (tests mutate it in place) — never rebind it.
from Optimization.sim_config import (            # noqa: F401
    CONFIG, REGRESSION_CONFIGS, STORE_CONFIGS, FULFILLMENT_CONFIGS,
    SEED_WORLD, SEED_BATCHES, N_BATCHES, K_PICKERS, STORE_RESTOCKS, _INITIAL_FILL,
    _OUTPUT_DIR, _DEFAULT_PROFILES_DIR, _CATEGORIES, _HANDLINGS, _AISLE_W, _AISLE_H,
    _STORE_PICKERS, _FF_PICKERS, _CART_TYPES,
    regime_sizing_from_config, _setup_logging, _checkpoint_every,
    _config_name, _build_pick_cfg, _clean_path, _load_env,
)
from Optimization.sim_assets import build_shared_assets                    # noqa: F401
from Optimization.sim_manifest import (                                    # noqa: F401
    _resume_path, _save_resume, _load_resume, write_run_manifest,
)


from Warehouse.Inventory_Management import Inventory_Manager
from Optimization.strategies import STRATEGIES, strategies_for
from Warehouse.Storage_Primitive import StoreCart

from Optimization.Picking_Data import create_run, init_run_db
from Optimization.Workload import WorkloadParams
from Warehouse.regime import STORE, FULFILLMENT

from Optimization.strategy_runner import (
    load_worker_checkpoint, _run_strategy_worker, _cleanup_checkpoints,
)
from Optimization.batch_precompute import ensure_batches


# ── DB helpers ─────────────────────────────────────────────────────────────────


# Directory-layout walkers live in runlayout (single owner of the tree shapes);
# re-imported here so rs.discover_db_pairs / rs.find_latest_db_pairs keep working.
from Optimization.runlayout import discover_db_pairs, find_latest_db_pairs, iter_sim_dbs  # noqa: F401,E402


def _prepare_channel_run(
    channel,                 # channels.Channel — its picker carries this run's cost + pool
    cfg     : dict,
    mixed   : bool,          # catalog has >1 regime → regime-filter + <config>/<channel>/ subdir
    shared  : dict,
    pair_dir: str,
    log     : logging.Logger,
    workers : int = 1,
) -> tuple[list, list]:
    """Pre-initialise ONE channel-run (one config driving one channel): create DBs, get
    run_ids, build strategy_args for the flat ProcessPoolExecutor.

    Store and fulfillment sweep their own config sets independently, so a run drives a single
    channel.  ``mixed`` is True when the catalog holds more than one regime — the run then
    filters inventory to ``channel.regime``, draws its own batch stream, and writes a
    ``<config>/<channel>/`` subtree.  A store-only catalog (``mixed`` False) collapses to the
    legacy ``<config>/`` layout + shared precomputed batch stream (byte-identical).

    Returns (strategy_args_list, [sim_result_skeleton]).  log_queue is NOT set — the caller
    injects it before submission.
    """
    from dataclasses import replace                        # noqa: E402 (local)
    from Warehouse.regime import regime_of                           # noqa: E402

    n_batches = CONFIG['global']['n_batches']              # may be overridden via --n-batches
    name     = _config_name(cfg)
    pick_cfg = channel.picker.cost      # built via _build_pick_cfg with this channel's pool + cart
    wp       = WorkloadParams.from_pick_config(pick_cfg)
    # Single channel → placement uses this run's own wp.  Only one regime's units are placed and
    # regimes route to disjoint bins, so a cross-regime cost map would be inert (verified against
    # Inventory_Management.init_travel_costs — the other regime's bins are never read).
    wp.by_regime = None
    run_dir  = os.path.join(pair_dir, name)
    os.makedirs(run_dir, exist_ok=True)

    inventory          = shared['inventory']
    batch_cfg          = shared['batch_cfg']
    load_params        = shared['load_params']
    warehouse_cfg      = shared['warehouse_cfg']
    total_aisles       = shared['total_aisles']
    total_bins         = shared['total_bins']
    total_units_needed = shared['total_units_needed']
    warehouse_meta     = shared.get('warehouse_meta')

    # Yardstick: minimal achievable Sigma f*D + full-labor floor W* (pure global-W
    # optimum) for a set of orders under a given pick cost.  Identical across strategies,
    # so plots report each strategy's realised metric as a fraction of this optimum.
    # Cheap (sort, no placement, no mutation).  Computed PER CHANNEL below, over that
    # channel's regime-filtered orders + its own speeds/cost — the only thing separating
    # the channels is the pick-time cost, so each section gets its own honest optimum
    # (a store-speed yardstick over the mixed catalog would be apples-to-oranges).
    def _yardsticks(orders_subset, x_speed, y_speed, wp_):
        if warehouse_meta is None or not orders_subset:
            return 0.0, 0.0
        _freq = {c.sku: c.demand.relative_frequency for c in orders_subset}
        _qty  = {c.sku: c.demand.quantity_rate for c in orders_subset}
        _mgr  = Inventory_Manager(warehouse_meta, affinity=None)
        sfd = _mgr.optimal_sigma_fd(orders_subset, _freq, x_speed, y_speed)
        wrk = _mgr.optimal_work(orders_subset, _freq, _qty, wp_)
        return sfd, wrk

    log.info(f'{"="*64}')
    log.info(f'  Config : {name}  [{channel.name}]')
    log.info(f'  w={pick_cfg.pick_weight_coef}  v={pick_cfg.pick_volume_coef}  '
             f'i={pick_cfg.pick_intercept}  c={pick_cfg.cart_swap_coef}')
    log.info(f'{"="*64}')

    config_record = {
        'name'            : name,
        'pick_weight_coef': pick_cfg.pick_weight_coef,
        'pick_volume_coef': pick_cfg.pick_volume_coef,
        'pick_weight_fn'  : pick_cfg.pick_weight_fn,
        'pick_volume_fn'  : pick_cfg.pick_volume_fn,
        'pick_intercept'  : pick_cfg.pick_intercept,
        'cart_swap_coef'  : pick_cfg.cart_swap_coef,
        'cart'            : pick_cfg.cart.__name__,
        'cart_capacity'   : pick_cfg.cart.capacity(),
        'x_speed'         : pick_cfg.x_speed,
        'y_speed'         : pick_cfg.y_speed,
        'num_pickers'     : pick_cfg.num_pickers,
        'load_lambda'     : load_params.lambda_,
        'load_k'          : load_params.k,
        'load_gamma'      : load_params.gamma,
        'total_aisles'    : total_aisles,
        'total_bins'      : total_bins,
        'n_skus'          : len(inventory.orders),
        'total_units'     : total_units_needed,
        'bin_slack_pct'   : round((total_bins / max(total_units_needed, 1) - 1) * 100, 2),
        'batch_mean_frac' : channel.batch_mean_fraction,   # this channel's actual mean fraction
        'n_batches'       : n_batches,
        'seed_world'      : SEED_WORLD,
        'seed_batches'    : SEED_BATCHES + channel.batch_seed_offset,   # this channel's actual seed
        'avg_equilibrium_qty': round(sum(getattr(c, 'equilibrium_qty', 1)
                                         for c in inventory.orders) / max(len(inventory.orders), 1), 1),
        'avg_reorder_point'  : round(sum(getattr(c, 'reorder_point', 1)
                                         for c in inventory.orders) / max(len(inventory.orders), 1), 2),
        'avg_lead_time_mean' : round(sum(getattr(c, 'lead_time_mean', 0.0)
                                         for c in inventory.orders) / max(len(inventory.orders), 1), 3),
        'avg_supply_cv'      : round(sum(getattr(c, 'supply_cv', 0.0)
                                         for c in inventory.orders) / max(len(inventory.orders), 1), 3),
        # Height-bracket multipliers M(y): (upper_y_phys | null, multiplier). Emitted so the
        # docs can render M(y) programmatically instead of transcribing it from code.
        'height_brackets'    : [[(None if thr == float('inf') else thr), mult]
                                for thr, mult in getattr(pick_cfg, 'height_brackets', ())],
    }
    with open(os.path.join(run_dir, 'config.json'), 'w') as f:
        json.dump(config_record, f, indent=2)

    keyframe_interval = int(shared.get('keyframe_interval', 5) or 0)
    # Run configuration recorded per run for reconstruction/replay.
    run_params = dict(
        num_pickers       = pick_cfg.num_pickers,
        x_speed           = pick_cfg.x_speed,
        y_speed           = pick_cfg.y_speed,
        pick_intercept    = pick_cfg.pick_intercept,
        pick_weight_coef  = pick_cfg.pick_weight_coef,
        pick_volume_coef  = pick_cfg.pick_volume_coef,
        cart_swap_coef    = pick_cfg.cart_swap_coef,
        k_pickers         = channel.picker.num_pickers,
        n_batches         = n_batches,
        seed_world        = SEED_WORLD,
        keyframe_interval = keyframe_interval,
        # Placeholders — overridden PER CHANNEL below with that section's own yardsticks.
        optimal_sigma_fd  = 0.0,
        optimal_work      = 0.0,
    )

    # Profile (inventory) label + per-strategy decomposition (initial | assignment | reslot,
    # split from the strategy label) so graphs can title plots without re-parsing the registry.
    _profile = os.path.basename(pair_dir.rstrip('/\\')) or 'profile'

    def _decomp(lbl: str) -> dict:
        parts = (lbl.split('|') + ['', '', ''])[:3]
        return dict(initial=parts[0], assignment=parts[1], reslot=parts[2])

    # Workers load the PLANNED inventory DB (grown equilibrium_qty + cross-tier stock plans)
    # when available so they reproduce the placement the warehouse was sized for.
    _planned_db   = shared.get('planned_inv_db')
    _worker_invdb = _planned_db or shared['inv_db']
    _worker_allow = None if _planned_db else shared.get('sku_allowlist')
    _worker_maxsk = None if _planned_db else shared.get('max_skus')

    # ── one worker set for THIS channel over the shared warehouse ────────────────────
    # The channel filters inventory to its regime (mixed catalog) and simulates with its own
    # picker cost + pool + batch stream, writing its own DB subtree.  A store-only catalog
    # (mixed False) uses the config dir directly + the precomputed pair-level batch stream →
    # byte-identical to the pre-channel pipeline.
    ch = channel
    # This channel's strategy arms — a restock subset (e.g. store: fifo + rank_labor) or the
    # full grid (fulfillment).  All per-channel work below iterates ch_strategies.
    ch_strategies = strategies_for(ch.restocks)
    ch_run_dir = os.path.join(run_dir, ch.name) if mixed else run_dir
    os.makedirs(ch_run_dir, exist_ok=True)
    ch_db_path = {s.key: os.path.join(ch_run_dir, f'sim_{s.key}.db') for s in ch_strategies}

    if mixed:
        # Channel-specific cost + pool + a REGIME-PURE precomputed batch stream shared across
        # ALL this channel's configs (fingerprint-keyed on the regime-filtered inventory + seed
        # + batch_cfg, so the first config computes it and the rest reuse) — apples-to-apples
        # across assignment functions and far less compute.
        ch_pick_cfg     = replace(ch.picker.cost, num_pickers=ch.picker.num_pickers)
        ch_wp           = WorkloadParams.from_pick_config(ch_pick_cfg)
        ch_wp.by_regime = None
        _ch_size        = sum(1 for c in inventory.orders if regime_of(c) == ch.regime)
        ch_batch_cfg    = ch.batch_config(max(1, _ch_size))
        ch_seed_batches = SEED_BATCHES + ch.batch_seed_offset
        ch_regime       = ch.regime
    else:
        # Store-only path: precomputed pair-level shared batch stream (whole catalog = store).
        ch_pick_cfg, ch_wp = pick_cfg, wp
        ch_batch_cfg    = batch_cfg
        ch_seed_batches = SEED_BATCHES
        ch_regime       = None
    try:
        ch_batches_path, ch_batches_fp = ensure_batches(
            pair_dir, _worker_invdb, _worker_maxsk, _worker_allow, shared['aff_db'],
            ch_batch_cfg, ch_seed_batches, n_batches, workers=workers, log=log,
            channel_regime=ch_regime)
    except Exception as exc:                   # noqa: BLE001 — never block on precompute
        log.warning(f'  batch precompute failed ({exc!r}); workers will sample inline')
        ch_batches_path, ch_batches_fp = None, None

    # Per-channel yardsticks over THIS section's orders + its own speeds/cost.
    _ch_orders = [c for c in inventory.orders if regime_of(c) == ch.regime]
    ch_optimal_sigma_fd, ch_optimal_work = _yardsticks(
        _ch_orders, ch_pick_cfg.x_speed, ch_pick_cfg.y_speed, ch_wp)
    ch_run_params = {**run_params,
                     'optimal_sigma_fd': ch_optimal_sigma_fd,
                     'optimal_work': ch_optimal_work}
    log.info(f'  [{ch.name}] Optimal Sigma f*D = {ch_optimal_sigma_fd:,.1f}  '
             f'W* floor = {ch_optimal_work:,.1f}')

    resume = _load_resume(ch_run_dir)
    if resume:
        run_ids = resume['run_ids']
        prev    = resume.get('next_batch', {})
        starts  = {s.key: (load_worker_checkpoint(ch_run_dir, s.key) or prev.get(s.key, 0))
                   for s in ch_strategies}
        log.info(f'  Resuming [{ch.name}]  '
                 + '  '.join(f'{s.key}@{starts[s.key]}' for s in ch_strategies))
    else:
        run_ids = {}
        _pair_label = os.path.basename(pair_dir.rstrip('/\\'))
        _identity = dict(
            pair_label            = _pair_label,
            config_label          = name,
            warehouse_fingerprint = shared.get('warehouse_fingerprint'),
            inventory_label       = _pair_label,
            channel               = ch.name,
        )
        for s in ch_strategies:
            init_run_db(ch_db_path[s.key])
            run_ids[s.key] = create_run(
                ch_db_path[s.key], s.run_type, ch_run_params,
                identity={**_identity, 'strategy_key': s.key})
        starts = {s.key: 0 for s in ch_strategies}
        log.info(f'  New run [{ch.name}]  '
                 + '  '.join(f'{s.key}={run_ids[s.key]}' for s in ch_strategies))
    _save_resume(ch_run_dir, run_ids, starts)

    _shared = dict(
        inv_db              = _worker_invdb,
        batches_path        = ch_batches_path,
        batches_fingerprint = ch_batches_fp,
        aff_db              = shared['aff_db'],
        run_dir             = ch_run_dir,
        n_batches           = n_batches,
        k_pickers           = ch.picker.num_pickers,
        seed_world          = SEED_WORLD,
        seed_batches        = ch_seed_batches,
        checkpoint          = _checkpoint_every(n_batches),
        max_skus            = _worker_maxsk,
        sku_allowlist       = _worker_allow,
        keyframe_interval   = keyframe_interval,
        warehouse_cfg       = warehouse_cfg,
        pick_cfg            = ch_pick_cfg,
        wp                  = ch_wp,
        load_params         = load_params,
        batch_cfg           = ch_batch_cfg,
        channel_regime      = ch_regime,      # worker filters inventory to this regime
        channel_name        = ch.name,
        # log_queue is NOT set here — injected by the flat pool (_run_workers_flat)
    )
    strategy_args = [{**_shared, 'strategy': s.key, 'run_id': run_ids[s.key],
                      'start_i': starts[s.key], 'db_path': ch_db_path[s.key],
                      'channel_key': ch.name}
                     for s in ch_strategies]
    sim_skeleton = dict(
        name       = name,
        inventory  = _profile,
        run_dir    = ch_run_dir,
        channel    = ch.name,
        strategies = [dict(key=s.key, label=s.label, color=s.color,
                           db_path=ch_db_path[s.key], run_id=run_ids[s.key],
                           **_decomp(s.label))
                      for s in ch_strategies],
        optimal_sigma_fd = ch_optimal_sigma_fd,
        optimal_work     = ch_optimal_work,
        inv_db     = shared['inv_db'],
        aff_db     = shared['aff_db'],
    )
    return strategy_args, [sim_skeleton]


def _channel_runs_for(inventory) -> tuple[bool, list[tuple]]:
    """Plan the channel-runs for one catalog.

    Store and fulfillment sweep their OWN config sets independently (a UNION, not a cross
    product): every STORE_CONFIGS entry drives a store channel-run, and — only when the
    catalog is mixed (contains fulfillment units) — every FULFILLMENT_CONFIGS entry drives a
    fulfillment channel-run.  A store-only catalog yields just the store runs (byte-identical
    to the pre-fulfillment pipeline).

    Returns (mixed, [(channel, cfg), ...]) where each channel carries its own pick cost + pool.
    """
    from Optimization.channels import make_channel                         # noqa: E402
    from Warehouse.regime import regime_of                              # noqa: E402

    mixed = any(regime_of(c) == FULFILLMENT for c in inventory.orders)
    runs: list[tuple] = []
    # Store first, then fulfillment (only for a mixed catalog) — every knob per channel
    # comes from CONFIG['channels'][name]; a pick-config entry may override 'num_pickers'.
    for name in ('store', 'fulfillment'):
        if name == 'fulfillment' and not mixed:
            continue
        chan = CONFIG['channels'][name]
        default_cart = _CART_TYPES.get(chan['cart'], StoreCart)
        for cfg in chan['configs']:
            n  = int(cfg.get('num_pickers', chan['num_pickers']))
            pc = _build_pick_cfg(cfg, num_pickers=n, default_cart=default_cart)
            ch = make_channel(name, chan['regime'], pc, n,
                              restocks=chan['restocks'],
                              batch_seed_offset=chan['seed_offset'],
                              batch_mean_fraction=chan['batch']['mean'],
                              batch_std_fraction=chan['batch']['std'])
            runs.append((ch, cfg))
    return mixed, runs


def _finalize_config_run(sim_skeleton: dict) -> dict:
    """Post-completion: remove resume file and write sim_meta.json.

    Returns the sim_result dict (subset of sim_skeleton without inv_db/aff_db).
    """
    run_dir = sim_skeleton['run_dir']
    rp = _resume_path(run_dir)
    if os.path.exists(rp):
        os.remove(rp)
    _cleanup_checkpoints(run_dir)   # config complete — clear its per-strategy _ckpt_*.pkl
    # Additive runs: if a sim_meta.json already exists (e.g. a --resume run adding
    # a NEW strategy into a prior comparison dir), MERGE the strategy lists instead
    # of overwriting, so run_analysis sees the previously-run strategies plus the
    # new one.  Strategies are de-duplicated by key (the new run wins on collision).
    meta_path = os.path.join(run_dir, 'sim_meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as _f:
                prev = json.load(_f)
        except (OSError, ValueError):
            prev = {}
        new_keys = {s['key'] for s in sim_skeleton.get('strategies', [])}
        kept     = [s for s in prev.get('strategies', []) if s.get('key') not in new_keys]
        sim_skeleton = {**prev, **sim_skeleton,
                        'strategies': kept + sim_skeleton.get('strategies', [])}
    with open(meta_path, 'w') as _f:
        json.dump(sim_skeleton, _f, indent=2)
    return {k: sim_skeleton[k]
            for k in ('name', 'inventory', 'run_dir', 'strategies',
                      'optimal_sigma_fd', 'optimal_work')
            if k in sim_skeleton}


def _warn_blank_arms(base_dir: str, log: logging.Logger) -> list:
    """Scan every sim_*.db under base_dir and log a prominent WARNING for any that
    recorded zero batches (blank result DB).  Returns the list of blank db paths.

    A blank arm means the whole strategy produced no metrics — almost always a
    placement/stocking failure (units never binned → no pick tasks → all batches
    skipped).  Downstream analysis silently omits such arms, so we flag them here.
    """
    import sqlite3
    blank = []
    for _run, db in iter_sim_dbs(base_dir):
        try:
            con = sqlite3.connect(db)
            n = con.execute('SELECT COUNT(*) FROM batch_stats').fetchone()[0]
            con.close()
        except Exception:                       # noqa: BLE001 — never block on the scan
            continue
        if n == 0:
            blank.append(db)
    if blank:
        log.warning(f'{"!"*64}')
        log.warning(f'  {len(blank)} BLANK sim DB(s) — recorded ZERO batches (no metrics):')
        for db in sorted(blank):
            log.warning(f'    {os.path.relpath(db, base_dir)}')
        log.warning('  These arms produced no data (likely a placement/stocking failure).')
        log.warning(f'{"!"*64}')
    else:
        log.info('  Metric check: every sim DB recorded ≥1 batch (no blanks).')
    return blank


def _run_workers_flat(
    pairs              : list,
    base_dir           : str,
    shared_by_pair     : dict,
    max_workers        : int,
    log                : logging.Logger,
    max_tasks_per_child: int | None = 1,
    skip_completed     : bool = False,
) -> None:
    """Flat ProcessPoolExecutor pool — zero idle time between A/B/C barriers.

    All (pair, config, strategy) work units are submitted to a single shared
    pool.  A free worker immediately picks up the next unit regardless of
    which pair or config it belongs to.  When all 3 strategies for a
    (pair, config) complete, sim_meta.json is written.

    Graph generation is decoupled: run run_analysis.py <base_dir> afterwards.
    """
    mp_manager = multiprocessing.Manager()
    log_queue  = mp_manager.Queue(-1)
    listener   = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True
    )
    listener.start()
    log.info('  Log listener started')

    try:
        # ── pre-init: build all work units in (pair, config) order ───────────
        work_units: list[tuple[tuple, dict]] = []   # ((label, cfg_name), args_dict)
        meta: dict[tuple, dict] = {}                # key → {sim_skeleton, remaining}

        for label, inv_db, aff_db in pairs:
            pair_dir = os.path.join(base_dir, label)
            shared   = shared_by_pair[label]

            # Store and fulfillment sweep their own config sets independently — a UNION of
            # channel-runs (store configs → store channel; fulfillment configs → fulfillment
            # channel, only when the catalog is mixed).  See _channel_runs_for.
            mixed, channel_runs = _channel_runs_for(shared['inventory'])
            for ch, cfg in channel_runs:
                cfg_name = _config_name(cfg)
                # A channel-run's outputs live at <cfg>/<channel>/ (mixed catalog) or <cfg>/
                # (store-only).  The skip guard must key on that exact dir — sim_meta.json and
                # resume.pkl land there, not at the config level.
                ch_run_dir = os.path.join(pair_dir, cfg_name, ch.name) if mixed \
                             else os.path.join(pair_dir, cfg_name)
                # On resume, a channel-run that finalized has written sim_meta.json and had its
                # resume marker removed (_finalize_config_run).  Skip it so resume never
                # recomputes already-complete work — _prepare_channel_run would otherwise see no
                # resume.pkl and restart it from batch 0.
                if skip_completed and os.path.exists(os.path.join(ch_run_dir, 'sim_meta.json')) \
                        and not os.path.exists(_resume_path(ch_run_dir)):
                    log.info(f'  [{label}/{cfg_name}/{ch.name}] already complete — skipping (resume)')
                    continue
                try:
                    strategy_args, sim_skeletons = _prepare_channel_run(
                        ch, cfg, mixed, shared, pair_dir, log, workers=max_workers)
                    for sa in strategy_args:
                        sa['log_queue'] = log_queue   # inject shared queue
                        ck = (label, cfg_name, sa.get('channel_key', ''))
                        work_units.append((ck, sa))
                    # One completion group per (pair, config, channel): all of THAT channel's
                    # strategies must finish before its sim_meta.json is written.  Channels may
                    # run different-sized subsets (e.g. store: fifo+rank_labor vs fulfillment:
                    # full suite), so count the skeleton's own strategies, not the global grid.
                    for sk in sim_skeletons:
                        ck = (label, cfg_name, sk.get('channel', ''))
                        meta[ck] = {'sim_skeleton': sk, 'remaining': len(sk['strategies'])}
                except Exception as exc:
                    log.error(f'  [{label}/{cfg_name}/{ch.name}] prepare FAILED: {exc}',
                              exc_info=True)

        # Assign a 1-based job index + identity tag to each work unit so every
        # worker log line can show which job of the flat list is progressing.
        total_jobs = len(work_units)
        for idx, (key, sa) in enumerate(work_units, start=1):
            _label, _cfg_name, _ch = key
            sa['job_index'] = idx
            sa['job_total'] = total_jobs
            _pfx = f'{_label}/{_cfg_name}/{_ch}' if _ch else f'{_label}/{_cfg_name}'
            sa['job_tag']   = f'{_pfx}/{sa["strategy"]}'

        # max_tasks_per_child recycles each worker process after this many jobs so
        # the OS reclaims its full RSS between simulations.  CPython rarely returns
        # freed arenas to the OS, so without recycling each long-lived worker's RSS
        # ratchets to its high-water mark and pins memory at 95%+, swap-thrashing
        # every worker's DB writes.  None = never recycle (legacy behaviour).
        recycle = max_tasks_per_child if max_tasks_per_child and max_tasks_per_child > 0 else None
        log.info(f'  Flat pool: {total_jobs} jobs'
                 f' -> ProcessPoolExecutor({max_workers}, '
                 f'max_tasks_per_child={recycle})')

        # ── execute ───────────────────────────────────────────────────────────
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=max_workers, max_tasks_per_child=recycle) as pool:
            futures = {
                pool.submit(_run_strategy_worker, args): key
                for key, args in work_units
            }
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                label, cfg_name, ch_key = key
                _tag = f'{label}/{cfg_name}/{ch_key}' if ch_key else f'{label}/{cfg_name}'
                try:
                    res = fut.result()
                    log.info(f'  [{_tag}] strategy-{res["strategy"]} done'
                             f'  batches={res["done"]}  wall={res["elapsed"]:.1f}s')
                except Exception as exc:
                    log.error(f'  [{_tag}] strategy FAILED: {exc}', exc_info=True)

                if key in meta:
                    meta[key]['remaining'] -= 1
                    if meta[key]['remaining'] <= 0:
                        try:
                            _finalize_config_run(meta[key]['sim_skeleton'])
                            log.info(f'  [{_tag}] sim_meta.json written')
                        except Exception as exc:
                            log.error(f'  [{_tag}] finalize FAILED: {exc}', exc_info=True)
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Warehouse assignment comparison — uses the newest generated inventory+affinity pair.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--profiles-dir', default=_DEFAULT_PROFILES_DIR,
                        help='Root directory produced by generate_profile_suite.py')
    parser.add_argument('--all-profiles', action='store_true',
                        help='Run every profile pair instead of only the newest')
    parser.add_argument('--resume', metavar='BASE_DIR', default=None,
                        help='Resume a previous run by passing its base directory')
    parser.add_argument('--workers', type=int, default=1, metavar='N',
                        help='Flat ProcessPoolExecutor pool size — every '
                             '(pair,config,strategy) work unit shares one pool. '
                             'Default 1 (sequential).')
    parser.add_argument('--max-tasks-per-child', type=int, default=1, metavar='N',
                        help='Recycle each pool worker after N jobs so the OS reclaims '
                             'its full memory between simulations (default 1 = fresh '
                             'process per job, the cleanest flush). Workers reload all '
                             'assets per job anyway, so spawn cost is negligible vs '
                             'multi-hour jobs. Raise it for fewer spawns at the cost of '
                             'flush completeness; 0 disables recycling (legacy).')
    parser.add_argument('--max-skus', type=int, default=None, metavar='N',
                        help='Cap inventory to the first N SKUs (smaller warehouse for quick runs)')
    # Per-channel warehouse-sizing caps (store vs fulfillment sized independently).
    parser.add_argument('--s-max-aisles', type=int, default=None, metavar='N',
                        help='STORE: cap total store aisle count (scales store replicas down).')
    parser.add_argument('--s-max-bins', type=int, default=None, metavar='N',
                        help='STORE: cap total store bins (trims store aisle replicas).')
    parser.add_argument('--s-min-bins', type=int, default=None, metavar='N',
                        help='STORE: require AT LEAST N store bins (min wins over --s-max-bins).')
    parser.add_argument('--ff-max-aisles', type=int, default=None, metavar='N',
                        help='FULFILLMENT: cap total fulfillment aisle count.')
    parser.add_argument('--ff-max-bins', type=int, default=None, metavar='N',
                        help='FULFILLMENT: cap total fulfillment bins (scales the fixed tier '
                             'distribution down).')
    parser.add_argument('--ff-min-bins', type=int, default=None, metavar='N',
                        help='FULFILLMENT: total fulfillment bins to scale the fixed tier '
                             'distribution to (also the floor).')
    parser.add_argument('--s-composition', type=str, default=None, metavar='JSON',
                        help='STORE: JSON file or inline factored basis vector of store bin '
                             'ratios (keys handling/category/size/unit → weight). Scale from '
                             '--s-min-bins (or demand). Fulfillment uses its fixed distribution.')
    parser.add_argument('--keyframe-interval', type=int, default=5, metavar='K',
                        help='Write a full bin snapshot to <run>.keyframes.db every K '
                             'batches so the visualizer can jump between batches '
                             '(0 disables). A keyframe = all occupied bins; raise K for '
                             'very large warehouses. Default 5.')
    parser.add_argument('--n-batches', type=int, default=None, metavar='N',
                        help='Override the per-run batch count (default '
                             f'{CONFIG["global"]["n_batches"]}). Use a small value for quick smoke runs.')
    args = parser.parse_args()

    # ── apply CLI overrides onto CONFIG (the single source of truth) ─────────────
    g = CONFIG['global']
    if args.n_batches:
        g['n_batches'] = args.n_batches
    if args.max_skus is not None:
        g['max_skus'] = args.max_skus
    g['workers']           = args.workers or 1
    g['keyframe_interval'] = args.keyframe_interval

    _store_comp = None
    if args.s_composition:
        if os.path.exists(args.s_composition):
            with open(args.s_composition) as _cf:
                _store_comp = json.load(_cf)
        else:
            _store_comp = json.loads(args.s_composition)

    _ss = CONFIG['channels']['store']['sizing']
    _ss.update(max_aisles=args.s_max_aisles, max_bins=args.s_max_bins,
               min_bins=args.s_min_bins, composition=_store_comp)
    _fs = CONFIG['channels']['fulfillment']['sizing']
    _fs.update(max_aisles=args.ff_max_aisles, max_bins=args.ff_max_bins,
               min_bins=args.ff_min_bins)

    if args.resume:
        base_dir = args.resume if os.path.isabs(args.resume) else os.path.join(_OUTPUT_DIR, args.resume)
        if not os.path.isdir(base_dir):
            sys.exit(f'Resume directory not found: {base_dir}')
    else:
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_dir = os.path.join(_OUTPUT_DIR, f'comparison_{ts}')
        os.makedirs(base_dir, exist_ok=True)

    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    log.info(f'Output directory : {base_dir}')
    log.info(f'Profiles dir     : {args.profiles_dir}')
    log.info(f'Mode             : {"all profiles" if args.all_profiles else "latest profile only"}')

    if args.all_profiles:
        pairs = discover_db_pairs(args.profiles_dir)
    else:
        pairs = find_latest_db_pairs(args.profiles_dir)

    if not pairs:
        sys.exit(f'No inventory+affinity DB pairs found in: {args.profiles_dir}')

    # Sanity-check: each inv_db path must be unique.  Duplicate paths indicate
    # a broken profile directory structure and would cause misleading results.
    seen_inv: dict[str, str] = {}
    for label, inv_db, _aff in pairs:
        if inv_db in seen_inv:
            log.warning(f'  DUPLICATE inv_db detected!')
            log.warning(f'    first seen as : {seen_inv[inv_db]}')
            log.warning(f'    repeated as   : {label}')
            log.warning(f'    path          : {inv_db}')
        else:
            seen_inv[inv_db] = label

    n_unique = len(seen_inv)
    log.info(f'Discovered {len(pairs)} DB pair(s)  ({n_unique} unique inventories):')
    for label, inv_db, aff_db in pairs:
        log.info(f'  {label}')
        log.info(f'    inv : {inv_db}')
        log.info(f'    aff : {aff_db}')

    n_store = len(STORE_CONFIGS)
    n_ff    = len(FULFILLMENT_CONFIGS)
    n_strats  = len(STRATEGIES)
    workers = args.workers or 1
    log.info(
        f'Execution plan: {len(pairs)} pair(s) × ({n_store} store + up to {n_ff} fulfillment) '
        f'config(s), swept independently per channel  |  flat pool workers={workers}'
    )

    # Top-level run manifest: a schema index of this run — see sim_manifest.
    write_run_manifest(base_dir, pairs, STORE_CONFIGS, FULFILLMENT_CONFIGS, STRATEGIES)
    log.info(f'Wrote run_manifest.json ({len(pairs)} inv × {n_store} store + {n_ff} ff cfg × {n_strats} strat)')
    # Per-regime warehouse sizing assembled from CONFIG (shared with run_analysis's rebuild).
    regime_sizing = regime_sizing_from_config()

    # ── flat ProcessPoolExecutor: every (pair,config,strategy) unit shares one pool ──
    shared_by_pair = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
        shared_by_pair[label] = build_shared_assets(
            inv_db, aff_db, log,
            max_skus=g['max_skus'], regime_sizing=regime_sizing,
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(base_dir, label, 'warehouse.db'),
        )
    _run_workers_flat(pairs, base_dir, shared_by_pair, workers, log,
                      max_tasks_per_child=args.max_tasks_per_child,
                      skip_completed=bool(args.resume))

    # Loud blank-arm check: surface any sim_*.db that completed with ZERO recorded
    # batches (e.g. an arm whose placement left no stock so every batch is skipped),
    # so a blank DB is discovered NOW, not halfway through downstream analysis.
    _warn_blank_arms(base_dir, log)

    log.info(f'\nAll {len(pairs)} dataset(s) × ({n_store} store + {n_ff} ff) config(s) simulations complete.'
             f'  Root: {base_dir}'
             f'\n  Run graphs: python run_analysis.py {base_dir}')


if __name__ == '__main__':
    main()
