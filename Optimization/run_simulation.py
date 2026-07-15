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
from concurrent.futures.process import BrokenProcessPool
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
    _write_run_spec, _load_run_spec, _run_spec_path,
    write_run_layout, read_run_layout, _run_layout_path,
)


from Warehouse.Inventory_Management import Inventory_Manager
from Optimization import strategies                       # noqa: F401  (--whatif arm override)
from Optimization.strategies import STRATEGIES, strategies_for
from Warehouse.Storage_Primitive import StoreCart

from Optimization.Picking_Data import create_run, init_run_db
from Optimization.Workload import WorkloadParams
from Warehouse.regime import STORE, FULFILLMENT

from Optimization.strategy_runner import (
    load_worker_checkpoint, _run_strategy_worker, _cleanup_checkpoints, reset_strategy_db,
)
from Optimization.batch_precompute import ensure_batches


# ── DB helpers ─────────────────────────────────────────────────────────────────


# Directory-layout walkers live in runlayout (single owner of the tree shapes);
# re-imported here so rs.discover_db_pairs / rs.find_latest_db_pairs keep working.
from Optimization.runlayout import discover_db_pairs, find_latest_db_pairs, iter_sim_dbs  # noqa: F401,E402


def _plan_strategy_start(ch_run_dir, s, n_batches, db_path, run_params, identity,
                         granularity, prev_id, prev_start, is_resume, log):
    """Decide (run_id, start_batch) for one strategy, honoring resume granularity.

    - Fresh run / a strategy new on resume → init DB + create_run, start 0.
    - Resumed done arm (checkpoint ≥ n_batches) → reuse run_id, start n_batches (empty loop).
    - Resumed PARTIAL arm (0 < ckpt < n_batches):
        strategy granularity → reset the arm's DB + fresh run_id, start 0 (bit-identical to an
                               uncrashed run — no un-replayed physical state);
        batch granularity    → reuse run_id, start = ckpt (fast, but NOT bit-identical — warn).
    """
    if not is_resume or prev_id is None:
        init_run_db(db_path)
        return create_run(db_path, s.run_type, run_params,
                          identity={**identity, 'strategy_key': s.key}), 0
    ckpt = load_worker_checkpoint(ch_run_dir, s.key) or prev_start
    if 0 < ckpt < n_batches:
        if granularity == 'strategy':
            reset_strategy_db(ch_run_dir, db_path, s.key)
            init_run_db(db_path)
            log.info(f'  [{s.key}] strategy-level reset -> batch 0 (bit-identical)')
            return create_run(db_path, s.run_type, run_params,
                              identity={**identity, 'strategy_key': s.key}), 0
        log.warning(f'  [{s.key}] batch-level resume @ {ckpt}: NOT bit-identical to an uncrashed '
                    f'run (un-replayed physical state). Use --resume-granularity strategy for '
                    f'exact cross-arm comparability.')
    return prev_id, ckpt


def _prepare_channel_run(
    channel,                 # channels.Channel — its picker carries this run's cost + pool
    cfg     : dict,
    mixed   : bool,          # catalog has >1 regime → regime-filter + <config>/<channel>/ subdir
    shared  : dict,
    pair_dir: str,
    log     : logging.Logger,
    workers : int = 1,
    resume_granularity: str = 'strategy',
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

    _pair_label = os.path.basename(pair_dir.rstrip('/\\'))
    _identity = dict(
        pair_label            = _pair_label,
        config_label          = name,
        warehouse_fingerprint = shared.get('warehouse_fingerprint'),
        inventory_label       = _pair_label,
        channel               = ch.name,
    )
    resume      = _load_resume(ch_run_dir)
    prev_ids    = resume['run_ids'] if resume else {}
    prev_starts = resume.get('next_batch', {}) if resume else {}
    run_ids, starts = {}, {}
    for s in ch_strategies:
        run_ids[s.key], starts[s.key] = _plan_strategy_start(
            ch_run_dir, s, n_batches, ch_db_path[s.key], ch_run_params, _identity,
            resume_granularity, prev_ids.get(s.key), prev_starts.get(s.key, 0),
            resume is not None, log)
    if resume:
        log.info(f'  Resuming [{ch.name}]  '
                 + '  '.join(f'{s.key}@{starts[s.key]}' for s in ch_strategies))
    else:
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
        velocity_zoning     = CONFIG['channels'].get(ch.name, {}).get('velocity_zoning'),
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


def _build_work_units(pairs, base_dir, shared_by_pair, log, log_queue, max_workers,
                      skip_completed, resume_granularity):
    """(Re-)prepare all work units from on-disk state.

    Returns (work_units, meta):
      work_units : list of (uid, args) where uid = (label, cfg_name, channel, strategy).
      meta       : {group_key: {'sim_skeleton', 'members'}}; group_key = uid[:3].  A group is
                   finalized only when EVERY member uid succeeds (see _run_pool) — so a crashed
                   arm never finalizes its group, keeping resume.pkl and staying resumable.
    Re-derives each arm's start from its ADVANCED _ckpt_*.pkl, so resubmit/resume is idempotent.
    """
    work_units, meta = [], {}
    for label, inv_db, aff_db in pairs:
        pair_dir = os.path.join(base_dir, label)
        shared   = shared_by_pair[label]
        # Store & fulfillment sweep independent config sets — a union of channel-runs.
        mixed, channel_runs = _channel_runs_for(shared['inventory'])
        for ch, cfg in channel_runs:
            cfg_name = _config_name(cfg)
            # Outputs live at <cfg>/<channel>/ (mixed) or <cfg>/ (store-only); the skip guard
            # keys on that exact dir.  A finalized channel-run (sim_meta.json present AND
            # resume.pkl removed) is skipped so resume never recomputes complete work.
            ch_run_dir = os.path.join(pair_dir, cfg_name, ch.name) if mixed \
                         else os.path.join(pair_dir, cfg_name)
            if skip_completed and os.path.exists(os.path.join(ch_run_dir, 'sim_meta.json')) \
                    and not os.path.exists(_resume_path(ch_run_dir)):
                log.info(f'  [{label}/{cfg_name}/{ch.name}] already complete — skipping (resume)')
                continue
            try:
                strategy_args, sim_skeletons = _prepare_channel_run(
                    ch, cfg, mixed, shared, pair_dir, log, workers=max_workers,
                    resume_granularity=resume_granularity)
                for sa in strategy_args:
                    sa['log_queue'] = log_queue
                    uid = (label, cfg_name, sa.get('channel_key', ''), sa['strategy'])
                    work_units.append((uid, sa))
                for sk in sim_skeletons:
                    gk = (label, cfg_name, sk.get('channel', ''))
                    members = frozenset((*gk, s['key']) for s in sk['strategies'])
                    meta[gk] = {'sim_skeleton': sk, 'members': members}
            except Exception as exc:
                log.error(f'  [{label}/{cfg_name}/{ch.name}] prepare FAILED: {exc}', exc_info=True)
    return work_units, meta


def _finalize_ready_groups(meta, done_uids, finalized, log):
    """Finalize every group whose members have ALL succeeded and isn't finalized yet."""
    for gk, m in meta.items():
        if gk not in finalized and m['members'] <= done_uids:
            try:
                _finalize_config_run(m['sim_skeleton'])
                finalized.add(gk)
                log.info(f'  [{"/".join(x for x in gk if x)}] sim_meta.json written')
            except Exception as exc:
                log.error(f'  [{gk}] finalize FAILED: {exc}', exc_info=True)


def _run_pool(remaining, meta, max_workers, recycle, log, done_uids, finalized):
    """One ProcessPoolExecutor lifetime over `remaining` [(uid, args)].  Returns
    (failed_uids, broke).  A genuine success adds uid to done_uids and finalizes its group once
    ALL members succeeded; a hard worker death (BrokenProcessPool) sets broke and abandons the
    rest so the driver can rebuild + resubmit; an ordinary worker Exception is isolated."""
    failed_uids, broke = set(), False
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers, max_tasks_per_child=recycle) as pool:
        futures = {pool.submit(_run_strategy_worker, sa): uid for uid, sa in remaining}
        for fut in concurrent.futures.as_completed(futures):
            uid = futures[fut]
            gk  = uid[:3]
            _tag = '/'.join(x for x in gk if x) + f'/{uid[3]}'
            try:
                res = fut.result()
                log.info(f'  [{_tag}] done  batches={res["done"]}  wall={res["elapsed"]:.1f}s')
                done_uids.add(uid)
            except BrokenProcessPool:
                broke = True
                log.error('  [supervisor] worker pool BROKEN (hard worker death) — abandoning '
                          'this pool; unfinished units will be rebuilt + resubmitted')
                break
            except Exception as exc:
                log.error(f'  [{_tag}] strategy FAILED: {exc}', exc_info=True)
                failed_uids.add(uid)
                continue
            gk_meta = meta.get(gk)
            if gk_meta and gk not in finalized and gk_meta['members'] <= done_uids:
                try:
                    _finalize_config_run(gk_meta['sim_skeleton'])
                    finalized.add(gk)
                    log.info(f'  [{"/".join(x for x in gk if x)}] sim_meta.json written')
                except Exception as exc:
                    log.error(f'  [{gk}] finalize FAILED: {exc}', exc_info=True)
    return failed_uids, broke


def _supervise(pairs, base_dir, shared_by_pair, max_workers, log, *, log_queue,
               max_tasks_per_child, skip_completed, max_retries, resume_granularity):
    """Bounded retry driver: on a hard worker death, rebuild the pool and resubmit the
    unfinished units (each resumes from its on-disk checkpoint), up to max_retries.  Ordinary
    per-unit exceptions are NOT auto-retried (near-always deterministic bad-config).  Quarantine
    + continue: never abort the run, never infinite-loop; unrecovered units keep their resume
    state and are reported with a resume command.  max_tasks_per_child recycles workers so RSS
    is reclaimed between jobs (a clean recycle is NOT a broken pool)."""
    recycle = max_tasks_per_child if max_tasks_per_child and max_tasks_per_child > 0 else None
    done_uids, finalized = set(), set()
    work_units, meta = [], {}
    for attempt in range(max_retries + 1):
        work_units, meta = _build_work_units(
            pairs, base_dir, shared_by_pair, log, log_queue, max_workers,
            skip_completed=(skip_completed or attempt > 0),
            resume_granularity=resume_granularity)
        remaining = [(uid, sa) for uid, sa in work_units if uid not in done_uids]
        if not remaining:
            break
        total = len(remaining)
        for idx, (uid, sa) in enumerate(remaining, start=1):
            sa['job_index'], sa['job_total'] = idx, total
            sa['job_tag'] = '/'.join(x for x in uid[:3] if x) + f'/{uid[3]}'
        if attempt:
            log.warning(f'  [supervisor] retry {attempt}/{max_retries}: '
                        f'rebuild pool + resubmit {total} unit(s)')
        log.info(f'  Flat pool: {total} job(s) -> ProcessPoolExecutor({max_workers}, '
                 f'max_tasks_per_child={recycle})')
        _failed, broke = _run_pool(remaining, meta, max_workers, recycle,
                                   log, done_uids, finalized)
        if not broke:
            break            # pool completed; residual failures are deterministic → quarantine
    _finalize_ready_groups(meta, done_uids, finalized, log)   # safety sweep (rare finalize retry)
    all_uids = {uid for uid, _ in work_units} | done_uids
    unfinished = sorted(all_uids - done_uids)
    if unfinished:
        bar = '!' * 72
        log.error(bar)
        log.error(f'  {len(unfinished)}/{len(all_uids)} unit(s) UNRECOVERED after {max_retries} '
                  f'retr{"y" if max_retries == 1 else "ies"}. Resume state left intact.')
        for uid in unfinished:
            log.error('    ' + '/'.join(x for x in uid if x))
        log.error(f'  Resume with:  python Optimization/run_simulation.py --resume {base_dir}')
        log.error(bar)


def _run_workers_flat(
    pairs              : list,
    base_dir           : str,
    shared_by_pair     : dict,
    max_workers        : int,
    log                : logging.Logger,
    max_tasks_per_child: int | None = 1,
    skip_completed     : bool = False,
    max_retries        : int = 2,
    resume_granularity : str = 'strategy',
) -> None:
    """Flat ProcessPoolExecutor pool with automatic crash-recovery.

    All (pair, config, channel, strategy) units share one pool.  A worker that raises is
    isolated; a hard worker death that BREAKS the pool triggers a rebuild + resubmit of the
    unfinished units (each resumes from its on-disk checkpoint), up to max_retries.  A group's
    sim_meta.json is written only when ALL its strategies genuinely succeed, so a crashed arm
    keeps its resume.pkl and stays resumable.  The Manager/QueueListener live out here so worker
    deaths + pool rebuilds don't touch shared logging.

    Graph generation is decoupled: run run_analysis.py <base_dir> afterwards.
    """
    mp_manager = multiprocessing.Manager()
    log_queue  = mp_manager.Queue(-1)
    listener   = logging.handlers.QueueListener(
        log_queue, *log.handlers, respect_handler_level=True)
    listener.start()
    log.info('  Log listener started')
    try:
        _supervise(pairs, base_dir, shared_by_pair, max_workers, log,
                   log_queue=log_queue, max_tasks_per_child=max_tasks_per_child,
                   skip_completed=skip_completed, max_retries=max_retries,
                   resume_granularity=resume_granularity)
    finally:
        listener.stop()
        mp_manager.shutdown()
        log.info('  Log listener stopped')


# ── one scenario: manifest + shared-asset build + flat pool run ──────────────────
# The inner run pipeline for a SINGLE warehouse configuration.  Both the normal
# single-config run and each what-if matrix cell call this, so the per-pair glue
# lives in exactly one place.  frozen_by_pair maps label -> an already-planned
# inventory DB to reshape from (what-if freeze); None per label ⇒ sample fresh.
def _run_scenario(base_dir, pairs, regime_sizing, workers, log, *,
                  frozen_by_pair=None, skip_completed=False, max_tasks_per_child=1,
                  max_retries=2, resume_granularity='strategy'):
    write_run_manifest(base_dir, pairs, STORE_CONFIGS, FULFILLMENT_CONFIGS, STRATEGIES)
    g = CONFIG['global']
    shared_by_pair = {}
    for label, inv_db, aff_db in pairs:
        log.info(f'\n{"="*64}\n  Loading shared assets: {label}\n{"="*64}')
        shared_by_pair[label] = build_shared_assets(
            inv_db, aff_db, log,
            max_skus=g['max_skus'], regime_sizing=regime_sizing,
            keyframe_interval=g['keyframe_interval'],
            warehouse_db_path=os.path.join(base_dir, label, 'warehouse.db'),
            frozen_inventory_db=(frozen_by_pair or {}).get(label),
        )
    _run_workers_flat(pairs, base_dir, shared_by_pair, workers, log,
                      max_tasks_per_child=max_tasks_per_child,
                      skip_completed=skip_completed,
                      max_retries=max_retries, resume_granularity=resume_granularity)
    # Loud blank-arm check: surface any sim_*.db that completed with ZERO recorded
    # batches so a blank DB is discovered NOW, not halfway through downstream analysis.
    _warn_blank_arms(base_dir, log)


# ── what-if matrix (--whatif): aisle-reconstruction × velocity-zoning sweep ──────
# The experiment spec (cells, arms, reference) is committed data in whatif_config.py;
# everything below is the driver that consumes it.  The matrix reshapes ONE frozen
# inventory across every cell, so cells differ only in layout + zoning.

_SCHED_SHORT = {'round_robin': 'rr', 'lpt': 'lpt'}


def _build_cells(spec):
    """Combinatorial (scheduler × zoning × k × capacity_loss) cells from a WHATIF spec.  k=1 = no
    split (loss collapses to 0).  Returns [(name, aisle_split|None, zoning_spec, scheduler), …];
    the (k=1, off, round_robin) cell is the natural reference.  ``schedulers`` defaults to
    ['round_robin'] (byte-identical, no name suffix); a multi-value list adds a `_rr`/`_lpt`
    suffix so the picker-scheduler becomes a sweep axis alongside aisle_split and zoning."""
    scheds = spec.get('schedulers', ['round_robin'])
    multi_sched = len(scheds) > 1
    cells, seen = [], set()
    for sched in scheds:
        for zname, zspec in spec['zoning']:
            for k in spec['ks']:
                for loss in (spec['losses'] if k > 1 else [0.0]):
                    split = None if k <= 1 else {'k': k, 'capacity_loss': loss}
                    name = f'k{k}' + ('' if k <= 1 else f'_l{int(round(loss * 100))}') + f'_{zname}'
                    if multi_sched:
                        name += f'_{_SCHED_SHORT.get(sched, sched)}'
                    if name in seen:
                        continue
                    seen.add(name)
                    cells.append((name, split, dict(zspec), sched))
    return cells


def _apply_cell(aisle_split, zoning, scheduler='round_robin') -> None:
    """Mutate CONFIG for one cell: same aisle_split + velocity_zoning + picker scheduler on every
    channel.  The scheduler is a pick-config field (like one_way), so set it on each channel's
    pick-config dicts."""
    for ch in CONFIG['channels']:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)
        for cfg in CONFIG['channels'][ch]['configs']:
            cfg['scheduler'] = scheduler


def _tightest_split(cells):
    """The split with the largest capacity_loss (fewest bins).  Freezing the sampled
    inventory to it guarantees every roomier cell can hold it (the frozen inventory
    always fits)."""
    splits = [c[1] for c in cells
              if c[1] and c[1].get('capacity_loss', 0.0) > 0 and int(c[1].get('k', 1)) > 1]
    return max(splits, key=lambda s: s.get('capacity_loss', 0.0), default=None)


def _cell_complete(scenario_base: str, pairs: list) -> bool:
    """A cell is done when it has ≥1 sim_meta.json per pair (all its configs finalized)."""
    import glob
    if not os.path.isdir(scenario_base):
        return False
    return all(glob.glob(os.path.join(scenario_base, label, '**', 'sim_meta.json'), recursive=True)
               for label, _i, _a in pairs)


def _run_whatif_matrix(base_dir, pairs, log, spec, resume=False, max_retries=2,
                       resume_granularity='strategy'):
    """Drive a cell-matrix run from a spec (see whatif_config.SPECS): every run is a matrix, so a
    plain run is the single cell ``k1_off``.  A MULTI-cell matrix freezes the sampled inventory once
    (tightest cell) and reshapes it per cell (apples-to-apples); a SINGLE-cell run skips the freeze
    and samples fresh — bit-identical to the old flat run, just nested under its cell dir.  Returns
    {'cells': [names], 'reference': name}."""
    cells = _build_cells(spec)
    reference = next((c[0] for c in cells if c[1] is None and not c[2].get('enabled')
                      and c[3] == 'round_robin'), spec.get('reference') or cells[0][0])
    # Arm override: 'all' ⇒ full suite (CHANNEL_RESTOCKS=None); list ⇒ subset; None ⇒
    # leave strategies.CHANNEL_RESTOCKS exactly as committed.  CONFIG['restocks'] was
    # snapshotted at import, so refresh it too.
    if spec.get('arms') is not None:
        arms = None if str(spec['arms']).lower() == 'all' else tuple(spec['arms'])
        for ch in CONFIG['channels']:
            strategies.CHANNEL_RESTOCKS[ch] = arms
            CONFIG['channels'][ch]['restocks'] = strategies.restocks_for(ch)

    log.info(f'Cell matrix → {base_dir}  ({len(cells)} cell(s), reference={reference}, '
             f'arms={spec.get("arms")!r}, resume={resume})')
    log.info('  cells: ' + ', '.join(c[0] for c in cells))

    # ── 1. FREEZE the sampled inventory once (from the tightest cell) per pair — MULTI-cell only ──
    # (the scheduler is task→picker, not placement, so it doesn't affect the frozen layout).  A
    # single-cell run has nothing to share, so it samples fresh (frozen=None).
    g = CONFIG['global']
    frozen: dict | None = None
    if len(cells) > 1:
        _apply_cell(_tightest_split(cells), {'enabled': False}, 'round_robin')
        frozen = {}
        for label, inv_db, aff_db in pairs:
            frozen_db = os.path.join(base_dir, '_frozen', label, 'planned_inventory.db')
            if resume and os.path.exists(frozen_db):
                frozen[label] = frozen_db
                log.info(f'  reusing frozen inventory[{label}]')
                continue
            log.info(f'\n{"="*64}\n  FREEZE inventory (tightest cell): {label}\n{"="*64}')
            shared = build_shared_assets(
                inv_db, aff_db, log, max_skus=g['max_skus'],
                regime_sizing=regime_sizing_from_config(), keyframe_interval=g['keyframe_interval'],
                warehouse_db_path=os.path.join(base_dir, '_frozen', label, 'warehouse.db'))
            frozen[label] = shared['planned_inv_db']

    # ── 2. Each cell: reshape the warehouse (from FROZEN inv when multi-cell) + simulate ──
    for name, aisle_split, zoning, sched in cells:
        scenario_base = os.path.join(base_dir, name)
        if resume and _cell_complete(scenario_base, pairs):
            log.info(f'  SKIP cell {name} (already complete)')
            continue
        zdesc = zoning.get('mode', 'off') if zoning.get('enabled') else 'off'
        log.info(f'\n{"#"*64}\n  CELL {name}  split={aisle_split}  zoning={zdesc}  scheduler={sched}\n{"#"*64}')
        _apply_cell(aisle_split, zoning, sched)
        os.makedirs(scenario_base, exist_ok=True)
        _run_scenario(scenario_base, pairs, regime_sizing_from_config(), g['workers'], log,
                      frozen_by_pair=frozen, skip_completed=resume,
                      max_retries=max_retries, resume_granularity=resume_granularity)

    log.info(f'\nCell matrix complete → {base_dir}')
    return {'cells': [c[0] for c in cells], 'reference': reference}


# ── entry point ────────────────────────────────────────────────────────────────

def _apply_run_spec(args, spec, explicit):
    """Overlay a saved run_spec onto args for --resume: the saved value is the base; a flag the
    user explicitly typed on the resume command overrides it (with a warning).  Returns
    (store_composition_override, notes) — the resolved store composition is injected directly so
    a since-deleted --s-composition file can't break resume."""
    notes = []
    for f in ('n_batches', 'max_skus', 's_max_aisles', 's_max_bins', 's_min_bins',
              'ff_max_aisles', 'ff_max_bins', 'ff_min_bins', 'keyframe_interval', 'whatif', 'spec',
              'profiles_dir', 'all_profiles', 'workers', 'max_tasks_per_child',
              'max_retries', 'resume_granularity'):
        if f not in spec:
            continue
        if f in explicit:
            if getattr(args, f) != spec[f]:
                notes.append(f'  run_spec override: {f} {spec[f]!r} -> {getattr(args, f)!r} (explicit flag wins)')
        else:
            setattr(args, f, spec[f])
    return spec.get('s_composition'), notes


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
    parser.add_argument('--spec', default='single',
                        help='Cell-matrix spec to run (see whatif_config.SPECS). EVERY run is a cell '
                             "matrix: 'single' (default) = one cell k1_off (a plain run, nested under "
                             "its cell dir); 'scheduler_ab' = the round_robin vs lpt sweep. Output: "
                             'comparison[_whatif]_<ts>/<cell>/<pair>/<config>[/<channel>]/…')
    parser.add_argument('--whatif', action='store_true',
                        help='DEPRECATED alias for `--spec scheduler_ab` (the picker-scheduler A/B).')
    parser.add_argument('--no-analyze', action='store_true',
                        help='Skip the in-process analysis pass (per-cell graphs + cross-cell what-if '
                             'summaries) that otherwise runs automatically after the simulation.')
    parser.add_argument('--analysis-workers', type=int, default=None, metavar='N',
                        help='Pool size for the post-sim analysis pass (default: same as --workers).')
    parser.add_argument('--max-retries', type=int, default=2, metavar='N',
                        help='On a hard worker death (segfault/OOM) that breaks the pool, rebuild '
                             'the pool and resubmit the unfinished units up to N times before '
                             'quarantining them (default 2). Ordinary per-unit errors are not retried.')
    parser.add_argument('--resume-granularity', choices=('strategy', 'batch'), default='strategy',
                        help="On recovery, how to resume a partially-run strategy: 'strategy' "
                             '(default) restarts it from batch 0 (bit-identical to an uncrashed run, '
                             "comparison-safe); 'batch' continues from its last checkpoint (faster, "
                             'but NOT bit-identical — un-replayed cumulative physical state).')
    args = parser.parse_args()
    # Flags the user explicitly typed (used so a saved run_spec is the base but an explicit
    # flag on a resume command still wins).
    explicit = {d for d in vars(args) if getattr(args, d) != parser.get_default(d)}

    # Resolve base_dir FIRST, then on --resume load + apply the saved run_spec BEFORE the
    # CONFIG-override block, so a bare `--resume DIR` reconstructs the run with zero retyped
    # flags (and no find_latest_db_pairs drift — see the pairs block below).
    from Optimization.whatif_config import get_spec, SPECS

    def _resolve_spec():
        """Selected cell-matrix (name, dict).  --whatif is the deprecated alias for scheduler_ab."""
        name = 'scheduler_ab' if args.whatif else args.spec
        try:
            return name, get_spec(name)
        except KeyError:
            sys.exit(f'unknown --spec {name!r}; choices: {sorted(SPECS)}')

    spec, _spec_store_comp, _spec_notes = None, None, []
    if args.resume:
        base_dir = args.resume if os.path.isabs(args.resume) else os.path.join(_OUTPUT_DIR, args.resume)
        if not os.path.isdir(base_dir):
            sys.exit(f'Resume directory not found: {base_dir}')
        spec = _load_run_spec(base_dir)
        if spec:
            _spec_store_comp, _spec_notes = _apply_run_spec(args, spec, explicit)
        else:
            _spec_notes = ['  no run_spec.json in resume dir (pre-recovery run) — resuming with '
                           'code defaults + retyped flags; re-supply the original run-shaping flags '
                           'to avoid warehouse/inventory/batch-count drift']
        spec_name, spec_dict = _resolve_spec()          # after run_spec overlay (restores --spec)
    else:
        spec_name, spec_dict = _resolve_spec()
        n_cells  = len(_build_cells(spec_dict))          # >1 cell ⇒ a what-if sweep dir name
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix   = 'comparison_whatif' if n_cells > 1 else 'comparison'
        base_dir = os.path.join(_OUTPUT_DIR, f'{prefix}_{ts}')
        os.makedirs(base_dir, exist_ok=True)

    # ── apply CLI overrides onto CONFIG (the single source of truth; reconciled w/ run_spec) ──
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
    if args.resume and spec is not None:      # resume: use the saved RESOLVED composition
        _store_comp = _spec_store_comp

    _ss = CONFIG['channels']['store']['sizing']
    _ss.update(max_aisles=args.s_max_aisles, max_bins=args.s_max_bins,
               min_bins=args.s_min_bins, composition=_store_comp)
    _fs = CONFIG['channels']['fulfillment']['sizing']
    _fs.update(max_aisles=args.ff_max_aisles, max_bins=args.ff_max_bins,
               min_bins=args.ff_min_bins)

    log = _setup_logging(os.path.join(base_dir, 'run.log'))
    for _n in _spec_notes:
        log.warning(_n)
    if args.resume and spec:
        log.info('  Resuming from run_spec.json — run-shaping params reconstructed; no retyped flags needed')
    log.info(f'Output directory : {base_dir}')
    log.info(f'Profiles dir     : {args.profiles_dir}')
    log.info(f'Mode             : {"all profiles" if args.all_profiles else "latest profile only"}')

    # On resume, use the pairs PINNED in run_spec.json — never re-discover (a newer profile
    # would silently swap the inventory out from under a resumed run).
    if args.resume and spec and spec.get('pairs'):
        pairs = [tuple(p) for p in spec['pairs']]
        log.info(f'  Using {len(pairs)} inventory pair(s) pinned in run_spec.json (no re-discovery)')
        for _lbl, _inv, _aff in pairs:
            if not (os.path.exists(_inv) and os.path.exists(_aff)):
                log.warning(f'    run_spec pair path missing for {_lbl}: {_inv} | {_aff}')
    elif args.all_profiles:
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

    # Persist the fully-resolved run spec (argv + resolved run-shaping params + pinned pairs)
    # for a NEW run, so a later crash resumes with `--resume DIR` and nothing retyped.
    if not args.resume:
        _write_run_spec(base_dir, {
            'argv'         : sys.argv,
            'n_batches'    : g['n_batches'],  'max_skus'    : g['max_skus'],
            's_max_aisles' : args.s_max_aisles, 's_max_bins' : args.s_max_bins, 's_min_bins': args.s_min_bins,
            'ff_max_aisles': args.ff_max_aisles, 'ff_max_bins': args.ff_max_bins, 'ff_min_bins': args.ff_min_bins,
            's_composition': _store_comp,
            'keyframe_interval': args.keyframe_interval, 'whatif': args.whatif, 'spec': spec_name,
            'profiles_dir' : args.profiles_dir, 'all_profiles': args.all_profiles,
            'workers'      : args.workers, 'max_tasks_per_child': args.max_tasks_per_child,
            'max_retries'  : args.max_retries, 'resume_granularity': args.resume_granularity,
            'pairs'        : [list(p) for p in pairs],
        })
        log.info('  Wrote run_spec.json — zero-param `--resume` enabled')

    # run_layout.json — the unified cell-tree descriptor (cells/reference/configs/pairs), so tools
    # INFER the tree instead of directory-guessing.  Written for a new run; a resumed LEGACY run
    # (no descriptor) gains one so it stays analyzable.  cell_tuples/reference match the driver's.
    cell_tuples = _build_cells(spec_dict)
    reference   = next((c[0] for c in cell_tuples if c[1] is None and not c[2].get('enabled')
                        and c[3] == 'round_robin'), spec_dict.get('reference') or cell_tuples[0][0])
    if (not args.resume) or (read_run_layout(base_dir) is None):
        write_run_layout(
            base_dir, spec=spec_name, reference=reference, cells=cell_tuples, pairs=pairs,
            store_cfgs=STORE_CONFIGS, ff_cfgs=FULFILLMENT_CONFIGS,
            channels=(['store', 'fulfillment'] if FULFILLMENT_CONFIGS else ['store']),
            arms=(None if spec_dict.get('arms') in (None, 'all') else list(spec_dict['arms'])),
            created=datetime.now().isoformat(timespec='seconds'))

    n_store = len(STORE_CONFIGS)
    n_ff    = len(FULFILLMENT_CONFIGS)
    workers = args.workers or 1
    log.info(
        f'Execution plan: {len(pairs)} pair(s) × ({n_store} store + up to {n_ff} fulfillment) '
        f'config(s), swept independently per channel  |  flat pool workers={workers}'
    )

    # EVERY run is a cell matrix (whatif_config.SPECS): a plain run is the single cell k1_off; a
    # sweep spec is >1 cell.  One driver, one tree — each cell is its own scenario subtree.
    log.info(f'Spec: {spec_name}')
    info = _run_whatif_matrix(base_dir, pairs, log, spec_dict, resume=bool(args.resume),
                              max_retries=args.max_retries, resume_granularity=args.resume_granularity)

    # ── one command: run the analysis in-process right after the sim (unless --no-analyze) ──
    if not args.no_analyze:
        from Optimization.analyze_run import analyze_run
        analyze_run(base_dir, log, cells=info['cells'],
                    workers=(args.analysis_workers or workers), reference=info['reference'])

    log.info(f'\nAll simulations complete.  Root: {base_dir}'
             + ('' if not args.no_analyze else
                f'\n  Analyze with: python -m Optimization.analyze_run {base_dir}'))


if __name__ == '__main__':
    main()
