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
import shutil
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
from Optimization.config.sim_config import (            # noqa: F401
    CONFIG, INBOUND_KEYS, REGRESSION_CONFIGS, STORE_CONFIGS, FULFILLMENT_CONFIGS,
    seed_world, seed_batches, n_batches, k_pickers, store_restocks, store_fill,
    _OUTPUT_DIR, _DEFAULT_PROFILES_DIR, _CATEGORIES, _HANDLINGS, _AISLE_W, _AISLE_H,
    _STORE_PICKERS, _FF_PICKERS, _CART_TYPES,
    regime_sizing_from_config, _setup_logging, _checkpoint_every,
    _config_name, _build_pick_cfg, _clean_path, _load_env,
)
# The lead draw's domain tag, imported rather than restated: it is recorded in the run spec
# beside the lead shape it keys, and a second copy of a literal whose whole job is to be
# stable is a copy that can drift.
from Inbound.transit import _LEAD_TAG                                                # noqa: F401
from Optimization.simdriver.sim_assets import build_shared_assets                    # noqa: F401
from Optimization.runschema.sim_manifest import (                                    # noqa: F401
    _resume_path, _save_resume, _load_resume, write_run_manifest,
    _write_run_spec, _load_run_spec, _run_spec_path, _pair_bindings,
    write_run_layout, read_run_layout, _run_layout_path,
)


from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_planning import structural_bin_floor
from Optimization.config import strategies                # noqa: F401  (--whatif arm override)
from Optimization.config.strategies import STRATEGIES, strategies_for
from Warehouse.layout.Storage_Primitive import StoreCart

from Optimization.persistence.Picking_Data import create_run, init_run_db
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.kernel.regime import STORE, FULFILLMENT

from Optimization.simdriver.strategy_runner import (
    load_worker_checkpoint, _run_strategy_worker, _cleanup_checkpoints, reset_strategy_db,
)
from Optimization.simdriver.batch_precompute import ensure_batches


# Directory-layout walkers live in runlayout (single owner of the tree shapes);
# re-imported here so rs.discover_db_pairs / rs.find_latest_db_pairs keep working.
from Optimization.runschema.runlayout import discover_db_pairs, find_latest_db_pairs, iter_sim_dbs  # noqa: F401,E402

# ── Driver package (Phase 2 extraction) ──────────────────────────────────────────
# The cell matrix, work-unit builder, crash-recovery supervisor, and scenario driver moved to
# Optimization.simdriver.  Re-exported here because tests, Diagnostics, and the arch graph reference
# them as rs.<name>.  CONFIG stays the SAME object (from sim_config above), never rebound —
# _apply_cell / _run_whatif_matrix mutate it in place.
from Optimization.simdriver.cells import (                # noqa: F401,E402
    _SCHED_SHORT, _build_cells, _apply_cell, _tightest_split, _cell_complete,
    reference_cell,
)
from Optimization.simdriver.workunits import (            # noqa: F401,E402
    _plan_strategy_start, _prepare_channel_run, _channel_runs_for, _build_work_units,
)
from Optimization.simdriver.supervisor import (           # noqa: F401,E402
    _finalize_config_run, _finalize_ready_groups, _run_pool, _supervise, _run_workers_flat,
)
from Optimization.simdriver.scenario import (             # noqa: F401,E402
    _warn_blank_arms, _run_scenario, _run_whatif_matrix,
)


# ── entry point ────────────────────────────────────────────────────────────────

def _nonneg_int(text: str) -> int:
    """An argparse `type=` that rejects negatives but ALLOWS zero.

    `--recv-crew-size 0` is meaningful -- it is the off switch, and the default -- so unlike
    `_positive_int` below, zero passes.  Negative does not: it would reach
    `crew_clock.new_clocks` and raise three layers down, or worse, be swallowed by a
    truthiness guard on the way.
    """
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not an integer')
    if n < 0:
        raise argparse.ArgumentTypeError(f'{n} is negative; a crew size is 0 or more')
    return n


def _positive_float(text: str) -> float:
    """An argparse `type=` for a DURATION that is meaningless at zero.

    `--recv-day-seconds 0` would mean "the crew has a day of no length", which is not the
    same as "the crew has no whistle" (that is omitting the flag) and is not a configuration
    anyone wants.  Rejected at the parser for the same reason `--n-batches 0` is: argparse
    prints the flag name and exits 2, instead of the value being quietly turned into its
    opposite by an `or`.
    """
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not a number')
    if v <= 0.0:
        raise argparse.ArgumentTypeError(
            f'{v} is not positive; omit the flag for "no whistle" rather than passing 0')
    return v


def _nonneg_float(text: str) -> float:
    """An argparse `type=` for a coefficient where 0 is the default and negative is nonsense.

    `swap_coef` is validated nowhere else -- not in `PutQueueSpec.__post_init__`, not in
    `PutQueue`. A negative value makes a cart swap REDUCE the duration of the put it precedes,
    and a negative total moves the crew clock BACKWARD, which makes `crew_clock.can_start`
    true again after the whistle has already blown. None of that raises anywhere.
    """
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not a number')
    if v < 0.0:
        raise argparse.ArgumentTypeError(
            f'{v} is negative; a swap cannot give time back, and a negative total would '
            f'move a crew clock backward past its own whistle')
    return v


def _positive_int(text: str) -> int:
    """An argparse `type=` that rejects zero and negatives.

    `--n-batches 0` was accepted, discarded by a truthiness guard, and the run went ahead
    on CONFIG's value — so a smoke run asking for nothing quietly became a full one.  A
    count that is meaningless at zero should fail at the parser, where argparse prints the
    flag name and exits 2, rather than three layers down or not at all.
    """
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not an integer')
    if n < 1:
        raise argparse.ArgumentTypeError(f'must be 1 or more, got {n}')
    return n


def _futuresight_window(text: str):
    """An argparse `type=` for the futuresight window: `'all'` or a non-negative int.

    The knob is a batch COUNT with a string sentinel for the oracle, and `_futuresight_batches`
    (the spec-build normalizer) rejects `'5'` as firmly as it rejects `'oracle'` — its
    `w != raw` test is what stops a fractional value, and a numeric STRING fails it too.  So
    the conversion has to happen at the parser: without it, `--inbound-futuresight-batches 5`
    would parse cleanly and then refuse at spec build, naming the settings constant rather
    than the flag the user actually typed.
    """
    if text == 'all':
        return 'all'
    try:
        w = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is neither 'all' nor an integer count of script batches")
    if w < 0:
        raise argparse.ArgumentTypeError(f'{w} is negative; a window looks forward or not at all')
    return w


def _apply_run_spec(args, spec, explicit):
    """Overlay a saved run_spec onto args for --resume: the saved value is the base; a flag the
    user explicitly typed on the resume command overrides it (with a warning).  Returns
    (store_composition_override, notes) — the resolved store composition is injected directly so
    a since-deleted --s-composition file can't break resume."""
    notes = []
    for f in ('n_batches', 'max_skus', 's_max_aisles', 's_max_bins', 's_min_bins',
              'ff_max_aisles', 'ff_max_bins', 'ff_min_bins', 'keyframe_interval', 'whatif', 'spec',
              'profiles_dir', 'all_profiles', 'workers', 'max_tasks_per_child',
              'max_retries', 'resume_granularity',
              # Sizing params: a resume MUST rebuild the same warehouse, so these are as
              # load-bearing here as the bin caps beside them.
              'store_fill', 'ff_fill', 'checkpoint_frac',
              # Batch-sampler era: a resume MUST regenerate the same batch sequence.
              'sampler',
              # The working day decides when batches are released and when pickers stop, so
              # a resume that forgot it would finish the arm on a different clock than it
              # started on.
              'work_day_seconds', 'releases_per_day', 'cut_at_day_end',
              'roll_over_unpicked',
              # ...and the receiving crew, for the same reason: an arm that resumed without
              # its dock would finish having received for free.
              'recv_crew_size', 'recv_day_seconds', 'recv_day_origin',
              # ...and the put-away shape, for the same reason: an arm that resumed
              # without its split would finish with one crew where it started with three.
              'put_queue_split', 'put_cart_crew', 'put_pallet_crew', 'put_ff_crew',
              'put_cart_staging', 'put_pallet_staging', 'put_ff_staging',
              'put_swap_coef',
              # ...and the whole inbound family, for the same reason twice over: an arm that
              # resumed without its yard would finish on v1's drain-everything dock, and one
              # that resumed without its lead shape would redraw a different arrival schedule.
              # Spliced from the one list rather than retyped, so a new inbound knob cannot be
              # recorded and then not restored.
              *INBOUND_KEYS,
              # ...and the seeds it is drawn from, plus the world it is drawn against.
              'seed_world', 'seed_batches'):
        if f not in spec:
            continue
        if f in explicit:
            if getattr(args, f) != spec[f]:
                notes.append(f'  run_spec override: {f} {spec[f]!r} -> {getattr(args, f)!r} (explicit flag wins)')
        else:
            setattr(args, f, spec[f])
    return spec.get('s_composition'), notes


def main():
    # FIRST statement in main, before the parser exists: `--help` is printed and exited from
    # INSIDE parse_args, so anything placed after it never runs on that path.  U+2192 (in
    # --s-composition's help) has no cp1252 mapping — unlike the em/en dashes and ellipses
    # elsewhere here — so `--help` on a legacy console died with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-utf-8 consoles (e.g. cp1252 → arrows)
    except Exception:
        pass

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
                        help='PINNED AT 1 — accepted for compatibility with saved run_specs '
                             'and older scripts, but a larger value is refused with a warning. '
                             'One fresh process per job is the cleanest memory flush, and the '
                             'only run that ever honoured a larger value deadlocked the pool at '
                             'a cell boundary (see supervisor._supervise). Workers reload their '
                             'assets per job anyway, so the spawn saving is ~10 s against a job '
                             'measured in minutes.')
    parser.add_argument('--max-skus', type=int, default=None, metavar='N',
                        help='Cap inventory to the first N SKUs (smaller warehouse for quick runs)')
    # Per-channel warehouse-sizing caps (store vs fulfillment sized independently).
    parser.add_argument('--s-max-aisles', type=int, default=None, metavar='N',
                        help='STORE: cap total store aisle count (scales store replicas down).')
    parser.add_argument('--s-max-bins', type=int, default=None, metavar='N',
                        help='STORE: cap total store bins (trims store aisle replicas).')
    parser.add_argument('--s-min-bins', type=int, default=None, metavar='N',
                        help='STORE: require AT LEAST N store bins (min wins over --s-max-bins).')
    # Fill headroom, per channel.  These had NO CLI until 2026-08-15 and were reachable only by
    # editing sim_config — a file in the run-tree contract's SHAPE_SOURCES, so changing a VALUE
    # tripped a schema preflight.  Recorded into run_spec.json like every other run-shaping
    # param, so a later standalone re-analysis rebuilds the same warehouse.
    parser.add_argument('--store-fill', type=float, default=None, metavar='F',
                        help='STORE: bin fill headroom the warehouse is sized to (default: the '
                             "value in sim_config's CONFIG). 0.9 = size for 90%% occupancy.")
    parser.add_argument('--ff-fill', type=float, default=None, metavar='F',
                        help='FULFILLMENT: bin fill headroom (default: CONFIG). Set separately '
                             'from --store-fill; the two regimes are sized independently.')
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
    # Defaulted FROM CONFIG rather than to a literal: this flag is assigned unconditionally
    # into g['keyframe_interval'] below, so a literal here would silently override the
    # declared default and make sim_config's value dead.
    parser.add_argument('--keyframe-interval', type=int,
                        default=CONFIG['global']['keyframe_interval'], metavar='K',
                        help='Write a full bin snapshot to <run>.keyframes.db every K batches '
                             '(0 disables). Spatial state is reconstructed from the '
                             'bin-mutation log, not from these — a keyframe is the '
                             'INDEPENDENT audit of that fold plus a quantity anchor. Lower K '
                             'buys more audit points, not more accuracy. Default '
                             f'{CONFIG["global"]["keyframe_interval"]}.')
    parser.add_argument('--n-batches', type=_positive_int, default=None, metavar='N',
                        help='Override the per-run batch count (default '
                             f'{CONFIG["global"]["n_batches"]}). Use a small value for quick smoke runs.')
    # The two seeds decide whether two runs are COMPARABLE at all -- same world seed = same
    # warehouse and catalogue, same batch seed = same demand stream -- and until now neither
    # had a flag while --workers, which changes no result, did.  Changing one and not
    # recording it is how an incomparable pair of runs gets published as a comparison.
    parser.add_argument('--seed-world', type=int, default=None, metavar='S',
                        help='Override the world seed: warehouse + catalogue construction '
                             f'(default {CONFIG["global"]["seed_world"]}). Two runs with '
                             'different world seeds are NOT comparable.')
    parser.add_argument('--seed-batches', type=int, default=None, metavar='S',
                        help='Override the base batch-stream seed (default '
                             f'{CONFIG["global"]["seed_batches"]}). Each channel draws from '
                             'this plus its own offset, so both streams move together.')
    # Defaulted FROM CONFIG (the --keyframe-interval precedent) so the assignment below is
    # unconditional and sim_config's era value stays the single source of truth.
    parser.add_argument(
        '--work-day-seconds', type=float, default=CONFIG['global']['work_day_seconds'],
        help='Working-day length in seconds (default: the shift length). Only matters '
             'alongside --releases-per-day or --cut-at-day-end.')
    parser.add_argument(
        '--releases-per-day', type=int, default=CONFIG['global']['releases_per_day'],
        help='Batches released per working day. Omit for the continuous default, where a '
             'batch starts when the previous one finished. With a value, an EMPTY batch '
             'still consumes its slot.')
    parser.add_argument(
        '--cut-at-day-end', action='store_true',
        default=CONFIG['global']['cut_at_day_end'],
        help='Stop pickers at the end of the working day and roll the work they did not '
             'reach into the next batch. Changes which units are picked in which batch.')
    parser.add_argument(
        '--roll-over-unpicked', action='store_true',
        default=CONFIG['global']['roll_over_unpicked'],
        help='Demand a batch did not pick joins the next batch, whatever the cause. The '
             'largest behaviour change here: it ends comparability with the archive.')
    # ── the receiving crew ──────────────────────────────────────────────────────
    # Its day is deliberately NOT gated on --cut-at-day-end.  That flag changes which units
    # are PICKED in which batch; coupling would make receiving rollover observable only in a
    # configuration that also perturbs picking, which is the first objection a reviewer
    # raises.  Decoupled, `--recv-crew-size 2 --recv-day-seconds 14400` is a clean arm: real
    # rollover, zero change to pick or put-away timing.
    parser.add_argument(
        '--recv-crew-size', type=_nonneg_int, default=CONFIG['global']['recv_crew_size'],
        metavar='N',
        help='Receivers on the inbound dock. 0 (the default) means NO receiving crew: '
             'merchandise reaches a put queue the instant its lead time elapses, as it '
             'always did. Above 0, arrivals land on a dock and this many people unload it. '
             'Crew SIZE is the only lever on a receiving makespan -- an unload has no '
             'travel term, so there is no speed and no mode to sweep.')
    parser.add_argument(
        '--recv-day-seconds', type=_positive_float,
        default=CONFIG['global']['recv_day_seconds'], metavar='SEC',
        help="The receiving crew's own working day. Omit for no whistle, where the dock "
             'drains every batch and the crew only costs seconds. With a value, what the '
             'crew does not reach stays on the dock for the next batch.')
    parser.add_argument(
        '--recv-day-origin', type=float, default=CONFIG['global']['recv_day_origin'],
        metavar='SEC',
        help="When the receiving day starts on the run's absolute axis. A dock that opens "
             'before the pickers do is a real shift pattern; this is where it goes.')
    # ── the SPLIT put-away configuration ────────────────────────────────────────
    # Three streams instead of one catch-all queue.  Two warnings belong on the flags
    # themselves because both are silent: each queue gets its OWN crew, so three queues of
    # size 1 is 3x the putters and roughly 3x the throughput; and without a staging limit
    # the floor is unbounded, nothing is ever refused, and none of the backpressure
    # machinery executes at all.
    parser.add_argument(
        '--put-queue-split', action='store_true',
        default=CONFIG['global']['put_queue_split'],
        help='Split put-away into three streams — singletons into carts, pallets onto a '
             'forklift, fulfillment into its own bins — instead of one catch-all queue. '
             'Each stream gets its own crew and its own clock, so size the crews below '
             'against the single-queue total or you are measuring headcount.')
    for _flag, _key, _what in (
            ('--put-cart-crew', 'put_cart_crew', 'putters walking singletons into carts'),
            ('--put-pallet-crew', 'put_pallet_crew', 'forklift drivers moving pallets'),
            ('--put-ff-crew', 'put_ff_crew', 'putters on the fulfillment stream')):
        parser.add_argument(_flag, type=_positive_int, default=CONFIG['global'][_key],
                            metavar='N', help=f'{_what.capitalize()} (split only).')
    for _flag, _key, _what in (
            ('--put-cart-staging', 'put_cart_staging', 'the singleton floor'),
            ('--put-pallet-staging', 'put_pallet_staging', 'the pallet floor'),
            ('--put-ff-staging', 'put_ff_staging', 'the fulfillment floor')):
        parser.add_argument(_flag, type=_positive_int, default=CONFIG['global'][_key],
                            metavar='N',
                            help=f'Items {_what} holds at once; omit for unbounded. '
                                 f'Setting it is what turns backpressure on at all — with '
                                 f'no limit nothing is ever refused.')
    parser.add_argument(
        '--put-swap-coef', type=_nonneg_float, default=CONFIG['global']['put_swap_coef'],
        metavar='SEC',
        help='Seconds to swap a full put-away cart for an empty one. 0 (the default) '
             'leaves swaps counted and free, which is what the single queue does today.')
    # ── the inbound trailer pipeline + the standing yard ────────────────────────
    # Seams 3 and 4 for the whole family, deferred by every knob this effort added ("the
    # first sweep"); the funnel IS the first sweep, so the debt falls due together.  Every
    # flag defaults FROM CONFIG (the --keyframe-interval precedent), which is what lets the
    # override loop below assign unconditionally without a flag-less run drifting.
    #
    # Deliberately NOT `choices=` for the four policy names: the registries fill at import of
    # `Inbound.gain`, so a choices list here would either import the sim to print --help or
    # freeze a stale set.  An unknown policy is refused by the registry, loudly, at spec build.
    parser.add_argument(
        '--inbound-trailer-type', choices=('53', '28'),
        default=CONFIG['global']['inbound_trailer_type'],
        help='Trailer type merchandise arrives on: 53 (26 pallet positions) or 28 (12). '
             'Omit for NO trailers — the whole family is structurally off and the manager '
             'keeps its batch lead queue byte-identically. Naming a type is a RESULTS ERA.')
    parser.add_argument(
        '--inbound-dock-doors', type=_positive_int,
        default=CONFIG['global']['inbound_dock_doors'], metavar='N',
        help='Staging slots at the dock. Bookkeeping without --inbound-standing-yard '
             '(every arrival lands at the batch epoch); with it, doors are REAL — at most '
             'N trailers staged, each holding its door until it is empty.')
    parser.add_argument(
        '--inbound-lead-minutes', type=_nonneg_float,
        default=CONFIG['global']['inbound_lead_minutes'], metavar='MIN',
        help='MEDIAN per-trailer transit delay, in minutes (converted to seconds once, at '
             'the spec seam). 0 = arrives instantly.')
    parser.add_argument(
        '--inbound-lead-spread', type=_nonneg_float,
        default=CONFIG['global']['inbound_lead_spread'], metavar='SIGMA',
        help='Sigma of the lognormal around that median (dimensionless): lead_i = median * '
             'exp(sigma * Z_i), one stateless draw per trailer keyed by the WORLD seed, so '
             'trailer #N draws the same lead in every arm. 0 constructs no RNG at all. '
             'Above 0 REQUIRES --inbound-standing-yard and a non-zero median.')
    parser.add_argument(
        '--inbound-global-policy', default=CONFIG['global']['inbound_global_policy'],
        metavar='NAME',
        help='v1 trailer order at BOTH dock moments (ignored once the standing yard splits '
             'it into the yard and dock policies below).')
    parser.add_argument(
        '--inbound-local-policy', default=CONFIG['global']['inbound_local_policy'],
        metavar='NAME', help='Load-pallet order WITHIN a trailer.')
    parser.add_argument(
        '--inbound-trailer-bound', type=_positive_int,
        default=CONFIG['global']['inbound_trailer_bound'], metavar='N',
        help="The dock's k_cap analog, in TRAILERS; omit for unbounded (inert under fifo).")
    parser.add_argument(
        '--inbound-standing-yard', action='store_true',
        default=CONFIG['global']['inbound_standing_yard'],
        help='Trailers STAND in the yard until a door frees, instead of the v1 '
             'drain-everything release. Requires a trailer type AND a receiving crew — '
             'either missing fails loudly, never silently inert.')
    parser.add_argument(
        '--inbound-crew-allocation', choices=('split', 'merged'),
        default=CONFIG['global']['inbound_crew_allocation'],
        help="How receivers meet staged trailers: 'split' = door teams (the standing "
             "physics, doors freeing staggered); 'merged' = v1's pooled gang, kept as "
             'honest physics and the lockstep bridge. A mechanics MODE, not a policy.')
    parser.add_argument(
        '--inbound-yard-policy', default=CONFIG['global']['inbound_yard_policy'],
        metavar='NAME',
        help='Freed door <- which STANDING trailer (the yard-priority registry).')
    parser.add_argument(
        '--inbound-dock-policy', default=CONFIG['global']['inbound_dock_policy'],
        metavar='NAME',
        help='Crew <- which STAGED trailer (the dock-priority registry). Under door teams '
             'this is a worker-allocation preference.')
    parser.add_argument(
        '--inbound-fee-threshold-days', type=_nonneg_float,
        default=CONFIG['global']['inbound_fee_threshold_days'], metavar='DAYS',
        help='Free yard days before a trailer accrues overage. ONE knob, TWO readers — the '
             "urgency gate and the fee report — so they can never disagree about 'overdue'. "
             'Spans are stored raw, so the fee axis is re-reportable under a different '
             'threshold without re-simulating.')
    parser.add_argument(
        '--inbound-urgency-horizon-days', type=_nonneg_float,
        default=CONFIG['global']['inbound_urgency_horizon_days'], metavar='DAYS',
        help="gain_gated's only dial: trailers within this many days of crossing the "
             'threshold are served FIFO ahead of the plan. 0 ~ pure gain; >= the threshold '
             '= pure FIFO. Hours and days never blend into one score — the gate is the '
             'only place they meet.')
    parser.add_argument(
        '--inbound-futuresight-batches', type=_futuresight_window, metavar='W',
        default=CONFIG['global']['inbound_futuresight_batches'],
        help="The futuresight arm's window, in SCRIPT BATCHES ahead of the one being "
             "released; 'all' is the oracle (w=inf). The arm REQUIRES it and requires the "
             'precomputed batch script. A declared-UNLAWFUL upper-bound reference, never '
             'in the recommendable set.')
    for _flag, _key, _what in (
            ('--inbound-unload-intercept', 'inbound_unload_intercept', 'fixed seconds per unload'),
            ('--inbound-unload-weight-coef', 'inbound_unload_weight_coef', 'seconds per pound'),
            ('--inbound-unload-volume-coef', 'inbound_unload_volume_coef',
             'seconds per cubic inch')):
        parser.add_argument(_flag, type=float, default=CONFIG['global'][_key], metavar='SEC',
                            help=f'Unload cost: {_what}. Omit to take the put-away value BY '
                                 f'REFERENCE, which is what every existing run did — so the '
                                 f'default splits no results era.')
    parser.add_argument('--sampler', choices=('v1', 'v2'),
                        default=CONFIG['global']['sampler'],
                        help='Batch-sampler VERSION — a results era, not a tuning knob. '
                             f'Default {CONFIG["global"]["sampler"]!r} (the Fenwick sampler, '
                             'adopted 2026-08-20). v1 reproduces the pre-2026-08-20 archive '
                             'byte-identically; the two eras\' batch caches never mix '
                             '(fingerprinted apart).')
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
    parser.add_argument('--no-preflight', action='store_true',
                        help='Skip the run-tree schema preflight. The preflight is a no-op unless a '
                             'shape-defining source changed since the recorded fingerprint; when one '
                             'did, it proves the tree shape with two tiny canary runs and adopts the '
                             'resulting content-addressed schema id '
                             '(Optimization/schemas/run_tree/<short>.json) so downstream tools can '
                             'resolve the layout without hand-written extraction code.')
    parser.add_argument('--analysis-workers', type=int, default=None, metavar='N',
                        help='Pool size for the post-sim analysis pass (default: same as --workers).')
    parser.add_argument('--max-retries', type=int, default=2, metavar='N',
                        help='On a hard worker death (segfault/OOM) that breaks the pool, rebuild '
                             'the pool and resubmit the unfinished units up to N times before '
                             'quarantining them (default 2). Ordinary per-unit errors are not retried.')
    parser.add_argument('--checkpoint-frac', type=float, default=None, metavar='F',
                        help='Per-strategy checkpoint cadence as a FRACTION of --n-batches '
                             '(default: CONFIG, 0.1). The interval is max(1, int(n_batches*F)), '
                             'so 0.5 on a 10-batch run checkpoints every 5 batches. Had no CLI '
                             'until 2026-08-15: a short run silently checkpointed every batch.')
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
    from Optimization.config.whatif_config import get_spec, SPECS

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
    # `is not None`, not truthiness: `--n-batches 0` used to be silently discarded here and
    # the run proceeded on CONFIG's value.  It is rejected at parse time now (_positive_int).
    if args.n_batches is not None:
        g['n_batches'] = args.n_batches
    # Read at call time by sim_config.seed_world()/seed_batches(), so this reaches the
    # warehouse build and every worker's batch stream.
    if args.seed_world is not None:
        g['seed_world'] = args.seed_world
    if args.seed_batches is not None:
        g['seed_batches'] = args.seed_batches
    if args.max_skus is not None:
        g['max_skus'] = args.max_skus
    g['workers']           = args.workers or 1
    g['keyframe_interval'] = args.keyframe_interval
    g['sampler']           = args.sampler
    g['work_day_seconds']  = args.work_day_seconds
    g['releases_per_day']  = args.releases_per_day
    g['cut_at_day_end']    = bool(args.cut_at_day_end)
    g['roll_over_unpicked'] = bool(args.roll_over_unpicked)
    g['recv_crew_size']    = args.recv_crew_size
    g['recv_day_seconds']  = args.recv_day_seconds
    g['recv_day_origin']   = args.recv_day_origin
    g['put_queue_split']    = bool(args.put_queue_split)
    g['put_cart_crew']      = args.put_cart_crew
    g['put_pallet_crew']    = args.put_pallet_crew
    g['put_ff_crew']        = args.put_ff_crew
    g['put_cart_staging']   = args.put_cart_staging
    g['put_pallet_staging'] = args.put_pallet_staging
    g['put_ff_staging']     = args.put_ff_staging
    g['put_swap_coef']      = args.put_swap_coef
    # The inbound family, unconditionally: every flag defaults FROM CONFIG, so a flag-less run
    # writes back exactly what was already there.  Assigning the whole list (rather than
    # `if not None`) is what lets a cell's inbound record and a CLI value share one mechanism —
    # both are just writes into CONFIG['global'], read at call time by `inbound_spec()`.
    for _k in INBOUND_KEYS:
        g[_k] = getattr(args, _k)
    if args.checkpoint_frac is not None:
        g['checkpoint_frac'] = args.checkpoint_frac
    # Fill is per-CHANNEL and read at call time (sim_config.store_fill/ff_fill), so mutating
    # CONFIG here reaches every consumer in this process — including the warehouse-DB
    # provenance write, which an import-time snapshot used to miss.
    if args.store_fill is not None:
        CONFIG['channels']['store']['fill'] = args.store_fill
    if args.ff_fill is not None:
        CONFIG['channels']['fulfillment']['fill'] = args.ff_fill

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

    if args.max_tasks_per_child != 1:
        log.warning(f'  --max-tasks-per-child {args.max_tasks_per_child} ignored: worker '
                    f'recycling is pinned at 1. A larger value deadlocked the pool at a cell '
                    f'boundary (18 workers, zero CPU, no error); the spawn saving it buys is '
                    f'~10 s per multi-minute job. See supervisor._supervise.')

    # Structural-floor check, HERE rather than mid-build: the store's bucket set is the
    # handling x category x tier cross-product, so its one-aisle-per-bucket floor is fixed by
    # configuration and no SKU cap lowers it.  `_apply_caps` warns and proceeds at the floor —
    # correct, but it did so ~15 s into EVERY pair, after the inventory load.  Saying it once,
    # up front, costs microseconds and lets the operator retype the flag before anything runs.
    if args.s_max_bins is not None:
        _floor_aisles, _floor_bins = structural_bin_floor(_HANDLINGS, _CATEGORIES,
                                                          _AISLE_W, _AISLE_H)
        if args.s_max_bins < _floor_bins:
            log.warning(
                f'  --s-max-bins {args.s_max_bins:,} is BELOW the store structural floor of '
                f'{_floor_bins:,} bins ({_floor_aisles} aisles: one per '
                f'{len(_HANDLINGS)}x{len(_CATEGORIES)}x5 bucket so every SKU is placeable). '
                f'The cap will NOT be honored and the store will size to the floor. This is '
                f'a property of the handling/category configuration, not of --max-skus.')
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
            # A resume MUST rebuild the same world and redraw the same demand.
            'seed_world'   : g['seed_world'], 'seed_batches': g['seed_batches'],
            's_max_aisles' : args.s_max_aisles, 's_max_bins' : args.s_max_bins, 's_min_bins': args.s_min_bins,
            'ff_max_aisles': args.ff_max_aisles, 'ff_max_bins': args.ff_max_bins, 'ff_min_bins': args.ff_min_bins,
            's_composition': _store_comp,
            # Read back by run_analysis so a standalone re-analysis sizes the warehouse the way
            # the RUN did, not the way this checkout's CONFIG happens to be set.
            'store_fill'   : CONFIG['channels']['store']['fill'],
            'ff_fill'      : CONFIG['channels']['fulfillment']['fill'],
            'checkpoint_frac': g['checkpoint_frac'],
            'sampler'      : g['sampler'],
            # The working day: two runs with different days are otherwise
            # indistinguishable after the fact, and a resume would finish an arm on a
            # different clock than it started on.
            'work_day_seconds': g['work_day_seconds'],
            'releases_per_day': g['releases_per_day'],
            'cut_at_day_end'  : g['cut_at_day_end'],
            'roll_over_unpicked': g['roll_over_unpicked'],
            # The receiving crew and its own day. Read from `g` (post-overlay), not from
            # `args`, so a value that came from CONFIG rather than the command line is
            # recorded too -- otherwise two runs with different docks look identical.
            'recv_crew_size'  : g['recv_crew_size'],
            'recv_day_seconds': g['recv_day_seconds'],
            'recv_day_origin' : g['recv_day_origin'],
            # The split put-away configuration. Read from `g` (post-overlay) so a value
            # that came from CONFIG rather than the command line is recorded too.
            'put_queue_split'   : g['put_queue_split'],
            'put_cart_crew'     : g['put_cart_crew'],
            'put_pallet_crew'   : g['put_pallet_crew'],
            'put_ff_crew'       : g['put_ff_crew'],
            'put_cart_staging'  : g['put_cart_staging'],
            'put_pallet_staging': g['put_pallet_staging'],
            'put_ff_staging'    : g['put_ff_staging'],
            'put_swap_coef'     : g['put_swap_coef'],
            # The inbound family. Read from `g` (post-overlay) like the two families above,
            # and recorded WHOLE rather than only when on: a phase-2 cell that cannot say
            # which lead shape and which fee threshold it ran under is not re-analysable, and
            # the yard's derive-late fee report reads the threshold off this record (its
            # HEAD-default fallback exists for runs that predate recording — the campaign must
            # never exercise it).
            **{k: g[k] for k in INBOUND_KEYS},
            # The lead draw's DOMAIN TAG, recorded beside the shape it keys. Un-re-derivable
            # by construction (a literal chosen to be stable), so a comparison spanning a TAG
            # change would silently span two different arrival schedules with nothing to
            # detect it from -- the same argument that puts `sampler` in this file.
            'inbound_lead_tag'  : _LEAD_TAG,
            'keyframe_interval': args.keyframe_interval, 'whatif': args.whatif, 'spec': spec_name,
            'profiles_dir' : args.profiles_dir, 'all_profiles': args.all_profiles,
            'workers'      : args.workers, 'max_tasks_per_child': args.max_tasks_per_child,
            'max_retries'  : args.max_retries, 'resume_granularity': args.resume_granularity,
            'pairs'        : [list(p) for p in pairs],
            # WHICH catalogue version each pinned pair is (None = pre-contract catalogue).
            # `pairs` above answers WHERE and drives resume; this answers WHICH, so a catalogue
            # regenerated in place after this run is detectable from the recorded digests.
            'pair_bindings': _pair_bindings(pairs),
        })
        log.info('  Wrote run_spec.json — zero-param `--resume` enabled')

    # ── run-tree schema preflight — BEFORE the descriptor is stamped, so the contract this run
    # declares is one that has actually been PROVEN against real output.  Free unless a
    # shape-defining source changed (it compares source fingerprints first); on a change it runs two
    # tiny canary sims and, if the tree really moved, bumps the version + updates the downstream
    # orchestrator files.  Skipping is explicit (--no-preflight).
    if not args.no_preflight:
        from Optimization.runschema import preflight as _preflight
        _pf = _preflight.ensure(echo=log.info)
        if _pf != 0:
            # Nothing has simulated yet; drop the stub run dir (log + spec only) so an aborted
            # preflight doesn't leave a phantom run behind for the next person to puzzle over.
            _stub = not any(os.path.isdir(os.path.join(base_dir, d)) for d in os.listdir(base_dir))
            log.error(f'Schema preflight stopped the run (exit {_pf}).')
            logging.shutdown()
            if _stub and not args.resume:
                shutil.rmtree(base_dir, ignore_errors=True)
            sys.exit(f'Schema preflight stopped the run (exit {_pf}). Resolve the run-tree contract '
                     f'above, or re-run with --no-preflight to proceed anyway.')

        # ── DB-shape precheck — the same bargain for the OTHER contract.  Every family this run
        # can write is already registered (registration happens at import, and this process
        # imports every writer), so the sweep below is "what this run writes" by construction.
        # Cost: a few os.listdir + in-memory DDL builds.  Blocking HERE is what lets the workers
        # stay warn-once: a store gap stops the run before hour 0, never at hour N.
        from Schema import compat as _schema_compat
        from Schema import identity as _schema_identity
        _db_problems = [p for name in _schema_identity.families()
                        for p in _schema_compat.verify_family_store(name)]
        if _db_problems:
            for _p in _db_problems:
                log.error(f'  DB-shape precheck: {_p}')
            _stub = not any(os.path.isdir(os.path.join(base_dir, d)) for d in os.listdir(base_dir))
            logging.shutdown()
            if _stub and not args.resume:
                shutil.rmtree(base_dir, ignore_errors=True)
            sys.exit('DB-shape precheck stopped the run: a schema this run would write is not on '
                     'the committed record (details above). Run the named command(s), or re-run '
                     'with --no-preflight to proceed anyway.')

    # run_layout.json — the unified cell-tree descriptor (cells/reference/configs/pairs), so tools
    # INFER the tree instead of directory-guessing.  Written for a new run; a resumed LEGACY run
    # (no descriptor) gains one so it stays analyzable.  cell_tuples/reference match the driver's.
    cell_tuples = _build_cells(spec_dict)
    # Same predicate the driver uses, from the same function — these two MUST agree: one
    # picks the baseline cell, the other writes its name into the descriptor for every
    # downstream what-if to diff against.
    reference   = reference_cell(cell_tuples, spec_dict.get('reference'))
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
                              max_retries=args.max_retries, resume_granularity=args.resume_granularity,
                              max_tasks_per_child=args.max_tasks_per_child)

    # ── one command: run the analysis in-process right after the sim (unless --no-analyze) ──
    if not args.no_analyze:
        from Optimization.analyze_run import analyze_run
        _aw = args.analysis_workers or workers
        # granularity 'config' emits ~4 jobs/cell — on an 18-worker pool the analysis
        # stage ran 3.3 min with 14 workers idle (measured, comparison_20260820_151204,
        # ~32% of that run's wall).  'graph' (one job per channel-run × evaluation,
        # ~88/cell) is the documented pool-saturating mode; keep 'config' when the pool
        # is a single worker, where finer jobs are pure dispatch overhead.
        analyze_run(base_dir, log, cells=info['cells'],
                    workers=_aw, reference=info['reference'],
                    granularity=('graph' if _aw > 1 else 'config'))

    log.info(f'\nAll simulations complete.  Root: {base_dir}'
             + ('' if not args.no_analyze else
                f'\n  Analyze with: python -m Optimization.analyze_run {base_dir}'))


if __name__ == '__main__':
    main()
