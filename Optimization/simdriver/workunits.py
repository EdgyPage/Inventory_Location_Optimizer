"""simdriver.workunits — plan the per-strategy work units for the flat pool.

Builds one work unit per (pair, config, channel, strategy) arm: shared assets +
batches + resume-aware start planning + the picklable strategy_args dict shipped to
each worker.  Runs only in the parent process."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace as _dc_replace

from Optimization.persistence.Picking_Data import (
    create_run, find_run, init_run_db, sim_schema_id)
from Optimization.metrics.Workload import WorkloadParams
from Optimization.simdriver.batch_precompute import ensure_batches, load_batches
from Optimization.config.sim_config import (
    CONFIG, seed_batches, seed_world, shift_seconds, put_crew_spec, put_queues_spec,
    crew_cost_spec,
    channel_pickers, staffing_spec, _PICKERS_KEY, CALIBRATION_KEYS, era_on,
    couple_channels,
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


def _arm_db_path(run_dir: str, arm: str) -> str:
    """`<channel run dir>/sim_<arm>.db` — ONE spelling of an arm's result DB.

    `_prepare_channel_run` builds the map the workers are handed and `_reconcile_coupled_unit`
    has to name the same files to discard them, so the two read it from here.  A second
    spelling would not fail: it would remove nothing and leave the arm's rows to be appended
    to on the replay, which is the silent-doubling shape `_plan_strategy_start`'s duplicate-run
    refusal exists to make impossible.
    """
    return os.path.join(run_dir, f'sim_{arm}.db')


def _arm_position(run_dir: str, arm: str, prev_start: int = 0) -> int:
    """The batch this arm would resume AT, from its on-disk state.

    The advanced per-arm checkpoint when there is one, else the counter the last prepare
    recorded in the leaf's resume file.  ONE definition, because `_plan_strategy_start` decides an
    arm's branch from it and `_reconcile_coupled_unit` decides whether a coupled pair's two
    leaves AGREE from it — and two leaves compared by a different rule from the one that
    plans them is a comparison that can be true while the plan disagrees.
    """
    return load_worker_checkpoint(run_dir, arm) or prev_start


def _plan_strategy_start(ch_run_dir, s, n_batches, db_path, run_params, identity,
                         granularity, prev_id, prev_start, is_resume, log,
                         roll_over: bool = False, receiving: bool = False,
                         coupled: bool = False):
    """Decide (run_id, start_batch) for one strategy, honoring resume granularity.

    - Fresh run / a strategy new on resume → init DB + create_run, start 0; RAISES if
      the arm's DB already holds a run (see the branch — a second run in one file is a
      silent corruption, never a recoverable state).
    - Resumed done arm (checkpoint ≥ n_batches) → reuse run_id, start n_batches (empty loop).
    - Resumed PARTIAL arm (0 < ckpt < n_batches):
        strategy granularity → reset the arm's DB + fresh run_id, start 0 (bit-identical to an
                               uncrashed run — no un-replayed physical state);
        batch granularity    → reuse run_id, start = ckpt (fast, but NOT bit-identical — warn),
                               and REFUSED outright when the carry is on, when a dock exists,
                               or when the arm is a LEAF OF A COUPLED UNIT (see below).

    THE COUPLED REFUSAL IS THE STRONGEST OF THE THREE, and independent of the other two
    (site-dock 10 section 4).  Two leaves resume from two independently written
    `_ckpt_<arm>.pkl` files in two directories, so a batch-level resume could lawfully start
    them at DIFFERENT batches -- and one batch is one site day (`simconfig/staffing.py`, which
    refuses channels with differing batch counts outright).  A site day half-run in one channel
    and not the other is not a degraded run; it is a run whose shared dock, put pool and recv
    clock never existed.  It is stated separately rather than left to the other two because
    NEITHER of them is a statement about coupling: `receiving` evaporates if a derived crew
    ever rounds below 1, and `roll_over` is a work-day knob any cell may clear, so today's
    coverage is a coincidence nobody would notice losing.

    There is no assertion anywhere downstream that the two leaves' batch-level starts agree,
    and there must not be: refusing here is what makes the case unreachable, and asserting
    about it would imply it is not.

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
        # A SECOND RUN IN ONE DB CORRUPTS IT SILENTLY, so the fresh branch refuses to open one.
        # `find_run` (Picking_Data) resolves `ORDER BY run_id LIMIT 1` — the OLDEST run — so a db
        # that acquired a second one answers every run_id-filtered query from the ABANDONED run
        # and doubles every unfiltered aggregate over the file.  There is no symptom: the arm
        # completes, the rows are all there, and every number over them is wrong.
        #
        # Nothing reaches this branch over a populated db today — a new run always gets a fresh
        # timestamped dir, and a `--resume` either skips a finalized channel-run or finds its
        # resume.pkl — but that is an ARGUMENT, not a check, and the argument is exactly what the
        # torn-finalize window used to break (`supervisor._finalize_config_run`).  The caller
        # that means to restart an arm resets its DB first (`reset_strategy_db`, the partial-arm
        # branch below); anything else arriving here is a bug, and this is the only place it can
        # be named.
        if os.path.exists(db_path):
            existing = find_run(db_path, s.key)
            if existing is not None:
                raise RuntimeError(
                    f'[{s.key}] refusing to create a second run in {db_path}: it already holds '
                    f'run_id={existing} (run dir {ch_run_dir}). The caller was expected to have '
                    f"reset this arm's DB (reset_strategy_db) before planning a fresh start.")
        init_run_db(db_path)
        return create_run(db_path, s.run_type, run_params,
                          identity={**identity, 'strategy_key': s.key}), 0
    ckpt = _arm_position(ch_run_dir, s.key, prev_start)
    if 0 < ckpt < n_batches:
        if granularity == 'strategy':
            reset_strategy_db(ch_run_dir, db_path, s.key)
            init_run_db(db_path)
            log.info(f'  [{s.key}] strategy-level reset -> batch 0 (bit-identical)')
            return create_run(db_path, s.run_type, run_params,
                              identity={**identity, 'strategy_key': s.key}), 0
        if coupled or roll_over or receiving:
            # Three different pieces of state, same consequence and same fix.  The COUPLED
            # case is checked first because it is the only one of the three that is a
            # statement about this unit's SHAPE rather than about a knob: the other two can
            # both be off on a coupled run, and then the refusal would evaporate silently.
            # The receiving case is the worse of the two knobs: unlike the pick carry,
            # merchandise standing on a discarded dock is STILL credited to the inventory
            # position, so the SKU does not re-order either -- the run reports labour it did
            # not do and inventory it does not have.
            why = ('this arm is one LEAF of a coupled unit: its sibling leaf keeps its own '
                   'checkpoint in its own directory, so resuming at a batch would start the '
                   'two channels on different site DAYS -- a day whose shared dock, put pool '
                   'and receiving clock never existed in either leaf'
                   if coupled else
                   'unpicked demand rolls over: the carry lives in the worker and is in no '
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
    coupled : bool = False,  # this channel-run is one LEAF of a coupled unit (site-dock 10)
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

    ``coupled`` says this run is one leaf of a COUPLED unit (`_prepare_site_run`).  It changes
    nothing here except the resume grain the planner will accept: a leaf may not resume at a
    batch, because its sibling's checkpoint lives in another directory and the two would start
    on different site days.  Default False, so every uncoupled caller is byte-identical.
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
    ch_db_path = {s.key: _arm_db_path(ch_run_dir, s.key) for s in ch_strategies}

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
    # THE DERIVED CREWS.  Under the calibrated era `_build_work_units` has run the derivation
    # for this pair (`_derive_staffing_for_pair`) and left its block on `shared['staffing']`;
    # the put and receiving crews are then the derived site totals, handed to the accessors
    # as `size=` -- derived values are never CONFIG keys.  Flag-off `shared` carries no such
    # block, both accessors read their declared keys, and the payload is byte-identical.
    # Resolved BEFORE the resume planner below: whether a dock exists is a question the
    # planner asks (a batch-level resume must refuse when one does), and under the era the
    # answer lives in the derived block, never in the declared key.
    _st = shared.get('staffing')
    _put_size = _st['derived']['put']['crew'] if _st else None
    _recv_size = _st['derived']['receiving']['crew'] if _st else None
    _staffing_payload = ({'inputs': staffing_spec(), **_st} if _st else staffing_spec())

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
            receiving=recv_crew_spec(size=_recv_size) is not None,
            coupled=coupled)
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
        # The yard's "someone must unload it" guard reads the SAME derived crew the dock
        # is built with; None (flag-off) makes it read the declared key, byte-identically.
        inbound             = inbound_spec(recv_crew_size=_recv_size),
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


# ── the coupled (site) work unit ───────────────────────────────────────────────
# The site crews a leaf must NOT carry.  Under coupling they are properties of the SITE, and
# `workunits.py`'s per-leaf payload handed EACH leaf the whole derived total -- two independent
# processes fielding the site's labour twice (`staffing.py:710-712` says outright that those two
# crews are site totals).  Site-dock 02 section 6 fixes it by DELETION: the keys leave the leaf
# payload and sit once at unit scope, where `_check_declared_crew` verifies them once per unit.
# `k_pickers` is NOT here -- pickers really are per-channel crews (`staffing.py:354-385`).
_SITE_CREW_KEYS = ('put_crew', 'recv_crew')


def _prepare_site_run(channel_runs, mixed: bool, shared: dict, pair_dir: str,
                      log: logging.Logger, workers: int = 1,
                      resume_granularity: str = 'strategy') -> tuple[list, list]:
    """Pre-initialise ONE pair's COUPLED runs: two channel leaves per work unit.

    `_prepare_channel_run` survives unforked and is called once per channel (site-dock 02
    section 3) -- each leaf genuinely needs its own run dir, batch stream, pick config, wp,
    yardsticks and DB, and every one of those stays per channel under coupling.  What this
    adds is only the pairing: the two channels' ordered arm lists zipped into arm PAIRS, the
    site crews lifted out of both leaf payloads to unit scope, and the two group keys carried
    on the unit so no parent-side reader has to slice a uid for them.

    Returns (unit_args_list, sim_skeletons) in the same shape `_prepare_channel_run` returns,
    so `_build_work_units` composes them identically.
    """
    # ONE config per channel. Which store config pairs with which fulfillment config is a
    # question nobody has answered -- 02 settled the arm pairing and said nothing about a
    # config cross product -- so more than one per channel is refused here rather than paired
    # by position, which would be a decision made by a `zip`.
    _by_channel: dict = {}
    for ch, cfg in channel_runs:
        _by_channel.setdefault(ch.name, []).append((ch, cfg))
    if sorted(_by_channel) != ['fulfillment', 'store']:
        raise ValueError(
            f'a coupled run needs exactly the store and fulfillment channels; got '
            f'{sorted(_by_channel)}. Coupling is a SITE model: one dock, one receiving crew '
            f'and one pool of putters over two channels, so a single-channel catalogue has '
            f'nothing to couple.')
    for _name, _runs in _by_channel.items():
        if len(_runs) != 1:
            raise ValueError(
                f'a coupled run needs exactly one config per channel; the {_name} channel '
                f'sweeps {len(_runs)} ({[_config_name(c) for _, c in _runs]}). Pairing two '
                f'config SETS is undecided (site-dock 02 settled the arm pairing only), and '
                f'zipping them here would make that decision silently.')

    # Store first, then fulfillment -- the declared channel order, so a unit's uid slots mean
    # the same thing on every run (`_channel_runs_for` builds them in this order too).
    leaves, skeletons = [], []
    for _name in ('store', 'fulfillment'):
        (ch, cfg), = _by_channel[_name]
        _sa, _sk = _prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log,
                                        workers=workers,
                                        resume_granularity=resume_granularity,
                                        # The leaf's resume grain (site-dock 10 section 4):
                                        # a batch-level start is refused for BOTH leaves, so
                                        # the pair can only ever replay a whole arm together.
                                        coupled=True)
        leaves.append((_config_name(cfg), ch, _sa))
        skeletons.extend(_sk)

    (_cfg_s, _ch_s, _sa_s), (_cfg_f, _ch_f, _sa_f) = leaves
    # THE DIAGONAL BY RANK. Both channels sweep an ORDERED arm list (`strategies_for`), and a
    # pair is rank against rank. A ragged hand-off is refused rather than truncated: `zip`
    # would silently drop the tail of the longer channel, which is the failure site-dock 06
    # made unrepresentable on the selection side and must not be reintroduced here.
    if len(_sa_s) != len(_sa_f):
        raise ValueError(
            f'a coupled unit pairs the two channels by RANK, so their arm lists must be the '
            f'same length; store sweeps {len(_sa_s)} arm(s) and fulfillment {len(_sa_f)}. '
            f'Curate `strategies.CHANNEL_RESTOCKS` so the two agree, or run uncoupled.')

    # The site crews, lifted out of BOTH leaf payloads exactly once. Read off the store leaf
    # because `_prepare_channel_run` derives them from `shared['staffing']`, which is the
    # pair's block and therefore identical on both -- asserted, because two different values
    # here would mean the derivation is not per pair after all and one leaf would run under a
    # crew the other never saw.
    site_crews = {k: _sa_s[0].get(k) for k in _SITE_CREW_KEYS}
    for k in _SITE_CREW_KEYS:
        if _sa_f[0].get(k) != site_crews[k]:
            raise ValueError(
                f'the two channels derived different {k} records ({_sa_s[0].get(k)!r} vs '
                f'{_sa_f[0].get(k)!r}); a site crew is ONE crew, derived per pair, so this '
                f'means the derivation is no longer per pair')

    unit_args = []
    for _ls, _lf in zip(_sa_s, _sa_f):
        for _l in (_ls, _lf):
            for k in _SITE_CREW_KEYS:
                _l.pop(k, None)          # the deletion that ends the double count
        unit_args.append({
            # Unit scope: the site crews, the record they are checked against, and the batch
            # count. Everything else is a leaf's own and lives under `leaves`.
            **site_crews,
            'staffing' : _ls.get('staffing'),
            'n_batches': _ls['n_batches'],
            # THE UNIT'S THIRD OUTPUT (site-dock 24, ADR-0005): where a coupled standing
            # run's trailer- and door-denominated rows go, since they belong to neither
            # leaf. Named HERE rather than rebuilt in the worker, from the same helper the
            # torn-pair reconciler removes it with -- one spelling, so a repair and a write
            # cannot disagree about which file they mean.
            'site_db'  : _site_db_path(pair_dir, _ls['strategy'], _lf['strategy']),
            'leaves'   : [_ls, _lf],
            # log_queue is NOT set here -- injected by the flat pool, as for a leaf unit.
        })
    return unit_args, skeletons


# ── the coupled pair's completeness, and the torn-pair repair ──────────────────
# Site-dock 10.  A coupled unit finalizes TWO leaves, so a kill can leave a half-written pair
# that every existing guard reads one leaf at a time and therefore calls half-complete.  The
# rule is both leaves or neither, and a pair that disagrees with itself REPAIRS rather than
# refuses: strategy-granularity resume already replays a partial arm bit-identically from
# batch 0 (`_plan_strategy_start`), and un-finalizing a leaf that happened to reach the end is
# that same operation applied one step later.  Refusal-until-clean is the right answer only
# where no exact replay exists; here one does.

def _meta_path(run_dir: str) -> str:
    """The completeness marker of ONE channel-run dir.  The declaration's name is spelled
    HERE and nowhere else in this module -- `runschema`'s ratchet counts every hand-written
    copy of a contract path, comments included, and three readers of one marker is three
    places to edit when the tree moves."""
    return os.path.join(run_dir, 'sim_meta.json')


def _leaf_is_complete(run_dir: str) -> bool:
    """Today's completeness test for ONE channel-run dir — no new marker.

    The marker present AND the resume file absent, which is the pair of facts
    `supervisor._finalize_config_run` writes in that order (site-dock 16 closed the window
    where a kill could leave neither).  A coupled unit needs nothing recorded beyond this:
    finalize runs only when every member uid succeeded, so TWO finalized leaves cannot exist
    without a successful unit, and the pair of markers already carries the fact a third one
    would record.  A third marker could also tear in its own right -- a kill after both leaves
    finalize but before it is written re-runs a FINISHED pair -- and adding a third thing to
    keep in sync is not how two things being out of sync gets fixed.

    ONE function, and the uncoupled skip guard reads it too: the coupled rule is this same
    test applied to two directories instead of one, and two spellings of "complete" would be
    two things to keep in step over exactly the question this file is reconciling.
    """
    return os.path.exists(_meta_path(run_dir)) and not os.path.exists(_resume_path(run_dir))


def _site_db_path(pair_dir: str, arm_store: str, arm_ful: str) -> str:
    """`<pair>/_site/inbound_<arm_store>__<arm_ful>.db` — the coupled unit's THIRD output.

    Site-dock 03 section 1: the pair is the only directory that dominates both leaves (their
    configs are siblings, so they share no ancestor below it), and the arm pair rides in the
    filename stem rather than a directory, on the `strategy` precedent.  The `_` prefix is the
    run tree's RESERVED one, so every walker skips the subtree.

    Built from `pair_dir` here rather than resolved through `runschema.resolver_for`: the
    artifact is not DECLARED yet -- that is the contract bump site-dock 24 carries with the
    writer -- so there is no accessor to ask, and the rule this repo actually enforces is
    about CONSUMERS walking a finished tree positionally.  This is the parent building a path
    under a directory it owns, which is what `reset_strategy_db` does with `_ckpt_<arm>.pkl`.
    Stated in ONE place so the writer reads it from here instead of spelling it a second time.
    The stem is TWO-armed by construction, because a site is two channels (`_prepare_site_run`
    refuses anything else); a third leaf raises here rather than quietly naming a file after
    two of the three.
    """
    return os.path.join(pair_dir, '_site', f'inbound_{arm_store}__{arm_ful}.db')


def _forget_arms(run_dir: str, arms) -> None:
    """Drop `arms` from one leaf's resume record so the planner starts them FRESH.

    `reset_strategy_db` removes the arm's db and its checkpoint but cannot touch the resume
    record, and the record is the planner's fallback: `_arm_position` reads the checkpoint
    `or prev_start`, so an arm whose checkpoint was just deleted would be planned at the
    counter the LAST prepare wrote -- typically `n_batches` -- and would then run an empty
    loop over a database that no longer exists.  Nothing raises; the arm simply produces no
    rows.  Dropping the entry makes `prev_id` None, which is the fresh branch.

    The file is removed outright when no arm survives it, so the leaf reads as a new run
    rather than a resume of nothing.
    """
    resume = _load_resume(run_dir)
    if not resume:
        return
    run_ids = {k: v for k, v in (resume.get('run_ids') or {}).items() if k not in arms}
    starts  = {k: v for k, v in (resume.get('next_batch') or {}).items() if k not in arms}
    if run_ids:
        _save_resume(run_dir, run_ids, starts)
    else:
        os.remove(_resume_path(run_dir))


def _reconcile_coupled_unit(pair_dir, leaves, n_batches, log, tag='', mid_flight=False) -> bool:
    """Is this coupled pair complete — and if it is torn, REPAIR it.  Site-dock 10 sections 1-3.

    `leaves` is `[(channel_run_dir, [arm_key, ...]), ...]` in the declared channel order
    (store first), positionally aligned: arm rank r of one leaf pairs with rank r of the
    other, which is exactly how `_prepare_site_run` builds the units.  Returns True only when
    every leaf is complete, i.e. when `_build_work_units` may skip the pair.

    THE FOUR STATES.  Both complete -> skip.  Neither started, or both mid-flight and in step
    -> nothing to do; the ordinary per-arm resume below handles them.  The other two are the
    ones this function exists for:

      * TORN -- one leaf finalized and the other not.  Reachable only by a kill between the
        two `_finalize_config_run` calls, and the repair is whole-dir: a finalized leaf has
        had its checkpoints cleaned, so there is no per-arm position left to compare and every
        arm of BOTH leaves replays.  The complete leaf's marker is removed, which is what
        un-finalizes it.
      * SKEWED -- the two leaves' copies of one arm disagree about where they are.  Each leaf
        writes its own `_ckpt_<arm>.pkl` inside one batch loop (`strategy_runner`: leaf A
        saves, then leaf B), so a kill between the two saves leaves the pair one checkpoint
        apart.  That rank replays in both leaves.

    THE REPLAY IS FORCED, NOT CHOSEN.  The leaves step in lockstep over a shared dock, a
    shared put clock and a shared receiving clock, so leaf B cannot be stepped without leaf A
    being stepped.  There is no version of this where the leaf that got further is spared; the
    only question was whether its output is discarded CLEANLY or left to collide with the
    replay, and that question has one answer.  The reset is exact -- the same reset
    `_plan_strategy_start` already applies to a partial arm, which replays bit-identically
    from batch 0 -- which is why refusing the resume outright would buy nothing.

    THE SKEW IS WHY THIS IS NOT OPTIONAL.  `_run_strategy_worker_impl` refuses a unit whose
    leaves do not share one batch range, and that refusal cannot clear itself: a leaf already
    at `n_batches` is planned as a done arm and never reset, while its sibling resets to 0, so
    every later `--resume` reproduces the same disagreement and the pair is wedged for good.

    THE SITE DB IS THE UNIT'S THIRD OUTPUT and joins the reset -- for every rank that
    REPLAYS, not only for the ranks that disagree.  `reset_strategy_db` knows an arm's
    `sim_<arm>.db`, its keyframe sibling and its checkpoint; it does not know the site DB, so
    a replayed unit would append a SECOND run's trailer and drain rows to it, and `find_run`
    resolves the OLDEST run -- so every site yard figure would render over the abandoned one.
    A pair in step but PARTIAL is not torn and not stale, and it replays all the same.  It is the only such artifact -- every pack-denominated
    receiving quantity lives in its own channel's sim DB (ADR-0005), and the run layout's
    `coupled` marker is written once per run by the parent, not per unit -- so the reset surface
    is exactly three things per leaf plus one per rank.

    `mid_flight` is the supervisor's RETRY, not a resume of a dead run.  A tear seen there
    means a `_finalize_config_run` raised while the pool was still up, and every unit is
    already in `done_uids` -- so a repair would delete the output of units that will never be
    resubmitted.  The tear is reported and left for the next `--resume`, where the units are
    planned again and the repair is safe.

    IT IS NOT SILENT.  `run.log` is the only place a multi-hour run's damage is visible, and
    "a finished leaf was discarded and replayed" is a line a reader must be able to find.
    """
    states = [_leaf_is_complete(d) for d, _ in leaves]
    if all(states):
        return True

    # Ranks are paired positionally.  A ragged arm set is `_prepare_site_run`'s refusal, a few
    # lines later and by name; reconciling the ranks that DO line up cannot mask it, because
    # completeness is a property of the directory rather than of any arm.
    ranks = list(zip(*[arms for _, arms in leaves]))
    if any(states):
        torn, stale = True, list(ranks)                    # whole dir: no positions survive
    else:
        # One resume record per leaf, read once: it is the fallback `_arm_position` uses when
        # an arm has no checkpoint yet, and re-reading it per arm would be the same answer
        # a hundred times on a full arm suite.
        torn = False
        prev = [(_load_resume(d) or {}).get('next_batch') or {} for d, _ in leaves]
        at = {r: {_arm_position(d, a, int(p.get(a, 0) or 0))
                  for ((d, _), p, a) in zip(leaves, prev, r)} for r in ranks}
        stale = [r for r in ranks if len(at[r]) > 1]
    if mid_flight:
        # A LIVE RETRY IS NOT A RESUME, and NOTHING below may run here -- not the reset, not
        # the un-finalize, and not the site-DB discard.  Every unit that wrote these leaves
        # is already in `done_uids` and will not be resubmitted, so any repair would delete
        # output nothing rebuilds.  The tear is reported and left for the next `--resume`.
        if stale:
            _why = ('one leaf is finalized and the other is not' if torn else
                    f'{len(stale)} arm pair(s) disagree about where they are')
            log.error(
                f'  [{tag}] TORN coupled pair during a live retry ({_why}) -- NOT '
                f'repairing: the units that wrote these leaves are already done and would '
                f'not be resubmitted, so the repair would delete output nothing rebuilds. A '
                f'finalize must have failed above; resume this run to repair it.')
        return False
    # THE SITE DB GOES WITH THE REPLAY, and the set is every rank that WILL replay -- not
    # only the ranks that disagree.  A pair killed mid-flight with both leaves at the same
    # batch is IN STEP and therefore not stale, but strategy-granularity resume still resets
    # both arms and replays them from batch 0 (`_plan_strategy_start`), so its site DB would
    # survive and take a SECOND run's trailer and drain rows.  `find_run` then answers every
    # filtered query from the abandoned run (`ORDER BY run_id LIMIT 1`, the oldest) and every
    # site yard figure renders over a truncated one, with no symptom.  A finished rank is
    # planned as done and writes nothing more, so it is the one shape that keeps its file.
    _replaying = (list(ranks) if torn
                  else [r for r in ranks if any(q != int(n_batches) for q in at[r])])
    for r in _replaying:
        _site_db = _site_db_path(pair_dir, *r)
        if os.path.exists(_site_db):
            os.remove(_site_db)
            log.warning(f'  [{tag}] discarded the site DB for arm pair {r}: the pair replays '
                        f'from batch 0, and a surviving file would take a SECOND run of '
                        f'trailer and drain rows that `find_run` would then read AROUND')
    if not stale:
        return False

    _what = ('one leaf is finalized and the other is not'
             if torn else
             f'{len(stale)} arm pair(s) disagree about where they are')
    log.warning(f'  [{tag}] TORN coupled pair: {_what}. A coupled unit writes BOTH leaves from '
                f'one batch loop over a shared dock and put pool, so neither leaf can be '
                f'replayed alone -- discarding {len(stale)} arm pair(s) in both leaves and '
                f'replaying them from batch 0 (bit-identical, site-dock 10).')
    for (run_dir, _arms), arms in zip(leaves, zip(*stale)):
        for a in arms:
            reset_strategy_db(run_dir, _arm_db_path(run_dir, a), a)
        _forget_arms(run_dir, set(arms))
        if torn:
            # The un-finalize.  Removing the marker is what takes the leaf back out of
            # "complete"; the resume file is already gone (that is what made it complete),
            # so `_plan_strategy_start` takes its fresh branch over the db this just reset.
            _meta = _meta_path(run_dir)
            if os.path.exists(_meta):
                os.remove(_meta)
                log.warning(f'  [{tag}] un-finalized {run_dir} '
                            f'(removed {os.path.basename(_meta)})')
    return False


def put_constant(totals, override) -> dict:
    """ONE channel's put-away price, as the recorded constant ("Give put-away a per-channel
    expected travel", 2026-09-08).

    This was a SITE ratio, `sum(put_s) / sum(put_units)` over both channels, and it averaged a
    real 3.4x spread away: the era re-read measured 98.5 s/unit put away on the store against
    29.2 on fulfillment.  The site TOTAL was right either way -- the ratio's denominator makes
    `units x price` sum back to `sum(put_s)` -- so the crew was correctly SIZED while both
    leaves' expected utilization sat about 0.21 outside the band, in OPPOSITE directions.
    `ScriptTotals.put_s` was already this channel's own expectation (priced by
    `expected_travel.put_site_pricer` over its own section's class-uniform destination); only
    this constant threw the per-channel structure away.

    `override` is `--s-put`, which stays ONE key: an operator declaring a price is asserting a
    single site-wide number, which is an honest declaration rather than the derived average
    that was the defect.  It is stamped onto EVERY channel, and each channel's own expectation
    rides along as `expected`, so the record still shows what the declaration displaced -- and
    the two channels therefore carry different `expected` values under one flag.

    A section the script implies no put-away for prices at 0.0 rather than dividing by zero;
    the caller warns, because a silent 0 under a note claiming an expected travel is a lie.

    Module-level and pure so it is testable without a run, and spawn-safe by construction
    (the repo's `ProcessPoolExecutor` rule).
    """
    own = (totals.put_s / totals.put_units) if totals.put_units else 0.0
    if override is not None:
        return _staffing.constant(float(override), 'declared', source='override', expected=own)
    return _staffing.constant(
        own, 'derived', source='expected_travel', placement='uniform',
        note='expected put travel from the aisle mouth over THIS CHANNEL\'s class-uniform '
             'destination, plus the packs\' handling at the class-mean height')


def refuse_unpriceable_put(global_cfg: dict) -> None:
    """REFUSE a put-away configuration the era's closed form cannot price.

    `staffing.implied_reorders` prices a put as travel from the mouth plus handling at the
    destination -- no cart-swap term, one site speed -- because that is the whole cost of a
    put on the ONE uncarted queue the era runs (`put_queue.single_queue`, `swap_coef = 0`).
    A split queue set carries per-queue crews with their own speeds and a `swap_coef` the
    runner does bind (`strategy_runner`: `store_and_fulfillment(... swap_coef=...)`), and
    the derivation would silently under-price both.  `run_simulation._check_era_flags`
    refuses the two flags at the parser; this is the same refusal at the SEAM, for a
    caller that reached the derivation without the CLI (a test harness, a programmatic
    launch, a spec that wrote CONFIG directly).  Pure and module-level: tested with a dict.
    """
    if global_cfg.get('put_queue_split'):
        raise ValueError(
            'the era derivation prices ONE uncarted put queue at one site speed; '
            'put_queue_split is on, so its per-queue crews and swaps would be under-priced '
            'in silence -- refused (run_simulation._check_era_flags says the same at the CLI)')
    coef = float(global_cfg.get('put_swap_coef') or 0.0)
    if coef > 0.0:
        raise ValueError(
            f'the era derivation has no cart-swap term and the single put queue never reads '
            f'one; put_swap_coef={coef} would be recorded and ignored -- refused')


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
    refuse_unpriceable_put(CONFIG['global'])
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
    # Both seconds-per-unit constants are keyed by CHANNEL: stage A fills `s_pick`, stage B
    # fills `s_put` (per channel since "Give put-away a per-channel expected travel").
    constants: dict = {'s_pick': {}, 's_put': {}}
    new_runs: list = []
    for name, grp in groups.items():
        ch = grp['ch']
        a = stage_a[name]
        constants['s_pick'][name] = a['s_pick']
        batch = a['batch']
        # THE DERIVED CREW (ADR-0004).  The channel was built at the placeholder count
        # (`channel_pickers`); stage A solved the crew from the first-time confidence, and
        # the picker profile AND its pick config carry it from here -- `k_pickers` in the
        # payload, the run params and the worker's crew all read `ch.picker`.
        K = int(a['pickers'])
        picker = _dc_replace(ch.picker, num_pickers=K,
                             cost=_dc_replace(ch.picker.cost, num_pickers=K))
        new_ch = _dc_replace(ch, picker=picker,
                             batch_mean_fraction=batch['mean_fraction'],
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
        constants['s_put'][name] = put_constant(totals, overrides.get('s_put'))
        if not totals.put_units:
            # The pick side warns on an unpriceable section (`era_coverage.stage_a`); say the
            # same here rather than recording a 0.0 whose note claims an expected travel.
            log.warning(f'  [staffing] {name}: put-away could not be priced (the script implies '
                        f'no units put away) -- recording s_put = 0 for this channel')
    derived = _staffing.derive(
        inputs=inputs, constants=constants, day_seconds=S,
        channels={n: {'pickers': a['pickers'],
                      'pickers_provenance': a.get('pickers_provenance', 'derived'),
                      'daily_demand_units': a['daily_demand_units'],
                      'demand': a.get('demand'), 'guarantee': a.get('guarantee'),
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
    _put_prices = ', '.join(f"{n}={c['value']:.3f} ({c['provenance']})"
                            for n, c in sorted(constants['s_put'].items()))
    log.info(f"  [staffing] put crew={derived['put']['crew']} "
             f"(s_put/unit {_put_prices}"
             f"; load={derived['put']['load_seconds_per_day']:,.0f} s/day)"
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


def _check_campaign_pin(pin: dict | None, label: str, derived: dict) -> None:
    """Refuse a pair whose fresh derivation is not the one the campaign pinned.

    `pin` is `run_spec['staffing']['pin']` -- `whatif_config.PHASE2_STAFFING_PIN`, stamped on
    the run at launch, and None on every run that is not a phase-2 campaign, which is the
    no-op path.  The digest is `staffing.pin_digest` over a rounded projection of the derived
    block ("Re-size the funnel in site days").

    THIS IS THE HALF `validate_spec` CANNOT DO.  The spec check fires before a directory
    exists, so all it can see is that a pin was declared; the derivation is per pair and only
    exists once the catalogue is read, the warehouse built and the script precomputed -- here.
    Two refusals, one question: is phase 2 running the warehouse phase 1 ranked?

    A pin that names no labels at all, or does not name THIS one, is a refusal too. Phase 1
    ranked the pairs it ran; a pair phase 2 adds afterwards was never ranked, and letting it
    through unchecked is the silent case the pin exists to end.
    """
    if not pin:
        return
    want = pin.get(label) if isinstance(pin, dict) else None
    got = _staffing.pin_digest(derived)
    if want == got:
        return
    if want is None:
        raise RuntimeError(
            f'[staffing] this run carries a campaign staffing pin, and it does not name the '
            f'pair {label!r} (it names {sorted(pin) if isinstance(pin, dict) else pin!r}). '
            f'Phase 1 ranked the pairs it ran; a pair phase 2 adds afterwards was never '
            f'ranked. Point the run at phase 1 catalogue, or re-run phase 1 for this pair.')
    raise RuntimeError(
        f'[staffing] the derivation for pair {label!r} hashes to {got!r}, and this run was '
        f'launched against the campaign pin {want!r}. Phase 1 ranked ONE warehouse and this '
        f'is a different one -- the crews, the day’s demand, one of the two '
        f'expected-travel prices or the script depth has moved since the ranking was taken. '
        f'Diff this run derived block against `staffing.projection[{label!r}]` in the '
        f'phase-1 run restock_selection.json to see which. Re-run phase 1 under the current '
        f'derivation, or roll the change back; do not re-copy the pin to make this pass.')


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
    _check_campaign_pin(st.get('pin'), label, derived)
    prev = (st.get('derived') or {}).get(label)
    if prev is not None:
        diffs = _staffing.derived_differs(prev, derived)
        if diffs:
            # A run recorded before 2026-09-08 carries `put.s_put` as ONE site constant;
            # it is a channel map now ("Give put-away a per-channel expected travel"), so
            # every such run is refused here.  Say so, because on a STORE-ONLY pair the
            # numbers are all identical -- same price, same crew, same expected utilization
            # -- and only the record's SHAPE moved, which makes the generic "under different
            # crews" wording a false diagnosis.  On a mixed pair the crews really did
            # re-band and the generic wording is the true one.
            shape_move = any(d.startswith('/put/s_put') for d in diffs)
            why = ('; `put.s_put` became a per-channel map on 2026-09-08, so a run recorded '
                   'before that is refused here on SHAPE -- on a store-only pair every '
                   'derived number is unchanged and only the record moved'
                   if shape_move else '')
            raise RuntimeError(
                f'[staffing] the derivation for pair {label!r} disagrees with the one this run '
                f'recorded at {", ".join(diffs[:8])}{" ..." if len(diffs) > 8 else ""}{why}; the '
                f'recorded derived block is authoritative on resume, so this run cannot '
                f'continue under different crews. Start a new run instead.')
        return
    # A MULTI-CELL era run has already recorded the pair's COVERAGE block: the freeze planned
    # the inventory (`_record_coverage` from `scenario`), and every cell then reshapes the
    # FROZEN inventory, whose derivation carries `coverage: None`.  Replacing the whole
    # calibration block here dropped that record on the first cell -- the analysis stage then
    # found "no stock declaration" for every cell and rebuilt nothing (found by the derived
    # fill's sweep canary, "Derive the fill headroom from the fragmentation").  A recorded
    # coverage stands; the fresh block fills in beside it.
    prev_cal = (st.get('calibration') or {}).get(label) or {}
    if calibration.get('coverage') is None and prev_cal.get('coverage') is not None:
        calibration = {**calibration, 'coverage': prev_cal['coverage']}
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
            if _own is not None and era_on():
                # Under the era the crew is SOLVED from the first-time confidence
                # (ADR-0004); a per-arm count is a typed crew, which is a regime nobody
                # derived, whether or not it happens to equal the placeholder.
                raise ValueError(
                    f"pick config {cfg.get('name')!r} declares num_pickers={_own}, but under "
                    f"--shift-drain-or-cap the {name} channel's crew is DERIVED from the "
                    f"declared demand and first_time_confidence; drop the key from the "
                    f"config module.")
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


def _stamp_identity(sa: dict, label: str, cfg_name: str) -> tuple:
    """Carry the unit's IDENTITY on its payload; return the unit's uid.

    A uid is positional, and its four slots mean `(pair, config, channel, arm)` only because
    every unit today is one channel leaf.  The coupled unit (site-dock 02) is
    `(label, 'coupled', arm_store, arm_ful)` — same arity, different meanings — so a parent-side
    reader asking "which group(s) does this finalize" or "which arm is this" must not answer by
    slicing.  It reads `group_keys` / `arm_key`, which the unit states about itself.

    A one-leaf unit states exactly `[uid[:3]]` and `uid[3]`, so nothing moves today.
    """
    gk = (label, cfg_name, sa.get('channel_key', ''))
    sa['group_keys'] = [gk]
    sa['arm_key']    = sa['strategy']
    return (*gk, sa['arm_key'])


def _stamp_site_identity(ua: dict, label: str, cfg_names: dict) -> tuple:
    """`_stamp_identity` for a COUPLED unit: two group keys, two arms, one uid.

    The uid is `(label, 'coupled', arm_store, arm_ful)` — arity 4, so `_tag_of` and every
    existing key path read it unchanged, but only the first slot still means what it used to.
    The literal `'coupled'` in the config slot is deliberate and honest (site-dock 02 section
    1): the unit is a member of NEITHER config subtree — `config` sits ABOVE `channel` in the
    tree and the two channels draw from different config sets, so its two leaves share no
    ancestor below `<pair>/` — and a synthetic config name that looked real would invite a
    walker to go looking for its directory.

    `arm_key` is None, not one of the two: a unit with two arms has no single arm, and a
    reader that takes one of them gets the other leaf's number with nothing to say so.
    `arm_keys` carries both, positionally with `group_keys`.
    """
    gks = [(label, cfg_names[_l['channel_key']], _l['channel_key']) for _l in ua['leaves']]
    ua['group_keys'] = gks
    ua['arm_keys']   = [_l['strategy'] for _l in ua['leaves']]
    ua['arm_key']    = None
    return (label, 'coupled', *ua['arm_keys'])


def _build_work_units(pairs, base_dir, shared_by_pair, log, log_queue, max_workers,
                      skip_completed, resume_granularity, mid_flight=False):
    """(Re-)prepare all work units from on-disk state.

    Returns (work_units, meta):
      work_units : list of (uid, args) where uid = (label, cfg_name, channel, strategy) and
                   args carries its own `group_keys` + `arm_key` (the parent reads those, not
                   uid slices — the uid's slots are not stable across unit kinds).
      meta       : {group_key: {'sim_skeleton', 'members'}}; group_key = uid[:3].  A group is
                   finalized only when EVERY member uid succeeds (see _run_pool) — so a crashed
                   arm never finalizes its group, keeping resume.pkl and staying resumable.
    Re-derives each arm's start from its ADVANCED _ckpt_*.pkl, so resubmit/resume is idempotent.

    `mid_flight` says the POOL IS STILL UP and this is the supervisor's rebuild-and-resubmit
    after a hard worker death, not a `--resume` of a dead run.  Only the coupled reconciler
    reads it, and only to decline a repair whose outputs nothing would rebuild — see
    `_reconcile_coupled_unit`.  Default False, so an uncoupled run never reaches it.
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
        # THE COUPLED PAIR.  One work unit finalizes two channel leaves, so the site's dock,
        # receiving crew and putters are fielded ONCE rather than once per leaf.  Declared per
        # run (`couple_channels`), never inferred: site-dock 06 couples every cell of the
        # campaign INCLUDING its inbound-off pole, so "coupling rides the inbound flag" is no
        # longer a rule a reader could derive from the flag.  A store-only catalogue has
        # nothing to couple and `_prepare_site_run` refuses one.
        #
        # THIS FLAG IS THE RECONCILER'S GROUND TRUTH, and it is a RESTORED declaration rather
        # than an inference: `couple_channels` rides the resume-restored parameter set and
        # reaches the tree as the run layout's `coupled` (site-dock 18), so a `--resume`
        # arrives back here with the same answer the killed run had.  Nothing reads a uid to
        # decide it -- a run that resumed uncoupled would rebuild per-channel units over a
        # tree whose leaves were written by coupled ones, and the completeness question below
        # would then be asked one leaf at a time, which is the state this whole ticket exists
        # to make impossible.
        if couple_channels() and mixed:
            _cfg_names = {ch.name: _config_name(cfg) for ch, cfg in channel_runs}
            # BOTH leaves or neither (site-dock 10).  A unit writes two leaves, so a pair with
            # one finalized leaf is a TORN pair: it must be re-run, and the finished leaf must
            # be un-finalized first or its arms would be planned over populated databases.
            # `_reconcile_coupled_unit` answers "is this pair complete" and repairs as a side
            # effect; it lives above both `_prepare_channel_run` calls because a two-leaf
            # question cannot be asked inside a one-leaf prepare, and the reset must happen
            # parent-side before any worker reopens a file (`reset_strategy_db`, Windows).
            _leaves = [(os.path.join(pair_dir, _cfg_names[ch.name], ch.name),
                        [s.key for s in strategies_for(ch.restocks)])
                       for ch, _ in channel_runs]
            if skip_completed and _reconcile_coupled_unit(
                    pair_dir, _leaves, CONFIG['global']['n_batches'], log,
                    tag=f'{label}/coupled', mid_flight=mid_flight):
                log.info(f'  [{label}/coupled] both leaves already complete — skipping (resume)')
                continue
            try:
                unit_args, sim_skeletons = _prepare_site_run(
                    channel_runs, mixed, shared, pair_dir, log, workers=max_workers,
                    resume_granularity=resume_granularity)
            except Exception as exc:
                log.error(f'  [{label}/coupled] prepare FAILED: {exc}', exc_info=True)
                continue
            for ua in unit_args:
                ua['log_queue'] = log_queue
                work_units.append((_stamp_site_identity(ua, label, _cfg_names), ua))
            # Every leaf's group takes EVERY unit as a member: a coupled unit writes both
            # leaves, so neither leaf may finalize until every unit succeeded.  That is the
            # same rule a per-channel group already follows, stated over the unit set the
            # coupled run actually has.
            _members = frozenset(uid for uid, _ in work_units[-len(unit_args):])
            for sk in sim_skeletons:
                gk = (label, _cfg_names[sk.get('channel', '')], sk.get('channel', ''))
                meta[gk] = {'sim_skeleton': sk, 'members': _members}
            continue
        for ch, cfg in channel_runs:
            cfg_name = _config_name(cfg)
            # Outputs live at <cfg>/<channel>/ (mixed) or <cfg>/ (store-only); the skip guard
            # keys on that exact dir.  A finalized channel-run is skipped so resume never
            # recomputes complete work -- `_leaf_is_complete` is that test, shared with the
            # coupled reconciler so the one-leaf and two-leaf rules cannot drift apart.
            ch_run_dir = os.path.join(pair_dir, cfg_name, ch.name) if mixed \
                         else os.path.join(pair_dir, cfg_name)
            if skip_completed and _leaf_is_complete(ch_run_dir):
                log.info(f'  [{label}/{cfg_name}/{ch.name}] already complete — skipping (resume)')
                continue
            try:
                strategy_args, sim_skeletons = _prepare_channel_run(
                    ch, cfg, mixed, shared, pair_dir, log, workers=max_workers,
                    resume_granularity=resume_granularity)
                for sa in strategy_args:
                    sa['log_queue'] = log_queue
                    work_units.append((_stamp_identity(sa, label, cfg_name), sa))
                for sk in sim_skeletons:
                    gk = (label, cfg_name, sk.get('channel', ''))
                    members = frozenset((*gk, s['key']) for s in sk['strategies'])
                    meta[gk] = {'sim_skeleton': sk, 'members': members}
            except Exception as exc:
                log.error(f'  [{label}/{cfg_name}/{ch.name}] prepare FAILED: {exc}', exc_info=True)
    return work_units, meta
