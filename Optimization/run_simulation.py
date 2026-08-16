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
    CONFIG, REGRESSION_CONFIGS, STORE_CONFIGS, FULFILLMENT_CONFIGS,
    SEED_WORLD, SEED_BATCHES, N_BATCHES, K_PICKERS, STORE_RESTOCKS, store_fill,
    _OUTPUT_DIR, _DEFAULT_PROFILES_DIR, _CATEGORIES, _HANDLINGS, _AISLE_W, _AISLE_H,
    _STORE_PICKERS, _FF_PICKERS, _CART_TYPES,
    regime_sizing_from_config, _setup_logging, _checkpoint_every,
    _config_name, _build_pick_cfg, _clean_path, _load_env,
)
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
              'store_fill', 'ff_fill', 'checkpoint_frac'):
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
    if args.n_batches:
        g['n_batches'] = args.n_batches
    if args.max_skus is not None:
        g['max_skus'] = args.max_skus
    g['workers']           = args.workers or 1
    g['keyframe_interval'] = args.keyframe_interval
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
            's_max_aisles' : args.s_max_aisles, 's_max_bins' : args.s_max_bins, 's_min_bins': args.s_min_bins,
            'ff_max_aisles': args.ff_max_aisles, 'ff_max_bins': args.ff_max_bins, 'ff_min_bins': args.ff_min_bins,
            's_composition': _store_comp,
            # Read back by run_analysis so a standalone re-analysis sizes the warehouse the way
            # the RUN did, not the way this checkout's CONFIG happens to be set.
            'store_fill'   : CONFIG['channels']['store']['fill'],
            'ff_fill'      : CONFIG['channels']['fulfillment']['fill'],
            'checkpoint_frac': g['checkpoint_frac'],
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
                              max_retries=args.max_retries, resume_granularity=args.resume_granularity,
                              max_tasks_per_child=args.max_tasks_per_child)

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
