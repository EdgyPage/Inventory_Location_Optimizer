"""simdriver.workunits — plan the per-strategy work units for the flat pool.

Builds one work unit per (pair, config, channel, strategy) arm: shared assets +
batches + resume-aware start planning + the picklable strategy_args dict shipped to
each worker.  Runs only in the parent process."""
from __future__ import annotations

import json
import logging
import os

from Optimization.persistence.Picking_Data import create_run, init_run_db, sim_schema_id
from Optimization.metrics.Workload import WorkloadParams
from Optimization.simdriver.batch_precompute import ensure_batches
from Optimization.config.sim_config import (
    CONFIG, seed_batches, seed_world, _CART_TYPES, _build_pick_cfg, _checkpoint_every,
    _config_name,
)
from Optimization.runschema.sim_manifest import _load_resume, _resume_path, _save_resume
from Optimization.config.strategies import strategies_for
from Optimization.simdriver.strategy_runner import load_worker_checkpoint, reset_strategy_db
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.kernel.regime import FULFILLMENT
from Warehouse.layout.Storage_Primitive import StoreCart
# local (in-function) imports preserved from the originals: dataclasses.replace,
# Warehouse.kernel.regime.regime_of, Optimization.config.channels.make_channel.


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
    from Warehouse.kernel.regime import regime_of                           # noqa: E402

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
        'sampler'         : channel.sampler,               # batch-sampler era (v1 | v2)
        'n_batches'       : n_batches,
        'seed_world'      : seed_world(),
        'seed_batches'    : seed_batches() + channel.batch_seed_offset,  # this channel's actual seed
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

    # Fallback reads the declared default rather than a literal, so sim_config stays the one
    # place the interval is chosen (see the note there on why it is no longer 5).
    keyframe_interval = int(
        shared.get('keyframe_interval', CONFIG['global']['keyframe_interval']) or 0)
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
        seed_world        = seed_world(),
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
        ch_seed_batches = seed_batches() + ch.batch_seed_offset
        ch_regime       = ch.regime
    else:
        # Store-only path: precomputed pair-level shared batch stream (whole catalog = store).
        ch_pick_cfg, ch_wp = pick_cfg, wp
        ch_batch_cfg    = batch_cfg
        ch_seed_batches = seed_batches()
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
        seed_world          = seed_world(),
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
        # The shape every sim_<arm>.db in this directory was written with (Schema/identity.py).
        # It is stamped into simulation_runs.sim_schema_id too, but the docs site opens NO
        # database — docs/macros.py and docs/experiments/ingest.py read committed JSON only —
        # so this is the ONLY way schema identity reaches the website.
        sim_schema_id = sim_schema_id(),
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
    from Optimization.config.channels import make_channel                         # noqa: E402
    from Warehouse.kernel.regime import regime_of                              # noqa: E402

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
                              batch_std_fraction=chan['batch']['std'],
                              sampler=CONFIG['global']['sampler'])
            runs.append((ch, cfg))
    return mixed, runs


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
