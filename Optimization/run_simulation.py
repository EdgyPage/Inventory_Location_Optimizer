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
    CONFIG, INBOUND_KEYS, STAFFING_KEYS, SPEC_KNOB_NAMES,
    apply_cli_overrides as _apply_cli_overrides, run_spec_record as _run_spec_record,
    REGRESSION_CONFIGS, STORE_CONFIGS, FULFILLMENT_CONFIGS,
    seed_world, seed_batches, n_batches, k_pickers, channel_pickers, staffing_spec,
    staffing_provenance, CALIBRATION_KEYS, ERA_ONLY_KEYS, FLAG_OFF_ONLY_KEYS, era_on,
    couple_channels,
    store_restocks, store_fill,
    _OUTPUT_DIR, _DEFAULT_PROFILES_DIR, _CATEGORIES, _HANDLINGS, aisle_geometry,
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


def _unit_fraction(text: str) -> float:
    """An argparse `type=` for a utilization target: a number in (0, 1].

    ρ is worked ÷ granted, so 0 means "no work is ever done" and above 1 means "the crew
    works more than the day it is granted" -- both are not a scenario but a typo, and the
    derivation would divide by the first.  Fails at the parser, naming the flag.
    """
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not a number')
    if not (0.0 < v <= 1.0):
        raise argparse.ArgumentTypeError(
            f'{v} is not a utilization target: worked / granted lies in (0, 1]')
    return v


def _free_share(text: str) -> float:
    """An argparse `type=` for a free share of a bin bucket: a number in [0, 1).

    Zero is a real setting ("trust the fragmentation chain, no floor"); one is a bucket
    sized for nothing, and the derivation would divide by `1 - 1`.  Fails at the parser,
    naming the flag, rather than as a bare ValueError at run-spec build after the run dir
    exists (`sim_config.min_headroom`).
    """
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{text!r} is not a number')
    if not (0.0 <= v < 1.0):
        raise argparse.ArgumentTypeError(
            f'{v} is not a free share: the minimum headroom lies in [0, 1)')
    return v


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
    # An ERA run recorded before the derived fill ("Derive the fill headroom from the
    # fragmentation") carries a NUMERIC `store_fill` (the typed 0.85 it was sized at) and no
    # `min_headroom`; resumed here, the parser default would take the derived branch and
    # re-size a warehouse its completed arms never ran on.  Nothing on this checkout can size
    # at a typed fill under the era, so the resume is refused rather than re-planned.
    if spec.get('shift_drain_or_cap') and spec.get('store_fill') is not None:
        raise SystemExit(
            f"this run was sized at a typed fill ({spec.get('store_fill')}) under "
            f'--shift-drain-or-cap, which predates the derived fill headroom; the era now '
            f'sizes every bucket from the stationary fragmentation and cannot rebuild the '
            f'warehouse those arms ran on. Start a new run instead of resuming this one.')
    # The staffing record is NESTED (`staffing.inputs.<key>`); flatten its inputs beside the
    # flat keys so one loop restores every family.  A pre-record spec has no `staffing`, so
    # its keys are simply absent and the flags' CONFIG defaults stand -- never a KeyError.
    _staffing_inputs = (spec.get('staffing') or {}).get('inputs') or {}
    spec = {**spec, **{k: _staffing_inputs[k] for k in STAFFING_KEYS if k in _staffing_inputs}}
    # EVERY knob the recorder wrote, plus the CLI-only names that are not CONFIG values.
    #
    # `SPEC_KNOB_NAMES` is derived from the same `spec_from` flag `run_spec_record` reads, so
    # a knob that is recorded and then never restored is not expressible.  That defect shipped
    # once -- `put_swap_coef` lost its restore and all 1,534 tests stayed green -- and the AST
    # guard in Tests/unit/test_run_shaping_params.py was written for it; deriving both ends
    # from one declaration makes the guard a tautology, which is the right outcome.
    #
    # Why each family is restored at all, kept because the reasons are not obvious: a resume
    # MUST rebuild the same warehouse (the sizing params), regenerate the same batch sequence
    # (the sampler and the seeds), finish on the clock it started on (the working day), and
    # finish under the regime it started in -- the era, the site dock, the receiving crew and
    # its day, the put-away split and its three crews, the other crews' price scalars, the
    # declared picker counts, and the whole inbound family.  An arm that resumed without its
    # yard would finish on v1's drain-everything dock; one that resumed without its lead shape
    # would redraw a different arrival schedule.
    #
    # The staffing keys arrive flattened from `staffing.inputs` above, so one loop restores
    # every family.
    for f in (*SPEC_KNOB_NAMES,
              # CLI-only run shaping -- none of these is a CONFIG value, so none is a knob.
              's_max_aisles', 's_max_bins', 's_min_bins',
              'ff_max_aisles', 'ff_max_bins', 'ff_min_bins',
              'whatif', 'spec', 'profiles_dir', 'all_profiles', 'profile_run',
              'max_tasks_per_child', 'max_retries', 'resume_granularity',
              # Per-CHANNEL rather than global, and skipped when None (the era derives the
              # fill headroom from the fragmentation instead).
              'store_fill', 'ff_fill',
              # Spliced rather than retyped: flattened from `staffing.inputs` above.
              *STAFFING_KEYS):
        if f not in spec:
            continue
        if f in explicit:
            if getattr(args, f) != spec[f]:
                notes.append(f'  run_spec override: {f} {spec[f]!r} -> {getattr(args, f)!r} (explicit flag wins)')
        else:
            setattr(args, f, spec[f])
    return spec.get('s_composition'), notes


#: The crew flags the calibrated era DERIVES.  Typed explicitly under the era, each is an
#: error ("Design the staffing record", decision 4): the derivation sizes these crews from
#: the pickers' daily demand, and a declared size beside a derived one is the stale-literal
#: trap the era exists to kill.  Flag-off, every one of them keeps working verbatim.
_ERA_DERIVED_FLAGS: tuple[str, ...] = (
    'recv_crew_size', 'recv_day_seconds', 'recv_day_origin',
    'put_crew_size', 'put_cart_crew', 'put_pallet_crew', 'put_ff_crew',
)

#: The two typed fills: flag-off only.  Under the era the planner sizes every bucket to a
#: DERIVED hold (the declaration plus its stationary fragmentation, floored at
#: `--min-headroom`), and the run spec records both as None ("Derive the fill headroom from
#: the fragmentation").  Not on `sim_config.FLAG_OFF_ONLY_KEYS` because they are channel
#: keys (`CONFIG['channels'][<ch>]['fill']`), not staffing keys.
_ERA_DERIVED_FILL_FLAGS: tuple[str, ...] = ('store_fill', 'ff_fill')


def _apply_run_defaults(args, spec_dict: dict, explicit: set) -> list[str]:
    """Overlay a cell-matrix spec's `run_defaults` onto args, for a NEW run.

    A spec may carry run-level knobs (whatif_config.ERA_RUN_DEFAULTS: the calibrated era)
    beside its cell axes.  They are DEFAULTS: a flag the user typed wins, with a note, so a
    spec cannot silently override a command line.  A resumed run never reaches this -- its
    run spec recorded the resolved values and `_apply_run_spec` restores them.
    Returns the notes.
    """
    notes = []
    for key, val in (spec_dict.get('run_defaults') or {}).items():
        # A misspelt key would land on the Namespace and reach nothing: the CONFIG
        # write-back iterates the key LISTS, not vars(args), so the run would carry the
        # parser default under a spec that says otherwise -- the same silent hazard
        # `cells._inbound_axis` refuses for the cell axis.
        if not hasattr(args, key):
            raise ValueError(f'run_defaults names {key!r}, which is not a parser flag')
        if key in explicit:
            if getattr(args, key) != val:
                notes.append(f'  spec default: {key} {val!r} -> {getattr(args, key)!r} '
                             f'(explicit flag wins)')
            continue
        setattr(args, key, val)
    return notes


def _check_era_flags(args, explicit: set) -> list[str]:
    """Enforce and complete the calibrated era's regime on the parsed args.

    Under `--shift-drain-or-cap` (".scratch/department-calibration", "Define the calibrated
    era"):
      * the legacy crew flags (`_ERA_DERIVED_FLAGS`) typed explicitly are an ERROR -- the
        put and receiving crews are derived from the pickers, never declared;
      * `--put-queue-split` is an error -- single queue only for this map; per-stream sizing
        is the out-of-scope swept-axis effort;
      * a nonzero `--put-swap-coef` is an error -- the put crew is sized by a closed form
        with no cart-swap term (`staffing.implied_reorders`), and the single queue the era
        runs never reads the coefficient anyway, so accepting it would record a knob the
        run ignores and the derivation cannot price;
      * the cadence is pinned at ONE release per day: an unset `--releases-per-day` is
        completed to 1 (with a note), an explicit other value is an error, because the
        derivation makes one batch one day's demand;
      * the roll-over and the cut are completed to on (with a note): a day that reads
        "drained" because cut demand was dropped is not equilibrium, and the worker forces
        the cut anyway -- the record should say what ran.
    Raises `SystemExit` with the reason; returns the completion notes otherwise.
    Module-level so a test can hand it a Namespace.
    """
    if not getattr(args, 'shift_drain_or_cap', False):
        # The era-only inputs (ADR-0004) typed WITHOUT the era: nothing reads them
        # flag-off -- the script's content is the channel's batch mean and the crew is
        # declared -- so accepting one would record a declaration the run ignores.
        bad = [f for f in ERA_ONLY_KEYS if f in explicit]
        if bad:
            raise SystemExit(
                f'{", ".join("--" + f.replace("_", "-") for f in bad)} declare the calibrated '
                f"era's demand, first-time confidence and minimum headroom and are read only "
                f'under --shift-drain-or-cap; flag-off the crew is declared (--store-pickers / '
                f'--ff-pickers), the batch content is the channel default and the fill is '
                f'typed (--store-fill / --ff-fill). Add --shift-drain-or-cap, or drop them.')
        return []
    bad = [f for f in _ERA_DERIVED_FLAGS if f in explicit]
    if bad:
        raise SystemExit(
            f'under --shift-drain-or-cap the put and receiving crews are DERIVED from the '
            f'script (Optimization/simconfig/staffing.py), so these flags are an error: '
            f'{", ".join("--" + f.replace("_", "-") for f in bad)}. Drop them, or drop '
            f'--shift-drain-or-cap to run the flag-off regime with declared crews.')
    # ADR-0004: demand is the declared input and the picking crew is SOLVED from the
    # first-time confidence, so a typed crew or a typed picking utilization target under
    # the era is a regime nobody derived -- refused exactly as the legacy crew flags are.
    bad = [f for f in FLAG_OFF_ONLY_KEYS if f in explicit]
    if bad:
        raise SystemExit(
            f'under --shift-drain-or-cap the picking crew is DERIVED from --store-demand / '
            f'--ff-demand and --first-time-confidence (ADR-0004), so these flags are an '
            f'error: {", ".join("--" + f.replace("_", "-") for f in bad)}. Declare the '
            f'demand instead, or drop --shift-drain-or-cap to run the flag-off regime with '
            f'a declared crew.')
    # "Derive the fill headroom from the fragmentation": the fill each bucket is sized to
    # is DERIVED per bucket from the stationary fragmentation and floored at
    # --min-headroom, so a typed fill under the era is a headroom nobody derived --
    # refused exactly as the picker flags are.  These are channel keys, not staffing
    # keys, so they are named here rather than on FLAG_OFF_ONLY_KEYS.
    bad = [f for f in _ERA_DERIVED_FILL_FLAGS if f in explicit]
    if bad:
        raise SystemExit(
            f'under --shift-drain-or-cap the fill headroom is DERIVED per bin bucket from the '
            f'stationary fragmentation, floored at --min-headroom, so these flags are an '
            f'error: {", ".join("--" + f.replace("_", "-") for f in bad)}. Declare the '
            f'minimum headroom instead, or drop --shift-drain-or-cap to size at a typed fill.')
    if getattr(args, 'put_queue_split', False):
        raise SystemExit(
            'under --shift-drain-or-cap the put crew is one derived site crew on a single '
            'queue; --put-queue-split is an error here (per-stream sizing is the '
            'staffing-as-a-swept-axis effort, out of this map\'s scope).')
    if float(getattr(args, 'put_swap_coef', 0.0) or 0.0) > 0.0:
        raise SystemExit(
            f'under --shift-drain-or-cap the put crew is sized by a closed form with no '
            f'cart-swap term (Optimization/simconfig/staffing.py implied_reorders), and the '
            f'single put queue the era runs never reads the coefficient; --put-swap-coef '
            f'{args.put_swap_coef} would be recorded and ignored. Drop it, or drop '
            f'--shift-drain-or-cap.')
    notes = []
    rpd = getattr(args, 'releases_per_day', None)
    if rpd is None:
        args.releases_per_day = 1
        notes.append('  era: --releases-per-day completed to 1 (one batch is one day\'s demand)')
    elif int(rpd) != 1:
        raise SystemExit(
            f'the calibrated era derives one batch per day, so --releases-per-day must be 1 '
            f'under --shift-drain-or-cap; got {rpd}.')
    if not getattr(args, 'roll_over_unpicked', False):
        args.roll_over_unpicked = True
        notes.append('  era: --roll-over-unpicked completed to on (a day that drops cut demand '
                     'is not equilibrium)')
    if not getattr(args, 'cut_at_day_end', False):
        args.cut_at_day_end = True
        notes.append('  era: --cut-at-day-end completed to on (the cap implies the cut; the '
                     'record now says so)')
    return notes


def _build_parser() -> argparse.ArgumentParser:
    """Every flag `run_simulation` accepts, and nothing else.

    Split out of `main`, which was 875 lines holding four unrelated things: this parser, the
    CONFIG override loop, the two contract prechecks, and the run.  Only the last three have
    anything to do with each other.

    DEFAULTS COME FROM `CONFIG`, NEVER FROM A LITERAL.  That is the invariant the whole
    five-seam chain rests on -- a flag whose default were written out here would disagree
    with `settings.py` the moment the setting moved, and the run would be shaped by
    whichever of the two the reader happened to trust.  `Tests/unit/test_cli_surface.py`
    checks the pairs against the live CONFIG rather than against this file's text.

    Reads CONFIG; never writes it.  A parser can therefore be built by a test without
    reshaping the run that follows.
    """
    parser = argparse.ArgumentParser(
        description='Warehouse assignment comparison — uses the newest generated inventory+affinity pair.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--profiles-dir', default=_DEFAULT_PROFILES_DIR,
                        help='Root directory produced by generate_profile_suite.py')
    parser.add_argument('--all-profiles', action='store_true',
                        help='Run every profile pair instead of only the newest')
    parser.add_argument('--profile-run', default=None, metavar='NAME',
                        help='Bind the NAMED profile run under --profiles-dir instead of '
                             'the newest. The default binds whichever was generated last, '
                             'which is not necessarily the one big enough for the run you '
                             'asked for: --max-skus above the catalogue is not an error, '
                             'it silently takes everything. Lists the available names when '
                             'given one that does not exist.')
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
    # Same rule as --keyframe-interval above: defaulted FROM CONFIG, because both are
    # assigned unconditionally into `g` below and a literal here would make the declared
    # value dead.  The pair is the reference warehouse's aisle SHAPE, which until now had
    # no flag at all -- so no run's own spec could say what geometry it was built on.
    parser.add_argument('--aisle-columns', type=_positive_int,
                        default=CONFIG['global']['aisle_columns'], metavar='C',
                        help='Bin-width columns per aisle (structural; both channels '
                             'share one aisle shape). Default '
                             f'{CONFIG["global"]["aisle_columns"]}.')
    parser.add_argument('--aisle-levels', type=_positive_int,
                        default=CONFIG['global']['aisle_levels'], metavar='L',
                        help='Bin-height levels per aisle. Fulfillment overrides only the '
                             'HEIGHT (short shelves). Default '
                             f'{CONFIG["global"]["aisle_levels"]}.')
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
    # ── THE CALIBRATED ERA ──────────────────────────────────────────────────────────
    # One flag turns the regime on: one site-wide drain-or-cap shift, one release per day,
    # the cut and the roll-over on (completed by `_check_era_flags`), the put and receiving
    # crews DERIVED from the pickers, the script priced from the expected-travel closed
    # form (simconfig/expected_travel.py).  The
    # campaign specs carry it as `run_defaults` (whatif_config.ERA_RUN_DEFAULTS).
    parser.add_argument(
        '--shift-drain-or-cap', action='store_true',
        default=CONFIG['global']['shift_drain_or_cap'],
        help='THE CALIBRATED ERA. One site-wide working stretch per day that ends when no '
             'work stands or at the cap (the day length), every crew on the same boundary; '
             'implies --releases-per-day 1, --cut-at-day-end and --roll-over-unpicked. '
             'Demand is then DECLARED (--store-demand / --ff-demand) and every crew is '
             'DERIVED: the picking crew and the line floor from --first-time-confidence, '
             'the put and receiving crews from the script and the expected-travel closed '
             'form -- so --store-pickers / --ff-pickers / --rho-pick and the legacy crew '
             'flags are an error. A RESULTS ERA: nothing is comparable across it.')
    # ── the site dock ───────────────────────────────────────────────────────────
    # One flag makes the two channels one SITE.  Declared rather than derived from the
    # inbound flag: the campaign couples its inbound-OFF pole too, so there is no flag a
    # reader could derive this from (.scratch/site-dock, "Re-shape the funnel for arm pairs").
    parser.add_argument(
        '--couple-channels', action='store_true',
        default=CONFIG['global']['couple_channels'],
        help='THE SITE DOCK. Run the store and fulfillment channels as ONE site: a work '
             'unit drives both leaves through one batch loop, so the dock, the receiving '
             'crew and the putters are fielded once rather than once per leaf. Arms pair by '
             'RANK, the diagonal, so the arm count is unchanged. Needs a mixed catalogue. '
             'Stamped into run_layout.json as `coupled`, which run_restock_selection '
             'refuses (phase 1 is uncoupled by definition).')
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
    # ── the single-queue put crew ──────────────────────────────────────────────────
    # The `put_crew_spec` trap, closed: the accessor reads CONFIG, so this flag reaches a
    # worker.  Flag-off only -- under the era the size is derived and typing it is an error.
    parser.add_argument(
        '--put-crew-size', type=_positive_int, default=CONFIG['global']['put_crew_size'],
        metavar='N',
        help='Putters on the single catch-all queue (default 1). Flag-off only: under '
             '--shift-drain-or-cap the put crew is DERIVED and this flag is an error.')
    # ── the other crews' price, as scalars of the pickers' ─────────────────────
    # Put-away and receiving keep picking's coefficients by reference; these three are the
    # only place their numbers may differ (settings, "the other crews' price").
    for _flag, _key, _what in (
            ('--put-intercept-scale', 'put_intercept_scale',
             "put-away intercept as a multiple of picking's ('putting is less work')"),
            ('--put-item-ratio', 'put_item_ratio',
             "put-away per-item charge as a multiple of picking's"),
            ('--recv-intercept-scale', 'recv_intercept_scale',
             "receiving intercept as a multiple of PUT-AWAY's (the per-item charge is "
             "put-away's, charged once per pack)")):
        parser.add_argument(_flag, type=_nonneg_float, default=CONFIG['global'][_key],
                            metavar='X', help=f'{_what[0].upper()}{_what[1:]}.')
    # ── staffing: pickers per channel (flag-off) ────────────────────────────────
    # THE DECLARED HEADCOUNT FLAG-OFF.  Under the calibrated era the picking crew is
    # SOLVED from the declared demand and the first-time confidence (ADR-0004), and typing
    # either of these is an error there, exactly like the legacy crew flags.  Each defaults
    # FROM CONFIG (the --keyframe-interval precedent) so the unconditional write-back below
    # cannot drift a flag-less run; a pick-config module that names its own `num_pickers`
    # must agree with the channel's value or setup raises.  One flag per STAFFING_KEYS
    # entry, in its order -- a key with no flag here is a knob reachable only by editing
    # settings.py, which is exactly the seam this closes.
    for _flag, _key, _what in (
            ('--store-pickers', 'store_pickers',
             'machine order-pickers on the store channel (flag-off only: under '
             '--shift-drain-or-cap the crew is derived and this is an error)'),
            ('--ff-pickers', 'ff_pickers',
             'walkers on the fulfillment channel (flag-off only, as above; a store-only '
             'catalogue ignores it)')):
        parser.add_argument(_flag, type=_positive_int, default=CONFIG['global'][_key],
                            metavar='N', help=f'{_what[0].upper()}{_what[1:]}.')
    # ── the era's DECLARED DEMAND and the first-time confidence (ADR-0004) ──────────
    # THE DECLARED INPUT under --shift-drain-or-cap: demand per channel in the batch
    # sampler's own unit (the fraction of the section's SKUs drawn as lines on a mean
    # day), and the one scalar the line floor and the picking crew are solved from.
    # Era-only: typed without the era they are an error (`_check_era_flags`).  Each
    # defaults FROM CONFIG like every staffing key.
    for _flag, _key, _what in (
            ('--store-demand', 'store_demand',
             "the store channel's demand: the fraction of its SKUs drawn as lines on a "
             'mean day (lines/day = fraction x SKUs); the spread keeps the declared batch cv'),
            ('--ff-demand', 'ff_demand',
             "the fulfillment channel's demand, in the same unit")):
        parser.add_argument(_flag, type=_unit_fraction, default=CONFIG['global'][_key],
                            metavar='FRACTION',
                            help=f'{_what[0].upper()}{_what[1:]} (default '
                                 f'{CONFIG["global"][_key]:g}). Era-only.')
    parser.add_argument(
        '--first-time-confidence', type=_unit_fraction,
        default=CONFIG['global']['first_time_confidence'], metavar='C',
        help='The joint FIRST-TIME confidence: the expected share of picks completed the '
             'first time, reached on their day AND filled from the shelf. Split equally: '
             'the line floor is solved so the first-pass fill clears sqrt(C), the picking '
             'crew is the smallest integer whose expected cut share of units is under '
             '1 - sqrt(C). Replaces --rho-pick under the era (put-away and receiving keep '
             f'theirs). Default {CONFIG["global"]["first_time_confidence"]:g}. Era-only.')
    parser.add_argument(
        '--min-headroom', type=_free_share, default=CONFIG['global']['min_headroom'],
        metavar='H',
        help='The MINIMUM free share every bin bucket keeps at setup. Under the era the fill '
             'a bucket is sized to is DERIVED -- requirement / (requirement + the expected '
             'stationary extra bins the fragmentation chain stamps) -- and never above 1 - H, '
             'covering what the chain does not model (supply jitter, spills, own-bin '
             'top-ups). Replaces --store-fill / --ff-fill under the era, which are an error '
             f'there. Default {CONFIG["global"]["min_headroom"]:g}. Era-only.')
    # ── the era's declared scalars: every step of the derivation is a knob ─────────
    # Each is a staffing INPUT (STAFFING_KEYS), recorded `declared` when typed and
    # `assumed` when the settings default stood.  ρ is a utilization target in (0, 1];
    # f is a replenishment ratio (1.0 = steady state); band_tol an absolute tolerance.
    for _flag, _key, _type, _what in (
            ('--rho-pick', 'rho_pick', _unit_fraction,
             'picking utilization target (worked / granted); capacity = K x day x rho. '
             'Flag-off only: under --shift-drain-or-cap --first-time-confidence replaces '
             'it and this is an error'),
            ('--rho-put', 'rho_put', _unit_fraction, 'put-away utilization target'),
            ('--rho-recv', 'rho_recv', _unit_fraction, 'receiving utilization target'),
            ('--f-put', 'f_put', _nonneg_float,
             'units put away per unit picked; 1.0 = steady state'),
            ('--f-recv', 'f_recv', _nonneg_float,
             'packs received per pack the script implies; 1.0 = steady state'),
            ('--f-repack', 'f_repack', _nonneg_float,
             'packs repacked per pack received (ADR-0003 rework); 0.0 = none expected, '
             'and the equilibrium audit flags any run that measures some'),
            ('--band-tol', 'band_tol', _nonneg_float,
             'equilibrium band: |realized - expected| utilization tolerance, absolute'),
            # Stock coverage in DAYS of each SKU's own demand: under the era setup
            # re-derives every SKU's order-up-to and reorder point from these and sizes the
            # warehouse through a pair-level fixed point (simdriver/era_coverage.py).  These
            # three are NOT era-only: ADR-0002 left the catalogue with no level to fall back
            # on, so the fixed point runs in every mode and the era flag decides only whether
            # the clock cuts and caps.  `--coverage-days` is therefore the lever that makes a
            # run small -- a bin CAP cannot, because a smaller warehouse is a shorter trip and
            # the derived lines/day (and so the levels) rise to meet it.
            ('--coverage-days', 'coverage_days', _positive_float,
             "stock coverage: order-up-to = days x the SKU's daily demand"),
            ('--safety-days', 'safety_days', _nonneg_float,
             'safety stock: reorder point = demand over (lead + safety) days')):
        parser.add_argument(_flag, type=_type, default=CONFIG['global'][_key], metavar='X',
                            help=f'{_what[0].upper()}{_what[1:]} (default '
                                 f'{CONFIG["global"][_key]}).')
    # The line floor's default is None: SOLVED under the era (the shelf side of the
    # first-time confidence, per section), one line flag-off.  A typed value under the
    # era is accepted only at or above the solved floor and refused below it.
    parser.add_argument(
        '--floor-lines', type=_positive_float, default=CONFIG['global']['floor_lines'],
        metavar='X',
        help="The line floor: Q and the reorder point never below this many of the SKU's "
             'own mean line, rounded up; a floored SKU runs base stock. Omit to take one '
             'line flag-off, or under --shift-drain-or-cap the floor SOLVED per section so '
             'the first-pass fill clears sqrt(first-time confidence); a typed value is '
             'refused under the era when it is below the solved one.')
    parser.add_argument(
        '--put-crew-mode', choices=('foot', 'machine'),
        default=CONFIG['global']['put_crew_mode'],
        help="The put crew's travel MODE, which picks its speed table. A DECLARED staffing "
             'input: the derivation sizes the count, the mode is a labour-model term.')
    # ── the expected constants' overrides ──────────────────────────────────────────
    # Seconds per unit.  Omit to take the closed-form expectation computed at setup
    # (Optimization/simconfig/expected_travel.py); a number is recorded `declared`.
    for _flag, _key, _what in (
            ('--s-pick-store', 's_pick_store', 'seconds per unit picked, store channel'),
            ('--s-pick-ff', 's_pick_ff', 'seconds per unit picked, fulfillment channel'),
            ('--s-put', 's_put', 'seconds per unit put away, declared for every channel '
                                 'alike (the derived price is per channel)')):
        parser.add_argument(_flag, type=_positive_float, default=CONFIG['global'][_key],
                            metavar='SEC',
                            help=f'Override the expected value: {_what}. Omit to take the '
                                 f'closed-form expectation over this run\'s catalogue and geometry.')
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
        '--inbound-door-team', type=_positive_int,
        default=CONFIG['global']['inbound_door_team'], metavar='N',
        help='TRAILER PHYSICS: at most N receivers support one trailer at once, every one '
             'additive. Applies in BOTH allocation modes (the cap belongs to the trailer, '
             'not the dealing rule). The deal stays EVEN and is then cut, so 22 receivers '
             'over three staged trailers is 8/7/7 and over two is 10/10 with two idle until '
             'a door frees. Omit for uncapped — the whole crew may stand at one door. '
             'Requires --inbound-standing-yard, which is the only path that deals teams.')
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
             'precomputed batch script. A declared-UNLAWFUL clairvoyance reference, never '
             'in the recommendable set: it bounds pricing accuracy, not achievable gain.')
    for _flag, _key, _what in (
            ('--inbound-unload-intercept', 'inbound_unload_intercept', 'fixed seconds per unload'),
            ('--inbound-unload-weight-coef', 'inbound_unload_weight_coef', 'seconds per pound'),
            ('--inbound-unload-volume-coef', 'inbound_unload_volume_coef',
             'seconds per cubic inch')):
        parser.add_argument(_flag, type=float, default=CONFIG['global'][_key], metavar='SEC',
                            help=f'Unload cost: {_what}. Omit to take the put-away value BY '
                                 f'REFERENCE, which is what every existing run did — so the '
                                 f'default splits no results era.')
    parser.add_argument('--sampler', choices=('v1', 'v2', 'v3'),
                        default=CONFIG['global']['sampler'],
                        help='Batch-sampler VERSION — a results era, not a tuning knob. '
                             f'Default {CONFIG["global"]["sampler"]!r}. v1 reproduces the '
                             'pre-2026-08-20 archive byte-identically; v2 (the Fenwick '
                             'sampler) is faster but delivers FEWER distinct lines than it '
                             'is asked for; v3 (the segment-tree sampler) is the first that '
                             'delivers exactly k. Each era\'s batch caches are fingerprinted '
                             'apart, so they never mix.')
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
    return parser



def main():
    # FIRST statement in main, before the parser exists: `--help` is printed and exited from
    # INSIDE parse_args, so anything placed after it never runs on that path.  U+2192 (in
    # --s-composition's help) has no cp1252 mapping — unlike the em/en dashes and ellipses
    # elsewhere here — so `--help` on a legacy console died with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(errors='replace')   # tolerate non-utf-8 consoles (e.g. cp1252 → arrows)
    except Exception:
        pass

    parser = _build_parser()
    args = parser.parse_args()
    # Flags the user explicitly typed (used so a saved run_spec is the base but an explicit
    # flag on a resume command still wins).
    explicit = {d for d in vars(args) if getattr(args, d) != parser.get_default(d)}

    # Resolve base_dir FIRST, then on --resume load + apply the saved run_spec BEFORE the
    # CONFIG-override block, so a bare `--resume DIR` reconstructs the run with zero retyped
    # flags (and no find_latest_db_pairs drift — see the pairs block below).
    from Optimization.config.whatif_config import get_spec, swept_rules_of, SPECS

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
        # The era's regime, re-checked on the resume command line: a legacy crew flag typed
        # here is as much an error as on the original launch.
        _spec_notes.extend(_check_era_flags(args, explicit))
    else:
        spec_name, spec_dict = _resolve_spec()
        # A spec's run-level defaults (the era) overlay a NEW run's args; a typed flag wins.
        _spec_notes.extend(_apply_run_defaults(args, spec_dict, explicit))
        # The calibrated era's regime: refuse the derived crews' flags, complete the cadence,
        # the cut and the roll-over.  BEFORE the run dir exists, so a refused launch leaves no
        # phantom run behind, and before the CONFIG write-back so the record says what ran.
        _spec_notes.extend(_check_era_flags(args, explicit))
        n_cells  = len(_build_cells(spec_dict))          # >1 cell ⇒ a what-if sweep dir name
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix   = 'comparison_whatif' if n_cells > 1 else 'comparison'
        base_dir = os.path.join(_OUTPUT_DIR, f'{prefix}_{ts}')
        os.makedirs(base_dir, exist_ok=True)

    # ── apply CLI overrides onto CONFIG (the single source of truth; reconciled w/ run_spec) ──
    #
    # ONE LOOP, over `sim_config.KNOBS`.  This was ~35 hand-typed assignments plus two family
    # loops, and a knob missing from it was accepted at the command line and then silently
    # ignored for the whole run.  Each knob declares how it is written back ('always' vs
    # 'if_set', plus any coercion), so every behaviour that mattered is preserved per knob
    # rather than per line: `--n-batches 0` is still not discarded (`is not None`, never
    # truthiness), the store_true flags still become real bools, `--workers` still takes 1
    # when unset, and the two families that already rode one list each still do.
    #
    # CONFIG is mutated in place and never rebound, so every call-time accessor
    # (`seed_world()`, `channel_pickers()`, `inbound_spec()`, ...) sees this.
    g = CONFIG['global']
    _apply_cli_overrides(args)

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
        _aw, _ah = aisle_geometry()          # read at CALL time: --aisle-* has landed
        _floor_aisles, _floor_bins = structural_bin_floor(_HANDLINGS, _CATEGORIES,
                                                          _aw, _ah)
        if args.s_max_bins < _floor_bins:
            log.warning(
                f'  --s-max-bins {args.s_max_bins:,} is BELOW the store structural floor of '
                f'{_floor_bins:,} bins ({_floor_aisles} aisles: one per '
                f'{len(_HANDLINGS)}x{len(_CATEGORIES)}x5 bucket so every SKU is placeable). '
                f'The cap will NOT be honored and the store will size to the floor. This is '
                f'a property of the handling/category configuration, not of --max-skus. '
                f'And the floor is one aisle a bucket, which almost certainly cannot hold the '
                f'levels this run declares -- expect the plan to REFUSE. To make a run small, '
                f'shrink the declaration (--coverage-days) or the catalogue (--max-skus).')
    if args.resume and spec:
        log.info('  Resuming from run_spec.json — run-shaping params reconstructed; no retyped flags needed')
    log.info(f'Output directory : {base_dir}')
    log.info(f'Profiles dir     : {args.profiles_dir}')
    log.info('Mode             : '
             + ("all profiles" if args.all_profiles
                else f"named profile run {args.profile_run!r}" if args.profile_run
                else "latest profile only"))

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
    elif args.profile_run:
        # A NAMED run. `find_latest_db_pairs` can only ever return the newest, and newest
        # is not biggest -- a rung asking for more SKUs than the bound catalogue holds is
        # neither an error nor a warning, it just takes everything and reads as flat.
        from Schema.profile_resolver import ProfileTree
        _tree = ProfileTree(args.profiles_dir)
        _known = list(_tree.runs())
        if args.profile_run not in _known:
            sys.exit(f'--profile-run {args.profile_run!r} is not under '
                     f'{args.profiles_dir}. Available: '
                     + (', '.join(_known) if _known else '(none)'))
        pairs = _tree.pairs(args.profile_run)
        log.info(f'  Binding NAMED profile run {args.profile_run!r} '
                 f'({len(pairs)} pair(s)) instead of the newest')
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
            # EVERY declared knob, each from the source its record declares
            # (`sim_config.KNOBS`).  These were 49 hand-written entries, and both restore
            # sites put back exactly this set, so "recorded but never restored" is no
            # longer expressible.
            **_run_spec_record(args),
            'argv'         : sys.argv,
            # A resume MUST rebuild the same world and redraw the same demand.
            's_max_aisles' : args.s_max_aisles, 's_max_bins' : args.s_max_bins, 's_min_bins': args.s_min_bins,
            'ff_max_aisles': args.ff_max_aisles, 'ff_max_bins': args.ff_max_bins, 'ff_min_bins': args.ff_min_bins,
            's_composition': _store_comp,
            # Read back by run_analysis so a standalone re-analysis sizes the warehouse the way
            # the RUN did, not the way this checkout's CONFIG happens to be set.  None under
            # the era: nothing sized from a typed fill there -- the per-bucket holds the run
            # derived ride the coverage record, and both restore sites skip a None
            # ("Derive the fill headroom from the fragmentation").
            'store_fill'   : None if g['shift_drain_or_cap'] else CONFIG['channels']['store']['fill'],
            'ff_fill'      : None if g['shift_drain_or_cap'] else CONFIG['channels']['fulfillment']['fill'],
            # The working day: two runs with different days are otherwise
            # indistinguishable after the fact, and a resume would finish an arm on a
            # different clock than it started on.
            # The calibrated era.  A RESULTS ERA: two runs on either side of it are not
            # comparable, and a resume must finish under the regime it started in.
            # The site dock.  Recorded for the same reason as the era above: it decides how
            # the site's crews are FIELDED, so a resume must finish under the model it
            # started in and two runs on either side of it do not compare.
            # The receiving crew and its own day. Read from `g` (post-overlay), not from
            # `args`, so a value that came from CONFIG rather than the command line is
            # recorded too -- otherwise two runs with different docks look identical.
            # The split put-away configuration. Read from `g` (post-overlay) so a value
            # that came from CONFIG rather than the command line is recorded too.
            # The other crews' price as scalars of the pickers' -- recorded so a resume and
            # a re-analysis price put-away and receiving as the run did.
            # THE STAFFING RECORD -- one nested key, not flat entries, because it is the
            # artifact the calibrated era exists to produce and it grows: `inputs` is what
            # was declared (the STAFFING_KEYS, read post-overlay through the accessor so a
            # value that came from CONFIG is recorded too); `provenance` says, per input,
            # whether a flag chose it (`declared`) or a settings default did (`assumed`);
            # `derived` and `calibration` are added PER PAIR by the derivation at setup
            # (workunits._record_derived), after batch precompute, because the receiving
            # crew needs the packs the script implies.  Both restore sites read `inputs` by
            # iterating STAFFING_KEYS, and the whole record is stamped onto sim_result for
            # the evaluations (run_analysis, the sixth seam).  The three CALIBRATION_KEYS
            # are `declared` only when typed; untyped they are `assumed` HERE (no override
            # was chosen) and the resolved constant under `calibration` is the closed-form
            # expectation, provenance `derived`.  The REGIME decides which keys are inputs
            # (ADR-0004, `staffing_provenance`): under the era the picker keys are None in
            # `inputs` and `derived` in `provenance`, the demand and confidence declared or
            # assumed; flag-off the era-only keys are None and carry no provenance.
            'staffing': {
                'inputs'    : staffing_spec(),
                'provenance': staffing_provenance(explicit),
                'era'       : bool(g['shift_drain_or_cap']),
                # THE CAMPAIGN PIN the run was LAUNCHED with (whatif_config.PHASE2_STAFFING_PIN
                # through the spec; None for every non-campaign run).  Recorded here rather than
                # re-read from the spec at setup for the same reason the era is: a resume must
                # finish under the pin it started on, and `_record_derived` is handed this file
                # already.  Checking the MATCH needs a derivation, which does not exist until
                # the pair is prepared, so the shape half lives in `validate_spec` and this is
                # how the value reaches the half that can compare.
                'pin'       : (spec_dict or {}).get('staffing_pin'),
            },
            # The inbound family. Read from `g` (post-overlay) like the two families above,
            # and recorded WHOLE rather than only when on: a phase-2 cell that cannot say
            # which lead shape and which fee threshold it ran under is not re-analysable, and
            # the yard's derive-late fee report reads the threshold off this record (its
            # HEAD-default fallback exists for runs that predate recording — the campaign must
            # never exercise it).
            # The lead draw's DOMAIN TAG, recorded beside the shape it keys. Un-re-derivable
            # by construction (a literal chosen to be stable), so a comparison spanning a TAG
            # change would silently span two different arrival schedules with nothing to
            # detect it from -- the same argument that puts `sampler` in this file.
            'inbound_lead_tag'  : _LEAD_TAG,
            'whatif': args.whatif, 'spec': spec_name,
            'profiles_dir' : args.profiles_dir, 'all_profiles': args.all_profiles,
            'profile_run'  : args.profile_run,
            'max_tasks_per_child': args.max_tasks_per_child,
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
            # Through the spec module's own accessor, so a rule-pair campaign records the rules
            # it actually swept: read straight off `arms`, a pair spec has none and the
            # descriptor would claim the committed full suite (site-dock 23).  Identical to the
            # old expression for every arm-shaped spec.
            arms=swept_rules_of(spec_dict),
            created=datetime.now().isoformat(timespec='seconds'),
            # Read through the ONE predicate the work-unit builder uses, so the descriptor
            # and the units cannot disagree about whether this run is a site.
            coupled=couple_channels())

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
