"""simdriver.workunits — plan the per-strategy work units for the flat pool.

Builds one work unit per (pair, config, channel, strategy) arm: shared assets +
batches + resume-aware start planning + the picklable strategy_args dict shipped to
each worker.  Runs only in the parent process."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace as _dc_replace

from Optimization.persistence.Picking_Data import create_run, init_run_db, sim_schema_id
from Optimization.metrics.Workload import WorkloadParams
from Optimization.simdriver.batch_precompute import ensure_batches, load_batches
from Optimization.config.sim_config import (
    CONFIG, seed_batches, seed_world, shift_seconds, put_crew_spec, put_queues_spec,
    crew_cost_spec,
    channel_pickers, staffing_spec, _PICKERS_KEY, CALIBRATION_KEYS, era_on,
    inbound_spec,
    recv_crew_spec,
    work_day_spec,
    _CART_TYPES,
    _build_pick_cfg, _checkpoint_every,
    _config_name,
)
from Optimization.runschema.sim_manifest import (
    _load_resume, _resume_path, _save_resume, _load_run_spec, _write_run_spec)
from Optimization.simconfig import expected_travel as _et
from Optimization.simconfig import staffing as _staffing
from Optimization.simdriver import era_coverage as _era_cov
from Optimization.config.strategies import strategies_for
from Optimization.simdriver.strategy_runner import load_worker_checkpoint, reset_strategy_db
from Warehouse.catalog.Demand import line_law_census
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.kernel.regime import FULFILLMENT
from Warehouse.layout.Storage_Primitive import StoreCart
# local (in-function) imports preserved from the originals: dataclasses.replace,
# Warehouse.kernel.regime.regime_of, Optimization.config.channels.make_channel.


def _plan_strategy_start(ch_run_dir, s, n_batches, db_path, run_params, identity,
                         granularity, prev_id, prev_start, is_resume, log,
                         roll_over: bool = False, receiving: bool = False):
    """Decide (run_id, start_batch) for one strategy, honoring resume granularity.

    - Fresh run / a strategy new on resume → init DB + create_run, start 0.
    - Resumed done arm (checkpoint ≥ n_batches) → reuse run_id, start n_batches (empty loop).
    - Resumed PARTIAL arm (0 < ckpt < n_batches):
        strategy granularity → reset the arm's DB + fresh run_id, start 0 (bit-identical to an
                               uncrashed run — no un-replayed physical state);
        batch granularity    → reuse run_id, start = ckpt (fast, but NOT bit-identical — warn),
                               and REFUSED outright when the carry is on (see below).

    THE CARRY MAKES BATCH-LEVEL RESUME LOSE DEMAND, not just precision.  `_pending` -- the
    units a day cut or a stock clamp rolled into the next batch -- lives in the worker's
    locals and is in no checkpoint.  Resuming at batch N therefore discards everything the
    pre-crash run had carried, and the resumed stream never asks for it again.  That is not
    the "un-replayed physical state" the warning describes: a resumed run would report BETTER
    throughput than it earned, because the work it failed to do stopped being counted, which
    is precisely the error `items_demanded` exists to make impossible.  So with rollover on
    it raises instead of warning.  Strategy granularity -- the default -- is unaffected: it
    replays from batch 0, and a carry that never happened cannot be lost.
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
        if roll_over or receiving:
            # Two different pieces of worker-local state, same consequence and same fix.
            # The receiving case is the worse of the two: unlike the pick carry, merchandise
            # standing on a discarded dock is STILL credited to the inventory position, so
            # the SKU does not re-order either -- the run reports labour it did not do and
            # inventory it does not have.
            why = ('unpicked demand rolls over: the carry lives in the worker and is in no '
                   'checkpoint, so resuming here would DROP every unit the pre-crash run '
                   'carried and report throughput it did not earn'
                   if roll_over else
                   'a receiving crew is configured: the standing contents of the dock live '
                   'in the worker and are in no checkpoint, so resuming here would discard '
                   'every unit on it -- and that merchandise is still credited to the '
                   'inventory position, so the SKU never re-orders to replace it either')
            raise RuntimeError(
                f'[{s.key}] batch-level resume @ {ckpt} is refused because {why}. Re-run '
                f'with --resume-granularity strategy (the default), which replays the arm '
                f'from batch 0.')
        log.warning(f'  [{s.key}] batch-level resume @ {ckpt}: NOT bit-identical to an uncrashed '
                    f'run (un-replayed physical state). Use --resume-granularity strategy for '
                    f'exact cross-arm comparability.')
    return prev_id, ckpt


def _worker_inventory_args(shared: dict) -> tuple:
    """(inv_db, sku_allowlist, max_skus) exactly as the workers load inventory.

    Workers load the PLANNED inventory DB (grown equilibrium_qty + cross-tier stock plans)
    when available so they reproduce the placement the warehouse was sized for; the
    allowlist and the limit then already live in that DB.  ONE helper, because the batch
    precompute's fingerprint is over exactly these inputs: the derivation's precompute and
    `_prepare_channel_run`'s must load the same candidates or they compute two scripts.
    """
    _planned_db = shared.get('planned_inv_db')
    return (_planned_db or shared['inv_db'],
            None if _planned_db else shared.get('sku_allowlist'),
            None if _planned_db else shared.get('max_skus'))


def _channel_batch_plan(ch, inventory, mixed: bool, shared: dict) -> tuple:
    """(batch_cfg, seed_batches, channel_regime) for one channel-run's batch stream.

    Mixed catalogue: the channel's own BatchConfig over its regime's SKU count, its own seed
    offset, its regime as the precompute filter.  Store-only: the pair-level shared
    BatchConfig (= the store channel's shape), the base seed, no filter -- byte-identical
    to the pre-channel pipeline.  ONE helper for the same reason as
    `_worker_inventory_args`: two callers computing the plan is two fingerprints.
    """
    if mixed:
        from Warehouse.kernel.regime import regime_of                       # noqa: E402
        _ch_size = sum(1 for c in inventory.orders if regime_of(c) == ch.regime)
        return ch.batch_config(max(1, _ch_size)), seed_batches() + ch.batch_seed_offset, ch.regime
    return shared['batch_cfg'], seed_batches(), None


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
             f'i={pick_cfg.pick_intercept}  p={pick_cfg.pick_per_item}  '
             f'c={pick_cfg.cart_swap_coef}')
    log.info(f'{"="*64}')

    config_record = {
        'name'            : name,
        'pick_weight_coef': pick_cfg.pick_weight_coef,
        'pick_volume_coef': pick_cfg.pick_volume_coef,
        'pick_weight_fn'  : pick_cfg.pick_weight_fn,
        'pick_volume_fn'  : pick_cfg.pick_volume_fn,
        'pick_intercept'  : pick_cfg.pick_intercept,
        # The per-item charge (ADR-0001).  Recorded so `docs/macros.py` renders the model
        # this leaf actually ran and `run_map_precompute` rebuilds it; an archived config
        # WITHOUT this key predates the charge and is reconstructed at 0.0 by both.
        'pick_per_item'   : pick_cfg.pick_per_item,
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
        # ── SCOPE: the next four are WHOLE-CATALOGUE, not this channel ───────────────
        # They average over `inventory.orders`, every regime included, while the arm this
        # record describes runs on ONE channel's partition -- so a mixed run writes
        # IDENTICAL values into the store leaf and the fulfillment leaf, while the two
        # neighbours above (`batch_mean_frac`, `seed_batches`) really are per-channel.
        # `n_skus` above is whole-catalogue for the same reason.  Two scopes in one record
        # with nothing to tell them apart -- the same shape as the unit-of-account mix
        # fixed in `Performance_Evaluations/tables/per_run.py`.
        # NOT recomputed over the channel partition here: this record is a DECLARED
        # artifact and the docs render these values, so narrowing them is a schema ride
        # rather than an edit.  `catalogue_scope` states what they are so no reader has to
        # infer it.  (The artifact is named once in this file, above -- the run-tree ratchet
        # counts prose, and spelling a contract path twice is the debt it exists to stop.)
        'catalogue_scope'    : 'all_regimes',
        # The levels THIS RUN declared, averaged over the orders it fielded (ADR-0002: the
        # catalogue authors none).  An undeclared order contributes nothing -- a default of 1
        # here would publish a fabricated level into every leaf's config document.
        'avg_equilibrium_qty': round(sum(c.equilibrium_qty for c in inventory.orders
                                         if c.stock_declared())
                                     / max(sum(1 for c in inventory.orders
                                               if c.stock_declared()), 1), 1),
        'avg_reorder_point'  : round(sum(c.reorder_point for c in inventory.orders
                                         if c.stock_declared())
                                     / max(sum(1 for c in inventory.orders
                                               if c.stock_declared()), 1), 2),
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
    _worker_invdb, _worker_allow, _worker_maxsk = _worker_inventory_args(shared)

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
    else:
        # Store-only path: precomputed pair-level shared batch stream (whole catalog = store).
        ch_pick_cfg, ch_wp = pick_cfg, wp
    # The batch stream's plan, from the ONE helper the derivation also uses -- under the era
    # `shared['batch_cfg']` / the channel's fractions are the DERIVED content by the time
    # this runs, and the two precomputes must fingerprint identically.
    ch_batch_cfg, ch_seed_batches, ch_regime = _channel_batch_plan(ch, inventory, mixed, shared)
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
            resume is not None, log,
            roll_over=bool(work_day_spec().get('roll_over_unpicked')),
            receiving=recv_crew_spec() is not None)
    if resume:
        log.info(f'  Resuming [{ch.name}]  '
                 + '  '.join(f'{s.key}@{starts[s.key]}' for s in ch_strategies))
    else:
        log.info(f'  New run [{ch.name}]  '
                 + '  '.join(f'{s.key}={run_ids[s.key]}' for s in ch_strategies))
    _save_resume(ch_run_dir, run_ids, starts)

    # THE DERIVED CREWS.  Under the calibrated era `_build_work_units` has run the derivation
    # for this pair (`_derive_staffing_for_pair`) and left its block on `shared['staffing']`;
    # the put and receiving crews are then the derived site totals, handed to the accessors
    # as `size=` -- derived values are never CONFIG keys.  Flag-off `shared` carries no such
    # block, both accessors read their declared keys, and the payload is byte-identical.
    _st = shared.get('staffing')
    _put_size = _st['derived']['put']['crew'] if _st else None
    _recv_size = _st['derived']['receiving']['crew'] if _st else None
    _staffing_payload = ({'inputs': staffing_spec(), **_st} if _st else staffing_spec())

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
        # The pick crew's MODE, carried rather than re-derived: the worker re-imports
        # sim_config and would get pristine defaults, and 'store_machine' is a name the
        # worker has no way to interpret.  A plain str so the payload stays trivially
        # picklable; Mode.of() parses it back.
        pick_mode           = str(ch.picker.mode),
        shift_seconds       = shift_seconds(),
        # The WORKING DAY, carried for the same reason as the crews below: a spawned worker
        # re-imports sim_config and would get pristine defaults, so a day configured on the
        # command line would be accepted and silently ignored.
        work_day            = work_day_spec(),
        # The put crew, carried the same way and for the same reason.  Its speed comes
        # from its MODE, not from the pick config: a crew labelled `foot` costed at the
        # store's machine speed would write rows whose mode and duration disagree.
        put_crew            = put_crew_spec(size=_put_size),
        recv_crew           = recv_crew_spec(size=_recv_size),
        inbound             = inbound_spec(),
        put_queues          = put_queues_spec(),
        # The other crews' PRICE as scalars of the pickers' -- the fifth seam of a knob.
        # Not in the payload = silently the kernel default in every spawned worker.
        crew_cost           = crew_cost_spec(),
        # The staffing record -- the fifth seam of the staffing knobs.  Flag-off it is the
        # INPUTS dict; under the era it is `{inputs, derived, calibration}` for this pair.
        # `k_pickers` above and the two crews are the values the worker sizes from; this is
        # the record it checks them against (`strategy_runner._check_declared_crew`), so a
        # worker handed a wrong crew refuses rather than running under one its run spec
        # never declared or derived.
        staffing            = _staffing_payload,
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


def _derive_staffing_for_pair(shared: dict, channel_runs: list, mixed: bool, pair_dir: str,
                              log: logging.Logger, workers: int = 1) -> tuple:
    """Run the calibrated era's staffing derivation for ONE inventory pair.

    Returns `(channel_runs, derived, calibration)`: the channel-runs with their batch
    fractions REPLACED by the derived batch content (a store-only pair also gets
    `shared['batch_cfg']` replaced, since that is the plan its workers read), the derived
    block (`staffing.derive`), and the `calibration` block -- which, since "Derive the
    expected-travel closed form", records HOW the constants were computed (the method,
    the placement distribution, the geometry fingerprint, any declared override) rather
    than which record they were copied from.  There is no calibration record.

    Two stages, because the script is derived from the pickers and the crews from the
    script (`Optimization/simconfig/staffing.py`):

      A. per channel, from the CATALOGUE and the BUILT GEOMETRY: the class-uniform
         placement distribution over the section's packs (`expected_travel.PlacementDist
         .uniform` -- arm-independent, and the FIFO restock's steady state), the
         expected day at every line count, and the FIXED POINT `n` at which that day
         fills the declared crew's capacity (`expected_travel.solve_n`); `s_pick` is the
         expected seconds per unit AT that point, the daily demand its units, and the
         batch content follows.  A declared `--s-pick-*` override replaces the fixed point
         with the demand that number buys, and the expectation is still recorded beside it.
      B. per channel, from the SCRIPT: precompute the batches under the derived content
         (the same call `_prepare_channel_run` makes, so the cache is warm for it), read
         them back, total the demand, the implied reorders' packs priced with the
         expected put travel (`expected_travel.put_site_pricer`) and their exact unload
         seconds, then size the two site crews.

    The pricing config of a channel is its FIRST config (`store` / `ful_calibrated`).
    Warnings, never failures: a saturated batch (the crew asks for more lines than the
    catalogue has SKUs), an unpriced section (empty), SKUs the packer placed nowhere.
    """
    inputs = staffing_spec()
    cc = crew_cost_spec()
    S = float(work_day_spec()['seconds'])
    inventory = shared['inventory']
    invdb, allow, maxsk = _worker_inventory_args(shared)
    n_batches = int(CONFIG['global']['n_batches'])
    geometry = _et.Geometry.from_warehouse(shared['warehouse_meta'])
    geometry_fp = shared.get('warehouse_fingerprint')
    _put = put_crew_spec()
    put_speed = SpeedProfile(_put['x_speed'], _put['y_speed'])
    overrides = {k: inputs.get(k) for k in CALIBRATION_KEYS}

    # Group the channel-runs by channel; the first config prices the channel.
    groups: dict = {}
    for ch, cfg in channel_runs:
        groups.setdefault(ch.name, {'ch': ch, 'cfg': cfg})
    # ── stage A: the catalogue and the geometry ────────────────────────────────────
    # ONE function (`era_coverage.stage_a`) for the coverage loop and the derivation.  The
    # loop ran it last on exactly these orders over exactly this geometry ("Rescale stock
    # coverage at setup"), so when `build_shared_assets` left that result on the shared dict
    # it is reused rather than priced again; anything else -- a caller that built its assets
    # without the loop -- prices the section here.
    specs = [_era_cov.ChannelSpec(name, grp['ch'].regime if mixed else None,
                                  grp['ch'].picker.cost, _config_name(grp['cfg']))
             for name, grp in groups.items()]
    cached = shared.get('era_stage_a')
    if cached and cached.get('n_orders') == len(inventory.orders) \
            and cached.get('aisles') == len(shared['warehouse_meta'].aisles) \
            and set(cached['channels']) == {s.name for s in specs}:
        stage_a = cached['channels']
        log.info('  [staffing] stage A reused from the coverage loop (same orders, same geometry)')
    else:
        stage_a = _era_cov.stage_a(inventory.orders, geometry, specs, inputs=inputs,
                                   day_seconds=S, log=log)
    constants: dict = {'s_pick': {}}
    new_runs: list = []
    for name, grp in groups.items():
        ch = grp['ch']
        a = stage_a[name]
        constants['s_pick'][name] = a['s_pick']
        batch = a['batch']
        new_ch = _dc_replace(ch, batch_mean_fraction=batch['mean_fraction'],
                             batch_std_fraction=batch['std_fraction'])
        groups[name]['new_ch'] = new_ch
    for ch, cfg in channel_runs:
        new_runs.append((groups[ch.name]['new_ch'], cfg))
    if not mixed:
        # The store-only path reads the PAIR-level BatchConfig; make it the derived one.
        st = stage_a['store']['batch']
        shared['batch_cfg'] = _dc_replace(shared['batch_cfg'], mean_fraction=st['mean_fraction'],
                                          std_fraction=st['std_fraction'])
    # ── stage B: the script ────────────────────────────────────────────────────────
    scripts: dict = {}
    for name, a in stage_a.items():
        new_ch = groups[name]['new_ch']
        batch_cfg, seed, regime = _channel_batch_plan(new_ch, inventory, mixed, shared)
        path, fp = ensure_batches(pair_dir, invdb, maxsk, allow, shared['aff_db'], batch_cfg,
                                  seed, n_batches, workers=workers, log=log,
                                  channel_regime=regime)
        batches = load_batches(path, fp) if path else None
        if batches is None:
            raise RuntimeError(
                f'[staffing] {name}: the era derivation needs the precomputed batch script and '
                f'none could be produced (path={path!r}); the receiving crew is sized from the '
                f'packs the script implies, so there is nothing to size it from')
        by_sku = {c.sku: c for c in a['orders']}
        totals = _staffing.script_totals(batches, by_sku, a['pricing'])
        put_site = _et.put_site_pricer(geometry, a['dist'], None, put_speed,
                                       brackets=a['pick_cfg'].height_brackets)
        _staffing.implied_reorders(
            totals, by_sku, a['pricing'],
            f_put=float(inputs['f_put']), f_recv=float(inputs['f_recv']),
            put_intercept_scale=cc['put_intercept_scale'], put_item_ratio=cc['put_item_ratio'],
            recv_intercept_scale=cc['recv_intercept_scale'], put_site=put_site)
        if totals.unknown_skus:
            log.warning(f'  [staffing] {name}: {totals.unknown_skus} script line(s) named a SKU '
                        f'outside the channel section -- skipped in the totals')
        scripts[name] = totals
    site_put_units = sum(t.put_units for t in scripts.values())
    site_put_s = sum(t.put_s for t in scripts.values())
    if overrides.get('s_put') is not None:
        constants['s_put'] = _staffing.constant(
            float(overrides['s_put']), 'declared', source='override',
            expected=(site_put_s / site_put_units) if site_put_units else 0.0)
    else:
        constants['s_put'] = _staffing.constant(
            (site_put_s / site_put_units) if site_put_units else 0.0, 'derived',
            source='expected_travel', placement='uniform',
            note='expected put travel from the aisle mouth over the class-uniform destination, '
                 'plus the packs\' handling at the class-mean height')
    derived = _staffing.derive(
        inputs=inputs, constants=constants, day_seconds=S,
        channels={n: {'pickers': a['pickers'], 'daily_demand_units': a['daily_demand_units'],
                      'analytic': a['analytic'], 'batch': a['batch'], 'n_skus': len(a['orders']),
                      'expected': a['expected']}
                  for n, a in stage_a.items()},
        scripts=scripts,
        pricing_names={n: a['pricing'].name for n, a in stage_a.items()})
    calibration = {
        'method': 'expected_travel', 'placement': 'uniform',
        'geometry_fingerprint': geometry_fp,
        's_pick': constants['s_pick'], 's_put': constants['s_put'],
        'overrides': sorted(k for k, v in overrides.items() if v is not None),
        # The coverage loop's record ("Rescale stock coverage at setup"): the declared
        # days, the catalogue's own implied coverage, every round's line count and the
        # floor shares of the levels the run fields.  None when the assets were built
        # without the loop (a frozen inventory, an analysis-shape rebuild).
        'coverage': shared.get('coverage'),
        # The line law every closed form above read ("Stamp the line distribution on the
        # SKU"): which families the catalogue carries and how many SKUs were RECONSTRUCTED
        # (`assumed`: a pre-stamp file, Poisson(demand_qty_rate) rebuilt at load).
        'line_law': line_law_census(inventory.orders),
    }
    log.info(f"  [staffing] put crew={derived['put']['crew']} "
             f"(s_put={constants['s_put']['value']:.3f} s/unit, "
             f"{constants['s_put']['provenance']}; load={derived['put']['load_seconds_per_day']:,.0f} s/day)"
             f"  receiving crew={derived['receiving']['crew']} "
             f"(exact {derived['receiving']['load_seconds_per_day']:,.0f} s/day over "
             f"{derived['receiving']['load_packs_per_day']:,.1f} packs/day)")
    return new_runs, derived, calibration


def _run_root_spec(base_dir: str) -> tuple:
    """(run_root, run_spec) for a cell dir or a run root; (None, None) when no run spec
    exists (a harness that never wrote one).  The run spec lives at the RUN ROOT while
    `_build_work_units` is handed a CELL dir -- the same parent-vs-self distinction
    `run_analysis._apply_run_shape` documents."""
    for root in (base_dir, os.path.dirname(os.path.abspath(base_dir))):
        spec = _load_run_spec(root)
        if spec:
            return root, spec
    return None, None


def _record_derived(base_dir: str, label: str, derived: dict, calibration: dict,
                    log: logging.Logger) -> None:
    """Write this pair's `derived` and `calibration` blocks into the run spec's `staffing`
    record -- or, when the run spec ALREADY holds a derived block for the pair, check that
    the fresh derivation agrees with it and RAISE if it does not.

    The recorded block is authoritative on resume ("Design the staffing record", decision 3):
    an arm fields the crew it started with, so a re-derivation that disagrees -- a changed
    derivation, a changed calibration record, a changed catalogue -- is refused rather than
    quietly resuming under different crews.  A multi-cell run hits the agreeing branch on
    every cell after the first, which is the free drift check across cells.
    """
    root, spec = _run_root_spec(base_dir)
    if spec is None:
        log.warning('  [staffing] no run spec at the run root -- the derived block is '
                    'carried in the payload but not recorded')
        return
    st = spec.setdefault('staffing', {})
    prev = (st.get('derived') or {}).get(label)
    if prev is not None:
        diffs = _staffing.derived_differs(prev, derived)
        if diffs:
            raise RuntimeError(
                f'[staffing] the derivation for pair {label!r} disagrees with the one this run '
                f'recorded at {", ".join(diffs[:8])}{" ..." if len(diffs) > 8 else ""}; the '
                f'recorded derived block is authoritative on resume, so this run cannot '
                f'continue under different crews. Start a new run instead.')
        return
    st.setdefault('derived', {})[label] = derived
    st.setdefault('calibration', {})[label] = calibration
    _write_run_spec(root, spec)
    log.info(f'  [staffing] recorded derived + calibration blocks for {label} in the run spec')


def _record_coverage(base_dir: str, label: str, coverage: dict | None,
                     log: logging.Logger) -> None:
    """Record this pair's COVERAGE block in the run spec, in every mode.

    A run declares its own stock levels whether or not the era flag is on (ADR-0002), and the
    only thing that varies between one run's declaration and another's is the fixed point's
    line count -- so this block is what lets anything reproduce the levels the run FIELDED.
    `run_analysis` and `run_map_precompute` re-plan a finished run's warehouse from its
    original catalogue, which holds no level, and re-declare from exactly this record
    (`era_coverage.declare_from_record`).

    Under the era the record rides `_record_derived`'s calibration block and this is a no-op
    on it; flag-off there is no derivation to record, and without this a flag-off run would
    declare levels nobody could ever reproduce -- its own analysis stage would then refuse to
    rebuild the warehouse and silently emit no graphs, which is how this was found.
    """
    if coverage is None:
        return
    root, spec = _run_root_spec(base_dir)
    if spec is None:
        log.warning('  [coverage] no run spec at the run root -- this run cannot be '
                    're-analysed from its catalogue, because the levels it declared are '
                    'recorded nowhere')
        return
    st = spec.setdefault('staffing', {}).setdefault('calibration', {}).setdefault(label, {})
    if st.get('coverage') is not None:
        return                      # already recorded (the era path, or an earlier cell)
    st['coverage'] = coverage
    _write_run_spec(root, spec)
    log.info(f'  [coverage] recorded the declaration for {label} in the run spec '
             f'({", ".join(f"{k} {v:,.0f} lines/day" for k, v in coverage["lines_per_day"].items())})')


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
    # comes from CONFIG['channels'][name], except the crew, which is the channel's DECLARED
    # picker count (CONFIG['global'], via channel_pickers).  A pick-config entry that names
    # its own 'num_pickers' must AGREE with it: the batch script is derived from the declared
    # crew, so an arm fielding a different crew is the stale-literal trap restated per arm
    # (.scratch/department-calibration, "Design the staffing record", decision 1).  Restating
    # the channel value stays legal; disagreeing raises here, at setup, before any DB exists.
    for name in ('store', 'fulfillment'):
        if name == 'fulfillment' and not mixed:
            continue
        chan = CONFIG['channels'][name]
        default_cart = _CART_TYPES.get(chan['cart'], StoreCart)
        n = channel_pickers(name)
        for cfg in chan['configs']:
            _own = cfg.get('num_pickers')
            if _own is not None and int(_own) != n:
                raise ValueError(
                    f"pick config {cfg.get('name')!r} declares num_pickers={_own}, but the "
                    f"{name} channel's declared crew is {n} ({_PICKERS_KEY[name]}). The script "
                    f"is derived from the declared crew, so an arm may not field a different "
                    f"one: drop the key from the config module, or change the channel's knob.")
            pc = _build_pick_cfg(cfg, num_pickers=n, default_cart=default_cart)
            ch = make_channel(name, chan['regime'], pc, n,
                              # The crew's MODE.  Without it both channels silently took
                              # make_channel's `foot` default while the store pool ran at
                              # the machine speed.
                              mode=chan['pick_mode'],
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
        # THE CALIBRATED ERA: derive the batch content and the two site crews for this pair
        # BEFORE any channel-run is prepared, because the crews are site totals over both
        # channels' scripts.  Flag-off this whole block is skipped and nothing below changes.
        if era_on():
            channel_runs, _derived, _cal = _derive_staffing_for_pair(
                shared, channel_runs, mixed, pair_dir, log, workers=max_workers)
            shared['staffing'] = {'derived': _derived, 'calibration': _cal}
            _record_derived(base_dir, label, _derived, _cal, log)
        # The declaration itself is recorded in EVERY mode, because it is MADE in every mode:
        # a rebuild re-declares from this block, and a run whose levels are recorded nowhere
        # can never have its warehouse reproduced from its catalogue again.  Under the era the
        # line above already wrote it, and this is a no-op.
        _record_coverage(base_dir, label, shared.get('coverage'), log)
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
