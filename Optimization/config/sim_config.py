"""sim_config.py — the run's single source of tunable truth.

Everything a run can be tuned by lives here: the .env-backed output/profile dirs,
the per-channel pick-config sweeps (STORE_CONFIGS / FULFILLMENT_CONFIGS), the nested
CONFIG dict (global + per-channel sections), the run-shaping accessors, and the
config→PickConfig conversion.  run_simulation re-exports the public names so
`rs.CONFIG` / `rs.REGRESSION_CONFIGS` consumers (tests, Diagnostics/bucket_fill)
keep working.

CONFIG identity rule: the re-export binds the SAME dict object — tests mutate
`rs.CONFIG['global'][...]` in place — so nobody may ever REBIND CONFIG (or the
mutation would silently detach from internal readers).

Corollary, and the reason the run-shaping values are FUNCTIONS rather than module
scalars: a value snapshotted at import cannot see that mutation.  Every consumer of a
tunable reads it at call time.  See the block above `seed_world()`.
"""
import logging
import os
import sys
from dataclasses import dataclass, fields as _dataclass_fields

_HERE      = os.path.dirname(os.path.abspath(__file__))
# NB two levels up: this module lives at Optimization/config/, not Optimization/.  _REPO_ROOT is
# used for `.env` and the default profiles dir, NOT for imports — so getting it wrong fails
# SILENTLY: .env stops loading, COMPARISON_OUTPUT_DIR falls back to the source tree, and a run
# writes a 150-200 GB output tree into the repo with no error.  Asserted below.
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
assert os.path.isdir(os.path.join(_REPO_ROOT, 'Warehouse')), (
    f'sim_config._REPO_ROOT resolved to {_REPO_ROOT!r}, which is not the repo root — the `..` count '
    f'must match this file\'s depth (see the note above).')

# ── .env support ──────────────────────────────────────────────────────────────
# Reads <repo_root>/.env and injects KEY=VALUE pairs into os.environ.
# No external packages required.  Shell-set variables are never overwritten.
# Recognised variables:
#   COMPARISON_OUTPUT_DIR  — parent directory for comparison_<ts>/ output folders
#   PROFILE_INPUT_DIR      — root directory for inventory+affinity DB pairs
# THE loader lives in `envfile`, which imports only `os` -- see that module for why it
# sits in this package rather than in the kernel or at the repo root.  The underscore
# names are kept as aliases: `run_simulation` imports them from here by those names.
from Optimization.config.envfile import clean_path as _clean_path, load_env as _load_env

_load_env(os.path.join(_REPO_ROOT, '.env'))

from Warehouse.layout.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.picking.Pick import PickConfig, DEFAULT_HEIGHT_BRACKETS
from Warehouse.layout.Storage_Primitive import StoreCart, FulfillmentCart
from Warehouse.kernel.regime import STORE, FULFILLMENT
from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS as _DEFAULT_SHIFT_SECONDS
from Optimization.config.strategies import restocks_for
from Optimization.config.channels import FF_BATCH_SEED_OFFSET
from Optimization.simconfig import PICK_CONFIGS                          # fires the registry import_all()
from copy import deepcopy as _deepcopy
from Optimization.simconfig.constants import _STORE_PICKERS, _FF_PICKERS
from Optimization.config import settings as _s

# ── warehouse geometry (structural; shared by both channels) ────────────────────
# Physical aisle dimensions: AISLE_COLUMNS pallet-width columns × AISLE_LEVELS
# extra_large-height levels.  Actual bin counts per aisle depend on unit type and size
# distribution.
#
# Read through `aisle_geometry()` below, NEVER as module scalars.  These were
# `aisle_width_for(50)` / `aisle_height_for(10)` evaluated at import, which is the shape
# `Tests/unit/test_config_reaches_the_worker.py` calls a shipped defect: a snapshot taken at
# import cannot see a CLI override, and `_INITIAL_FILL` did exactly that and made a run
# misreport its own sizing in its own warehouse DB.

# Picker-pool DEFAULTS per channel live in Optimization/simconfig/constants.py (imported above as
# _STORE_PICKERS / _FF_PICKERS; re-exported for the diagnostics that reach them via `rs.`).  The
# live values are CONFIG['global']['store_pickers'] / ['ff_pickers'], read at call time by
# `channel_pickers(name)` below; a pick-config entry that names its own 'num_pickers' must agree
# with its channel's declared count or setup raises (see `workunits._channel_runs_for`).


_OUTPUT_DIR = _clean_path(os.getenv(
    'COMPARISON_OUTPUT_DIR',
    _HERE,
))
_DEFAULT_PROFILES_DIR = _clean_path(os.getenv(
    'PROFILE_INPUT_DIR',
    os.path.normpath(os.path.join(_REPO_ROOT, 'Warehouse', 'generated', 'profiles')),
))

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_HANDLINGS  = ['conveyable', 'non-conveyable']

# Warehouse layout is no longer a static table — Inventory_Manager.plan_warehouse
# builds per-(handling, category, size_tier, unit_type) uniform aisles sized to
# the actual inventory, guaranteeing every bucket exists (≥1 aisle) so every
# SKU is placeable.  See Warehouse/inventory/Inventory_Management.py.


# ── per-channel config sweeps (rebuilt from the simconfig registry) ─────────────
# Store and fulfillment are INDEPENDENT warehouse sections (see channels.py): each sweeps its OWN
# set of pick-time configs, runs its own restock suite, writes its own DB subtree, and is combined
# only post-analysis by run_channel_rollup.  The sweep is a UNION, not a cross product: a mixed
# catalog runs len(STORE_CONFIGS) store runs + len(FULFILLMENT_CONFIGS) fulfillment runs.
#
# The pick-config DICTS now live as self-registering modules under Optimization/simconfig/configs/
# (mirroring the graph suite — add a config = drop a module).  Here we rebuild the per-channel lists
# from the registry, filtered by channel + enabled and ordered by the spec's `order` (LIST ORDER IS
# LOAD-BEARING — it sets config-dir identity + sweep order, so it must NOT depend on filesystem walk
# order).  STORE_CONFIGS / FULFILLMENT_CONFIGS / REGRESSION_CONFIGS keep their names (run_simulation
# re-exports them; CONFIG['channels'][*]['configs'] references them; tests read rs.REGRESSION_CONFIGS)
# but their VALUES are assembled from the registry.  The commented-out store variants are now
# enabled=False modules (a discoverable menu); the fast-walker ff variant stays removed.
#
# The dicts here are COPIES of the registry's, and that is load-bearing.  A PickConfigSpec is
# frozen, but `frozen` guards rebinding the field — not mutating the dict it points at, and
# `cells._apply_cell` writes `cfg['scheduler']` into every entry of
# CONFIG['channels'][*]['configs'] for each what-if cell.  While these were the registry's own
# dicts, that write reached `PICK_CONFIG_BY_KEY['store'].cfg` and stayed there for the life of
# the process: a key the config author never declared, materialised by whichever cell ran last.
#
# So the split is explicit.  The REGISTRY is the immutable declaration — what the author wrote.
# CONFIG is the mutable run state — the declaration plus this run's overrides.  One copy at
# build time is the whole boundary between them.  (The LIST objects still alias, so
# `CONFIG['channels']['store']['configs'] is STORE_CONFIGS` holds as before.)
_ACTIVE_PICK_CONFIGS = sorted((s for s in PICK_CONFIGS if s.enabled),
                              key=lambda s: (s.channel, s.order, s.name))
STORE_CONFIGS       = [dict(s.cfg) for s in _ACTIVE_PICK_CONFIGS if s.channel == 'store']
FULFILLMENT_CONFIGS = [dict(s.cfg) for s in _ACTIVE_PICK_CONFIGS if s.channel == 'fulfillment']
REGRESSION_CONFIGS  = STORE_CONFIGS      # legacy alias: REGRESSION_CONFIGS IS the store set


# ── nested run configuration (single source of truth) ───────────────────────────
# Everything tunable lives here: a `global` section (run-wide: seeds, batch count, pool
# size, checkpointing) and a per-channel `channels` section so store and fulfillment can
# be tuned INDEPENDENTLY — each with its own pick-config sweep, restock subset, picker
# pool, cart, batch-stream shape, fill headroom, and warehouse sizing.  CLI flags override
# these defaults (see main()).  Read internally via `g = CONFIG['global']` /
# `CONFIG['channels'][name]`; a few module-level aliases below mirror the common values so
# external read-only consumers (bucket_fill) keep working.
CONFIG = {
    'global': {
        'seed_world'      : _s.SEED_WORLD,
        'seed_batches'    : _s.SEED_BATCHES,
        'n_batches'       : _s.N_BATCHES,
        'workers'         : _s.WORKERS,
        'checkpoint_frac' : _s.CHECKPOINT_FRAC,   # every ceil(n_batches * frac) batches
        # Keyframes are no longer how spatial state is RECONSTRUCTED — bin_placement +
        # bin_eviction + picks fold to exact bin state at every batch, with no keyframe
        # involved.  What a keyframe is now: an INDEPENDENT audit of that fold (the viewer
        # compares the two) and a quantity anchor that bounds a pick scan to one interval.
        # Neither job needs 5: that wrote 20 full-warehouse snapshots per 100-batch arm,
        # ~3.1M rows, to re-answer a question the log answers exactly.  25 keeps both roles
        # at a fifth of the cost.  0 still disables the sidecar entirely.
        'keyframe_interval': _s.KEYFRAME_INTERVAL,
        'aisle_columns'   : _s.AISLE_COLUMNS,
        'aisle_levels'    : _s.AISLE_LEVELS,
        'max_skus'        : _s.MAX_SKUS,   # input-catalog cap (preserves the store/ff mix)
        # Batch-sampler VERSION — a results ERA, not a tuning knob.  Each version draws the
        # same weight model but a different SEQUENCE, so runs across a flip are not
        # row-comparable; batch caches are fingerprinted apart per sampler, so the eras can
        # never contaminate each other, and `--sampler v1`/`v2` reproduce their archives.
        #   v1  the original O(k·N) cumsum sampler — every pre-2026-08-20 run.
        #   v2  the Fenwick sampler (e7c9ed9, default 2026-08-20): O((k·(1+partners))·log N),
        #       measured 21.6s -> 0.48s per batch at 160k SKUs.  DEFECTIVE — its subtractive
        #       tree update loses small weights under this model's ~1e26 weight dynamic
        #       range and re-draws SKUs already taken, so its batches are short.
        #   v3  the segment-tree sampler (default 2026-09-12): recomputes each node from its
        #       children instead of adjusting it by a delta, and so is the first version that
        #       delivers exactly k distinct SKUs.  ~1.5x v2, ~1/35th of v1.
        'sampler'         : _s.SAMPLER,
        # Shift length in SECONDS (the sim's own unit), 8 hours by default.  A REPORTING
        # FRAME over a continuous clock: it labels work_events.shift_index and nothing
        # dispatches against it -- work does not pause at the whistle and no task is split
        # at a boundary.  See Warehouse.kernel.timeline.shift_index.
        'shift_seconds'   : _s.REPORTING_FRAME_SECONDS,   # CONFIG key frozen (recorded surface); the authoring name is the honest one
        # The WORKING DAY, which unlike shift_seconds above actually dispatches: it decides
        # when a batch is released and when a picker is stopped.  Defaults reproduce the
        # pre-working-day runner exactly.  See Warehouse.kernel.timeline.WorkDay.
        'work_day_seconds': _s.WORK_DAY_SECONDS,
        'releases_per_day': _s.RELEASES_PER_DAY,
        'cut_at_day_end'  : _s.CUT_AT_DAY_END,
        'roll_over_unpicked': _s.ROLL_OVER_UNPICKED,
        # The RECEIVING CREW and its own day.  size 0 = no crew, which is every run before
        # this existed; see recv_crew_spec below for why that is a structural no-op.
        'recv_crew_size' : _s.RECV_CREW_SIZE,
        'recv_day_seconds': _s.RECV_DAY_SECONDS,
        'recv_day_origin': _s.RECV_DAY_ORIGIN,
        'shift_drain_or_cap': _s.SHIFT_DRAIN_OR_CAP,
        'couple_channels': _s.COUPLE_CHANNELS,
        'inbound_trailer_type'  : _s.INBOUND_TRAILER_TYPE,
        'inbound_dock_doors'    : _s.INBOUND_DOCK_DOORS,
        'inbound_lead_minutes'  : _s.INBOUND_LEAD_MINUTES,
        'inbound_lead_spread'   : _s.INBOUND_LEAD_SPREAD,
        'inbound_local_policy'  : _s.INBOUND_LOCAL_POLICY,
        'inbound_trailer_bound' : _s.INBOUND_TRAILER_BOUND,
        'inbound_plan_trace'    : _s.INBOUND_PLAN_TRACE,
        'inbound_fill_span_days': _s.INBOUND_FILL_SPAN_DAYS,
        'inbound_fill_ratio'    : _s.INBOUND_FILL_RATIO,
        # The standing yard (real doors, split yard/dock priorities, door-team crews,
        # own unload coefficients).  All riding inbound_spec below, so the whole family
        # crosses the worker payload as one record.
        'inbound_standing_yard'    : _s.INBOUND_STANDING_YARD,
        'inbound_crew_allocation'  : _s.INBOUND_CREW_ALLOCATION,
        'inbound_yard_policy'      : _s.INBOUND_YARD_POLICY,
        'inbound_dock_policy'      : _s.INBOUND_DOCK_POLICY,
        'inbound_door_team'        : _s.INBOUND_DOOR_TEAM,
        'inbound_fee_threshold_days'   : _s.INBOUND_FEE_THRESHOLD_DAYS,
        'inbound_urgency_horizon_days' : _s.INBOUND_URGENCY_HORIZON_DAYS,
        'inbound_futuresight_batches'  : _s.INBOUND_FUTURESIGHT_BATCHES,
        'inbound_unload_intercept' : _s.INBOUND_UNLOAD_INTERCEPT,
        'inbound_unload_weight_coef': _s.INBOUND_UNLOAD_WEIGHT_COEF,
        'inbound_unload_volume_coef': _s.INBOUND_UNLOAD_VOLUME_COEF,
        # The SPLIT put-away configuration.  False = one catch-all queue, which is every
        # run before this existed; see put_queues_spec below for why that is a structural
        # no-op rather than a flag test.
        'put_queue_split'   : _s.PUT_QUEUE_SPLIT,
        'put_cart_crew'     : _s.PUT_CART_CREW,
        'put_pallet_crew'   : _s.PUT_PALLET_CREW,
        'put_ff_crew'       : _s.PUT_FF_CREW,
        'put_cart_staging'  : _s.PUT_CART_STAGING,
        'put_pallet_staging': _s.PUT_PALLET_STAGING,
        'put_ff_staging'    : _s.PUT_FF_STAGING,
        'put_swap_coef'     : _s.PUT_SWAP_COEF,
        # The other crews' PRICE as scalars of the pickers' (settings, "the other crews'
        # price"); read at call time by crew_cost_spec below and carried in the payload.
        'put_intercept_scale' : _s.PUT_INTERCEPT_SCALE,
        'put_item_ratio'      : _s.PUT_ITEM_RATIO,
        'recv_intercept_scale': _s.RECV_INTERCEPT_SCALE,
        # STAFFING: pickers per channel -- FLAG-OFF the declared headcount, under the era
        # a placeholder the derivation replaces (ADR-0004: demand is declared, the crew
        # derived).  Two flat GLOBAL keys (not a per-channel entry) so they ride the same
        # flag / run-spec / restore / payload machinery as every other run-shaping knob; the
        # channel dicts below deliberately carry no 'num_pickers' -- `channel_pickers(name)`
        # reads these at call time, and `staffing_spec()` records them.  STAFFING_KEYS is the
        # spliced list every seam iterates.
        'store_pickers'       : _s.STORE_PICKERS,
        'ff_pickers'          : _s.FF_PICKERS,
        # The era's DECLARED DEMAND per channel (the sampler's unit) and the joint
        # first-time confidence the floor and the picking crew are solved from (settings,
        # "the calibrated era's DECLARED DEMAND").  Era-only: `staffing_spec()` records
        # them as None flag-off, and the era records the picker keys as None instead.
        'store_demand'        : _s.STORE_DEMAND,
        'ff_demand'           : _s.FF_DEMAND,
        'first_time_confidence': _s.FIRST_TIME_CONFIDENCE,
        # The minimum headroom the era's DERIVED fill keeps per bucket (settings,
        # `MIN_HEADROOM`).  Era-only like the three above: flag-off the fill is the typed
        # `channels.<ch>.fill` and this is None; under the era the typed fill is unread.
        'min_headroom'        : _s.MIN_HEADROOM,
        # The era's declared scalars (settings, "the calibrated era's declared scalars")
        # and the put crew's MODE -- staffing INPUTS, on STAFFING_KEYS with the pickers.
        # `rho_pick` is flag-off only since ADR-0004 (the confidence replaces it).
        'rho_pick'            : _s.RHO_PICK,
        'rho_put'             : _s.RHO_PUT,
        'rho_recv'            : _s.RHO_RECV,
        'f_put'               : _s.F_PUT,
        'f_recv'              : _s.F_RECV,
        'f_repack'            : _s.F_REPACK,
        'band_tol'            : _s.BAND_TOL,
        'put_crew_mode'       : _s.PUT_CREW_MODE,
        # Stock coverage in days (settings, "stock coverage"): staffing INPUTS the era's
        # setup re-derives every SKU's stock levels from (simdriver/era_coverage.py).
        'coverage_days'       : _s.COVERAGE_DAYS,
        'safety_days'         : _s.SAFETY_DAYS,
        'floor_lines'         : _s.FLOOR_LINES,
        # The calibration constants' overrides (None = the committed record) -- the
        # CALIBRATION_KEYS, also staffing inputs: typed, they are `declared`; untyped,
        # the resolved constant carries the record's own provenance.
        's_pick_store'        : _s.S_PICK_STORE,
        's_pick_ff'           : _s.S_PICK_FF,
        's_put'               : _s.S_PUT,
        # The put crew's SIZE: flag-off only.  The `put_crew_spec` trap is closed -- the
        # accessor reads THIS key, so `--put-crew-size` reaches a worker -- and under the
        # era the size is derived and an explicit flag raises (run_simulation).
        'put_crew_size'       : _s.PUT_CREW_SIZE,
    },
    'channels': {
        'store': {
            'regime'     : STORE,
            'configs'    : STORE_CONFIGS,
            'pick_mode'  : _s.STORE_PICK_MODE,
            'restocks'   : restocks_for('store'),
            'cart'       : _s.STORE_CART,
            'seed_offset': 0,
            'batch'      : {'mean': _s.STORE_BATCH_MEAN, 'std': _s.STORE_BATCH_STD},
            'fill'       : _s.STORE_FILL,
            # aisle_split (optional): cut each aisle into k shorter segments (~depth/k) with a
            # capacity_loss modeling throughway construction.  None/{'k':1} = no split
            # (byte-identical).  e.g. {'k': 2, 'capacity_loss': 0.15}.
            'sizing'     : {'min_bins': None, 'max_bins': None, 'max_aisles': None,
                            'composition': None, 'aisle_split': None},
            # Velocity zoning: restrict each unit's viable aisles to its velocity band
            # ("like-with-like"), composing with every arm.  enabled=False = byte-identical.
            # mode 'equal' (default, equal-count bands) | 'abc' (manual A/B/C by demand-mass
            # thresholds; aisles allocated by band footprint so the hot band is a small fraction).
            'velocity_zoning': _deepcopy(_s.ZONING_OFF),
        },
        'fulfillment': {
            'regime'     : FULFILLMENT,
            'configs'    : FULFILLMENT_CONFIGS,
            'pick_mode'  : _s.FF_PICK_MODE,
            'restocks'   : restocks_for('fulfillment'),
            'cart'       : _s.FF_CART,
            'seed_offset': FF_BATCH_SEED_OFFSET,
            'batch'      : {'mean': _s.FF_BATCH_MEAN, 'std': _s.FF_BATCH_STD},
            'fill'       : _s.FF_FILL,
            # Sized from the requirement, exactly as the store is.  There was a fixed tier
            # distribution here (0.5/0.3/0.2 across ff_small/ff_medium/ff_large, scaled to a
            # target_bins); it is retired because it ignored the ff demand mix, and the
            # declared levels need 11/63/26 -- so ff_medium ran 293k bins short on a section
            # that was, in total, the right size, and 30% of its SKUs were fielded below
            # their line floor (.scratch/department-calibration, "Field the floor" 8).
            # depth_classes (optional): split each ff size tier's aisles into shallow/deep
            # SHAPES sharing one BinKey, so velocity zoning / trip-min can route hot SKUs to
            # shallow (low-travel) aisles.  Each = {'columns': n, 'share': w}; None = one width
            # (byte-identical).  e.g. [{'columns':10,'share':0.3},{'columns':40,'share':0.4},
            #                          {'columns':100,'share':0.3}]
            # aisle_split (optional): cut each aisle into k shorter segments with a capacity_loss
            # (throughway construction).  None/{'k':1} = byte-identical.  e.g. {'k':2,'capacity_loss':0.15}.
            'sizing'     : {'depth_classes': None, 'aisle_split': None,
                            'min_bins': None, 'max_bins': None, 'max_aisles': None},
            # Velocity zoning is the fulfillment experiment axis (default off = byte-identical);
            # pairs with depth_classes/aisle_split so hot SKUs cluster into shallow aisles.
            'velocity_zoning': _deepcopy(_s.ZONING_OFF),
        },
    },
}

# ── run-shaping accessors — read CONFIG at CALL time ────────────────────────────
#
# These were five import-time scalars (SEED_WORLD, SEED_BATCHES, N_BATCHES, K_PICKERS,
# STORE_RESTOCKS) that captured CONFIG's values once, at import, while the module's own
# docstring promised CONFIG was authoritative and mutated in place.  `_INITIAL_FILL` was
# the sixth and it is already gone — it made a run misreport its own sizing, because
# `run_simulation` writes the CLI override into CONFIG and the snapshot never saw it.
#
# The remaining five had the same defect and it had not bitten yet only because
# `--n-batches` is the only one of them with a flag, and nothing read `N_BATCHES` on the
# run path.  `seed_world` and `seed_batches` decide whether two runs are comparable at
# all, and `workunits` derives every channel's batch seed from `SEED_BATCHES` — so the
# first CLI flag for either would have been silently ignored by the worker payload.
#
# Same rule as store_fill(): read at call time, never snapshot.

def seed_world() -> int:
    """The world seed (warehouse + catalogue construction), read at call time."""
    return CONFIG['global']['seed_world']


def seed_batches() -> int:
    """The base batch-stream seed, read at call time.

    A CHANNEL's seed is this plus its `batch_seed_offset` (see `channels.Channel`), so
    store and fulfillment draw independent streams from one configured base.
    """
    return CONFIG['global']['seed_batches']


def n_batches() -> int:
    """The configured batch horizon, read at call time (`--n-batches` writes CONFIG)."""
    return CONFIG['global']['n_batches']


def aisle_geometry() -> tuple[int, int]:
    """(width, height) of one aisle in physical units, read at CALL time.

    A FUNCTION, not two module scalars, and deliberately: the pair used to be
    `aisle_width_for(50)` / `aisle_height_for(10)` evaluated at import, so a CLI override
    could never reach them. That is the `_INITIAL_FILL` defect this project has already
    shipped once -- a run misreporting its own sizing in its own warehouse DB --
    and `Tests/unit/test_config_reaches_the_worker.py` mutates the CONFIG key and calls
    every accessor twice to keep it from coming back.

    PARENT-SIDE. The warehouse is planned before any worker exists, so this is not in
    the worker payload; it is listed in that test's `PARENT_ONLY` with the reason.
    """
    g = CONFIG['global']
    return aisle_width_for(g['aisle_columns']), aisle_height_for(g['aisle_levels'])


#: Every `CONFIG['global']` key the staffing record's INPUTS block carries -- the family's
#: SHAPE in one place, on the `INBOUND_KEYS` precedent: the CLI flags, the run-spec record and
#: BOTH restore sites iterate this list rather than retyping the keys, so a new staffing input
#: cannot be recorded and then not restored.  Order is the order the flags are emitted in.
#: The two picker counts, the two demand declarations, the first-time confidence and the
#: minimum headroom, the utilization / replenishment scalars, the band tolerance, the put
#: crew's mode, the stock coverage, and the three calibration overrides
#: (.scratch/department-calibration, "Design the staffing record", decision 2; ADR-0004;
#: "Derive the fill headroom from the fragmentation").  Derived values are NEVER on this
#: list: the derivation's outputs live in the run spec's `staffing.derived` block and cannot

# ══════════════════════════════════════════════════════════════════════════════════════
# THE KNOB REGISTRY -- one declaration per tunable
# ══════════════════════════════════════════════════════════════════════════════════════
#
# A knob used to be one fact written down in six places: a settings constant, a CONFIG key,
# an `add_argument`, a write-back line in `run_simulation.main`, a `run_spec.json` entry, and
# TWO restore sites (`run_simulation._apply_run_spec` for a resume, `run_analysis._apply_run_shape`
# for a standalone re-analysis).  `run_analysis` calls its own "the sixth seam" in as many
# words, because an analysis worker is spawned too and re-imports pristine defaults.
#
# Every omission was silent and lasted the whole run.  Two families -- staffing and inbound --
# already rode a single tuple each for exactly that reason, and the mechanism worked; the other
# 32 keys were retyped at every site, roughly 128 hand-written assignments for 32 facts.
#
# This is that mechanism, generalised.  A `Knob` declares what every downstream loop needs to
# know, and the loops are derived rather than authored:
#
#   apply       how `main` writes the CLI value back onto CONFIG['global'].
#               'always'  -- assign unconditionally; the flag defaults FROM CONFIG, so a
#                            flag-less run writes back exactly what was already there.
#               'if_set'  -- assign only when the flag was given.  `is not None`, never
#                            truthiness: `--n-batches 0` was silently discarded once.
#               'parent'  -- no CLI flag at all; nothing to write back.
#   coerce      'bool' for store_true flags whose CONFIG value must be a real bool;
#               'or_one' for `workers`, which takes 1 when unset.
#   spec_from   where run_spec.json records this knob's value from, and None when it is not
#               recorded at all.  'config' reads CONFIG POST-overlay, so a value that came
#               from CONFIG rather than the command line is recorded too.  'args' reads the
#               parsed namespace, and the distinction is load-bearing rather than stylistic:
#               `workers` is `args.workers or 1` in CONFIG, so recording it from CONFIG would
#               write 1 where the run recorded None.  Both restore sites put back exactly the
#               set with a non-None `spec_from`, so "recorded but never restored" stops being
#               expressible -- that defect shipped once (`put_swap_coef`) and all 1,534 tests
#               stayed green.
#
# NOT derived from here, and deliberately: the argparse declarations themselves (70 flags with
# bespoke help, types and choices) and the `workunits._shared` payload, which carries BUNDLES
# (`*_spec()` accessors) rather than flat keys for most of its contents.  Both are recorded in
# the ticket; this registry closes the write-back and the record/restore pair, which are the
# two that could drift apart without any error.


@dataclass(frozen=True)
class Knob:
    """One tunable of CONFIG['global'], declared once."""
    name: str                      # the CONFIG key AND the argparse dest
    family: str = ''               # 'staffing' | 'inbound' | '' (loose)
    apply: str = 'always'          # 'always' | 'if_set' | 'parent'
    coerce: str | None = None      # None | 'bool' | 'or_one'
    spec_from: str | None = 'config'   # 'config' | 'args' | None (not recorded)


def _knobs(names, **kw):
    return tuple(Knob(n, **kw) for n in names)


#: Order is the order the flags and the run-spec record are emitted in, so it reads for a
#: human as much as for the loops.
KNOBS: tuple[Knob, ...] = (
    # ── the run's shape ───────────────────────────────────────────────────────────────
    Knob('n_batches',           apply='if_set'),
    Knob('max_skus',            apply='if_set'),
    Knob('seed_world',          apply='if_set'),
    Knob('seed_batches',        apply='if_set'),
    Knob('workers',             coerce='or_one', spec_from='args'),
    Knob('keyframe_interval',   spec_from='args'),
    Knob('checkpoint_frac',     apply='if_set'),
    Knob('sampler'),
    Knob('aisle_columns',       spec_from='args'),
    Knob('aisle_levels',        spec_from='args'),
    # ── the working day ───────────────────────────────────────────────────────────────
    Knob('work_day_seconds'),
    Knob('releases_per_day'),
    Knob('cut_at_day_end',      coerce='bool'),
    Knob('roll_over_unpicked',  coerce='bool'),
    Knob('shift_drain_or_cap',  coerce='bool'),
    #: No CLI flag: declared beside the site day and read by the parent only.
    Knob('shift_seconds',       apply='parent', spec_from=None),
    # ── the site dock ─────────────────────────────────────────────────────────────────
    Knob('couple_channels',     coerce='bool'),
    Knob('recv_crew_size'),
    Knob('recv_day_seconds'),
    Knob('recv_day_origin'),
    # ── put-away: the split queues, their crews and their price ───────────────────────
    Knob('put_queue_split',     coerce='bool'),
    Knob('put_cart_crew'),
    Knob('put_pallet_crew'),
    Knob('put_ff_crew'),
    Knob('put_cart_staging'),
    Knob('put_pallet_staging'),
    Knob('put_ff_staging'),
    Knob('put_swap_coef'),
    Knob('put_crew_size'),
    Knob('put_intercept_scale'),
    Knob('put_item_ratio'),
    Knob('recv_intercept_scale'),
    # ── the two families that already rode one list each ──────────────────────────────
    *_knobs(('store_pickers', 'ff_pickers',
             'store_demand', 'ff_demand', 'first_time_confidence',
             'min_headroom',
             'rho_pick', 'rho_put', 'rho_recv', 'f_put', 'f_recv', 'f_repack',
             'band_tol', 'put_crew_mode',
             'coverage_days', 'safety_days', 'floor_lines',
             's_pick_store', 's_pick_ff', 's_put'), family='staffing', spec_from=None),
    *_knobs(('inbound_trailer_type', 'inbound_dock_doors',
             'inbound_lead_minutes', 'inbound_lead_spread',
             'inbound_local_policy', 'inbound_trailer_bound', 'inbound_plan_trace',
             'inbound_fill_span_days', 'inbound_fill_ratio',
             'inbound_standing_yard', 'inbound_crew_allocation',
             'inbound_yard_policy', 'inbound_dock_policy', 'inbound_door_team',
             'inbound_fee_threshold_days', 'inbound_urgency_horizon_days',
             'inbound_futuresight_batches',
             'inbound_unload_intercept', 'inbound_unload_weight_coef',
             'inbound_unload_volume_coef'), family='inbound'),
)

KNOB_BY_NAME: dict[str, Knob] = {k.name: k for k in KNOBS}

_COERCERS = {None: lambda v: v, 'bool': bool, 'or_one': lambda v: v or 1}


def apply_cli_overrides(args) -> None:
    """Write every declared knob's CLI value back onto CONFIG['global'].

    THE write-back.  Replaces ~35 hand-typed assignments plus two family loops; a knob that
    is declared above cannot now be accepted at the command line and then silently ignored
    for the whole run, which is the defect this registry exists to make unrepresentable.

    CONFIG is mutated in place and never rebound (see the module head): every accessor reads
    at call time, so this reaches the warehouse build, the channel build and every worker
    payload built after it.
    """
    g = CONFIG['global']
    for k in KNOBS:
        if k.apply == 'parent':
            continue
        v = getattr(args, k.name, None)
        if k.apply == 'if_set' and v is None:
            continue
        g[k.name] = _COERCERS[k.coerce](v)


def run_spec_record(args) -> dict:
    """Every declared knob a run records, each from the source it declares.

    `spec_from='config'` reads POST-overlay so a value that came from CONFIG rather than the
    command line is recorded too -- otherwise two runs with different docks look identical
    after the fact.  `spec_from='args'` reads the namespace, because a few knobs are recorded
    as TYPED rather than as resolved (`workers` resolves to 1 in CONFIG and records None).

    The caller adds the entries that are not plain knobs: argv, the sizing caps (which live on
    `args` and never in CONFIG), the era-conditional per-channel fills, the store composition,
    the nested staffing record, and the CLI-only run shaping.
    """
    g = CONFIG['global']
    out = {}
    for k in KNOBS:
        if k.spec_from == 'config':
            out[k.name] = g[k.name]
        elif k.spec_from == 'args':
            out[k.name] = getattr(args, k.name)
    return out


#: The names both restore sites put back.  Derived from the same flag the recorder reads, so
#: "recorded but never restored" is not expressible -- that defect shipped once (`put_swap_coef`)
#: and all 1,534 tests stayed green.
SPEC_KNOB_NAMES: tuple[str, ...] = tuple(k.name for k in KNOBS if k.spec_from)


#: Recorded knobs that `run_analysis._apply_run_shape` -- the SIXTH seam -- deliberately does
#: NOT restore, each with the reason.  That function is not derived from this registry and
#: should not be: every key there carries a bespoke absence rule ('or "v1"', 'bool(...)',
#: 'or 0', a plain get that must never be an `or` because a declared 0.0 is real, a skip-if-
#: None) encoding what a PRE-FIELD spec means for that one knob.  Flattening those into an
#: enum would risk the silent wrong-regime re-analysis the rules exist to prevent.
#:
#: So the registry guards the SET rather than the semantics: a knob that is recorded and then
#: neither restored nor named here fails `Tests/unit/test_knob_registry.py`, which is the drift
#: that could otherwise ship unnoticed.
ANALYSIS_EXEMPT: dict[str, str] = {
    'workers': 'pool size. Nothing about re-analysis depends on how many processes the run '
               'used, and a re-analysis picks its own --analysis-workers.',
    'couple_channels': 'the analysis reads coupling off the TREE, not CONFIG: the flag reaches '
                       'run_layout.json as `coupled`, which is what a downstream tool reads '
                       '(see couple_channels()). Its only two callers build the run, and '
                       'neither runs during a standalone re-analysis.',
}


#: be set from the command line.
STAFFING_KEYS: tuple[str, ...] = tuple(k.name for k in KNOBS if k.family == 'staffing')

#: The subset of STAFFING_KEYS that override an EXPECTED constant.  None = "take the
#: closed-form expectation" (`simconfig/expected_travel.py`), which is why `staffing_spec()`
#: does NOT default them from settings: an absent override is meaningful, and the
#: derivation (`workunits._derive_staffing_for_pair`) reads it as such.
CALIBRATION_KEYS: tuple[str, ...] = ('s_pick_store', 's_pick_ff', 's_put')

#: The keys that mean something ONLY under the calibrated era (ADR-0004: demand is the
#: declared input and the picking crew is solved from the confidence; the fill is derived
#: per bucket and floored at the minimum headroom) and the keys that mean something ONLY
#: flag-off (the declared pickers and their utilization target).  Each regime records the
#: other's keys as None -- `staffing_spec()` below -- so a record never shows a crew of 25
#: beside a derived one of 32, or a utilization target nothing read.
#: `run_simulation._check_era_flags` refuses the wrong regime's flags when typed.  The two
#: typed fills (`--store-fill` / `--ff-fill`) are the era's flag-off-only counterpart of
#: `min_headroom`; they are channel keys rather than staffing keys, so they are refused by
#: name there and recorded as None in the run spec under the era.
ERA_ONLY_KEYS: tuple[str, ...] = ('store_demand', 'ff_demand', 'first_time_confidence',
                                  'min_headroom')
FLAG_OFF_ONLY_KEYS: tuple[str, ...] = ('store_pickers', 'ff_pickers', 'rho_pick')

#: The scalar inputs' settings defaults, for a None restored from a pre-record run spec
#: (`run_analysis._apply_run_shape` writes None for every absent key): that run ran under
#: the defaults of its day, which is what these still are.  `floor_lines` is NOT here: its
#: None is a value ("solve it", `coverage.DEFAULT_FLOOR_LINES` flag-off) that the coverage
#: loop reads, exactly as the CALIBRATION_KEYS' None is.
_SCALAR_DEFAULTS: dict = {
    'rho_pick': _s.RHO_PICK, 'rho_put': _s.RHO_PUT, 'rho_recv': _s.RHO_RECV,
    'f_put': _s.F_PUT, 'f_recv': _s.F_RECV, 'f_repack': _s.F_REPACK,
    'band_tol': _s.BAND_TOL,
    'put_crew_mode': _s.PUT_CREW_MODE,
    'coverage_days': _s.COVERAGE_DAYS, 'safety_days': _s.SAFETY_DAYS,
}

#: The era-only scalars' settings defaults (a None under the era is the default, exactly as
#: `_SCALAR_DEFAULTS` resolves the shared ones).
_ERA_DEFAULTS: dict = {
    'store_demand': _s.STORE_DEMAND, 'ff_demand': _s.FF_DEMAND,
    'first_time_confidence': _s.FIRST_TIME_CONFIDENCE,
    'min_headroom': _s.MIN_HEADROOM,
}

#: Which global key each channel's pick crew is sized from (flag-off), and which its demand
#: is declared under (the era).  Module-level tables rather than keys in the channel dict,
#: so the channel dicts stay "settings references + structure" and each value has exactly
#: one live home.
_PICKERS_KEY: dict[str, str] = {'store': 'store_pickers', 'fulfillment': 'ff_pickers'}
_DEMAND_KEY: dict[str, str] = {'store': 'store_demand', 'fulfillment': 'ff_demand'}


def channel_pickers(name: str) -> int:
    """The DECLARED picker count of channel `name`, read from CONFIG at CALL time.

    The `recv_crew_spec` pattern: a flag writes CONFIG, a resume and a re-analysis restore
    into CONFIG, so CONFIG is the only place this may be read from.
    A None (a pre-record run spec restored by `run_analysis._apply_run_shape`) resolves to the
    leaf default -- that run fielded the compile-time constant of its day, which is what the
    constant still is.  An unknown channel is a KeyError, deliberately: a third channel needs
    its own declared knob, not a silent share of someone else's.

    UNDER THE ERA THIS IS A PLACEHOLDER (ADR-0004): the channel is built from it and then
    the derivation replaces the crew with the solved one (`workunits._derive_staffing_for_pair`),
    the record carries the picker keys as None, and every reader that needs a crew reads it
    through `staffing.channel_crew`, which prefers the derived block.  Nothing sizes from
    this value under the era.
    """
    key = _PICKERS_KEY[name]
    v = CONFIG['global'].get(key)
    if v is None:
        v = {'store_pickers': _STORE_PICKERS, 'ff_pickers': _FF_PICKERS}[key]
    n = int(v)
    if n < 1:
        raise ValueError(f'{key} must be a positive picker count; got {v!r}')
    return n


def channel_demand(name: str) -> float | None:
    """The DECLARED demand of channel `name` under the era, in the sampler's unit (the
    fraction of the section's SKUs drawn per day), read from CONFIG at call time; None
    flag-off, where the script's content is the channel's `batch.mean` and nothing reads
    this.  A None IN the key under the era (a spec restored by `_apply_run_shape` from a
    run that predates the declaration) resolves to the settings default."""
    if not era_on():
        return None
    key = _DEMAND_KEY[name]
    v = CONFIG['global'].get(key)
    v = _ERA_DEFAULTS[key] if v is None else float(v)
    if not (0.0 < v <= 1.0):
        raise ValueError(f'{key} must be a fraction of the section drawn per day in (0, 1]; '
                         f'got {v!r}')
    return v


def min_headroom() -> float | None:
    """The minimum free share every bin bucket keeps at setup under the era, read from CONFIG
    at call time; None flag-off, where the typed `store_fill()` / `ff_fill()` size the
    warehouse and nothing reads this.  A None IN the key under the era (a spec restored from
    a run that predates the derived fill) resolves to the settings default.  Refuses a value
    outside [0, 1): a headroom of one is a bucket sized for nothing."""
    if not era_on():
        return None
    v = CONFIG['global'].get('min_headroom')
    v = _ERA_DEFAULTS['min_headroom'] if v is None else float(v)
    if not (0.0 <= v < 1.0):
        raise ValueError(f'min_headroom must be a free share in [0, 1); got {v!r}')
    return v


def staffing_spec() -> dict:
    """The staffing record's INPUTS as a picklable dict, one entry per STAFFING_KEYS key.

    Inputs ONLY -- the derived block (batch content, the picking crew, put crew, receiving
    crew, expected utilization) is the pure module `Optimization/simconfig/staffing.py`, run
    by `workunits._derive_staffing_for_pair` after batch precompute, and is never a CONFIG
    key.  Read from CONFIG at call time for the reason every accessor in this file is, and
    carried in `workunits._shared` so a spawned worker can check that the crews it was
    handed are the crews the record declares.

    THE REGIME DECIDES WHICH KEYS ARE INPUTS (ADR-0004).  Flag-off the pickers resolve
    through `channel_pickers` (a None is the leaf default) and the ERA_ONLY_KEYS are None;
    under the era the demand and the confidence resolve to their defaults and the
    FLAG_OFF_ONLY_KEYS are None -- the crew is derived, `rho_pick` is not read.  The shared
    scalars resolve a None to their settings default (`_SCALAR_DEFAULTS`); `floor_lines`
    and the CALIBRATION_KEYS keep None, because "solve it" / "no override" is a value the
    coverage loop and the derivation read.
    """
    g = CONFIG['global']
    era = era_on()
    out: dict = {}
    for ch, k in _PICKERS_KEY.items():
        out[k] = None if era else channel_pickers(ch)
    for ch, k in _DEMAND_KEY.items():
        out[k] = channel_demand(ch) if era else None
    if era:
        v = g.get('first_time_confidence')
        out['first_time_confidence'] = (_ERA_DEFAULTS['first_time_confidence'] if v is None
                                        else float(v))
        out['min_headroom'] = min_headroom()
    else:
        out['first_time_confidence'] = None
        out['min_headroom'] = None
    for k, default in _SCALAR_DEFAULTS.items():
        v = g.get(k)
        out[k] = default if v is None else v
    if era:
        out['rho_pick'] = None
    v = g.get('floor_lines')
    out['floor_lines'] = None if v is None else float(v)
    for k in CALIBRATION_KEYS:
        v = g.get(k)
        out[k] = None if v is None else float(v)
    return out


def staffing_provenance(explicit) -> dict:
    """The staffing record's PROVENANCE block, one entry per input the regime reads.

    `declared` when a flag chose the value (`explicit` is the set of flag names typed on
    the command line), `assumed` when a settings default stood; the picker keys are
    `derived` under the era (ADR-0004), and a key the regime does not read -- None in
    `staffing_spec()` -- carries no provenance at all, because "assumed" would claim a
    settings default was in force when nothing read one.  `floor_lines` under the era is
    `derived` unless typed (the coverage loop solves it).
    """
    inputs = staffing_spec()
    era = era_on()
    out: dict = {}
    for k in STAFFING_KEYS:
        if era and k in _PICKERS_KEY.values():
            out[k] = 'derived'
        elif inputs[k] is None and k in (*ERA_ONLY_KEYS, *FLAG_OFF_ONLY_KEYS):
            continue
        elif k == 'floor_lines' and era and inputs[k] is None:
            out[k] = 'derived'
        else:
            out[k] = 'declared' if k in explicit else 'assumed'
    return out


def era_on() -> bool:
    """Whether this run is under the calibrated era: the drain-or-cap shift is on.

    ONE predicate for "derive the crews, refuse the legacy crew flags, price the script from
    the calibration record", read from CONFIG at call time.  The same key `work_day_spec()`
    carries to the worker as `drain_or_cap`, so the parent and the worker cannot disagree
    about which regime a run is in.
    """
    return bool(CONFIG['global'].get('shift_drain_or_cap'))


def couple_channels() -> bool:
    """Whether this run's two channels share one site: one dock, one receiving crew, one pool
    of putters — a COUPLED work unit per arm pair instead of one unit per channel leaf.

    A DECLARATION, never an inference. The obvious rule — "coupling rides the inbound flag" —
    does not hold: site-dock 06 couples every cell of the inbound campaign including its
    inbound-OFF pole, because the matrix's own deltas are what it publishes and an uncoupled
    anchor inside a coupled matrix would not be one. So the run says whether it is a site.

    Read from CONFIG at call time, like `era_on()` and for the same reason: a spawned worker
    re-imports this module and would get the pristine default. The flag reaches the run tree
    as `run_layout.json`'s `coupled`, which is what a downstream tool reads (a coupled root is
    refused by `run_restock_selection.select`, whose validity argument IS channel independence).
    """
    return bool(CONFIG['global'].get('couple_channels'))


def k_pickers() -> int:
    """The store channel's DECLARED picker count, read at call time (the diagnostics' entry
    point).  Flag-off only in meaning: under the era this is the placeholder, not the solved
    crew -- a probe that models an era run's crew reads `staffing.channel_crew` off that
    run's staffing record instead."""
    return channel_pickers('store')


def store_restocks() -> tuple:
    """The store channel's restock-rule subset, read at call time."""
    return CONFIG['channels']['store']['restocks']


def shift_seconds() -> float:
    """Shift length in seconds, read at call time.  Default: an eight-hour day.

    A REPORTING FRAME, not a scheduler.  It labels `work_events.shift_index`; nothing
    dispatches against it, work does not pause at the whistle, and no task is split at a
    boundary -- see `Warehouse.kernel.timeline.shift_index`.
    """
    return float(CONFIG['global'].get('shift_seconds') or _DEFAULT_SHIFT_SECONDS)


def work_day_spec() -> dict:
    """The working day as a picklable record: `{seconds, releases_per_day, cut_at_day_end}`.

    Read at call time and handed to the worker in its payload rather than re-imported there:
    a spawned worker re-imports this module and would get pristine defaults, so a day
    configured on the command line would be accepted and then silently ignored.

    ONE accessor and ONE payload key for three values, because three of each is how two of
    them end up disagreeing.  `seconds` falls back to the shift length so a run that asks for
    a cut without naming a day gets the eight hours it already reports against.
    """
    g = CONFIG['global']
    return {
        'seconds': float(g.get('work_day_seconds') or shift_seconds()),
        'releases_per_day': g.get('releases_per_day'),
        'cut_at_day_end': bool(g.get('cut_at_day_end')),
        'roll_over_unpicked': bool(g.get('roll_over_unpicked')),
        # The drain-or-cap shift rides the SAME record: one accessor, one payload
        # key, so the mode and the day it caps against cannot disagree in a worker.
        'drain_or_cap': bool(g.get('shift_drain_or_cap')),
    }


def recv_crew_spec(size: int | None = None) -> dict | None:
    """The receiving crew as a picklable record, or **None** when there is no crew.

    `size` is the DERIVED crew under the calibrated era (`staffing.derive`), handed in by
    the caller that holds the derived block; None reads the declared `recv_crew_size` key,
    which is every flag-off run.  Derived values are never CONFIG keys, so the derivation
    cannot leak into a flag-off run by leaving a key behind.

    None rather than an empty dict, and that distinction is the feature's off-switch. The
    worker tests `args.get('recv_crew') is None` and skips its whole receive block: no Crew,
    no Workers, no clocks, no Dock, no WorkDay. The no-op is therefore "nothing was
    constructed" rather than "an empty thing exists" -- which matters because an empty dock
    would still be an object something could fold into a clock or a snapshot.

    COPIED FROM `work_day_spec`, NOT FROM the `put_crew_spec` of its day. That one read
    `_s.PUT_CREW_SIZE` directly with no `CONFIG['global']['put_crew_*']` key at all -- so a
    CLI flag, which writes CONFIG, would have been accepted and ignored forever, and a
    standalone re-analysis would have sized against this checkout's `settings.py` instead
    of the run's own. Reading CONFIG at CALL time is what makes the other three seams
    reachable.  (`put_crew_spec` has since been fixed the same way.)

    `day_seconds` is resolved with an explicit `is not None`, never `or`: `--recv-day-seconds
    0` means "the crew has no day today", and `or` would silently turn that into "no whistle
    at all" -- the inverse. (The parser rejects 0 as well, so this is the second of two
    guards on the same mistake.)

    Speeds come from the PUT crew's foot table. Not laziness: an unload has no travel term,
    so no speed is consumed by the cost model at all, and declaring receiving-specific
    constants would assert a distinction the model cannot express. They are carried only so a
    `Crew` can be built and its rows labelled.
    """
    g = CONFIG['global']
    size = int(g.get('recv_crew_size') or 0) if size is None else int(size)
    if size < 1:
        return None
    day = g.get('recv_day_seconds')
    return {
        'size': size,
        'day_seconds': None if day is None else float(day),
        'day_origin': float(g.get('recv_day_origin') or 0.0),
        # No mode knob -- see settings.RECV_CREW_SIZE. `foot` labels the rows honestly:
        # a receiver walks merchandise off a trailer.
        'mode': 'foot',
        'x_speed': _s.PUT_FOOT_X,
        'y_speed': _s.PUT_FOOT_Y,
    }


def _plan_trace_every(raw):
    """Normalize INBOUND_PLAN_TRACE: None (off) or a positive int cadence in batches.

    A probe instrument (`.scratch/inbound-throughput/` 03), never a policy: a traced cell
    ranks byte-identically to an untraced one and only writes a sidecar.  `True` is refused
    rather than read as 1, for the same reason the futuresight window refuses bools."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f'INBOUND_PLAN_TRACE must be None or a positive integer cadence '
                         f'in batches (trace every Nth batch); got {raw!r}')
    return raw


def fill_spec() -> dict | None:
    """The FILL TRIAL's record (CONTEXT.md: Fill trial), or None -- every run that is not one.

    `{'span_days', 'ratio'}`: the site days the fill crews are derived over, and the arrival
    pressure the declaration is dispatched at (dispatch rate over the seated receiving rate).
    Both are declared; the crews, the dispatch rate and the fill's length are derived from
    them (`staffing.derive_fill`, the worker's `_FillDispatch`).

    Refused rather than degraded, every one of them a run that would complete under the fill's
    name without being one:
      * a fill with no trailer pipeline -- nothing would ever arrive, and the pick stage would
        never start;
      * a fill without the standing yard -- v1 drains everything at once, so there is no
        queue for the unloading order to act on;
      * a fill under a trailer bound -- refuted for ranking (memory
        `the-trailer-bound-buys-wall-with-discrimination`) and the fill trial's cells exclude
        it by decision (inbound-throughput Q23);
      * a ratio outside (0, 1) -- at or above 1 the yard grows without bound and the policies
        converge on arrival order (Q15/Q20), at or below 0 nothing is dispatched.
    The coupled era is checked where the flags are (`run_simulation._check_era_flags`).
    """
    g = CONFIG['global']
    span = g.get('inbound_fill_span_days')
    if span is None:
        return None
    if isinstance(span, bool) or not isinstance(span, (int, float)) or not span > 0:
        raise ValueError(f'INBOUND_FILL_SPAN_DAYS must be None (no fill) or a positive number '
                         f'of site days; got {span!r}')
    ratio = g.get('inbound_fill_ratio')
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) \
            or not 0.0 < float(ratio) < 1.0:
        raise ValueError(f'INBOUND_FILL_RATIO must lie strictly between 0 and 1 (arrivals '
                         f'over the seated drain rate); got {ratio!r}')
    if not g.get('inbound_trailer_type'):
        raise ValueError(
            'a FILL TRIAL needs a trailer pipeline (INBOUND_TRAILER_TYPE): the declaration '
            'arrives by trailer, so with none nothing would ever land and the pick stage would '
            'never start. An inbound-OFF cell places nothing in a fill (inbound-throughput Q23)')
    if not g.get('inbound_standing_yard'):
        raise ValueError(
            'a FILL TRIAL needs INBOUND_STANDING_YARD: without real doors the v1 drain unloads '
            'everything at once and there is no queue for an unloading policy to order')
    _overlays = sorted(k for k in ('inbound_unload_intercept', 'inbound_unload_weight_coef',
                                   'inbound_unload_volume_coef') if g.get(k) is not None)
    if _overlays:
        raise ValueError(
            f'a FILL TRIAL refuses the unload-cost overlays {_overlays}: the fill crews and the '
            f'dispatch rate are priced at the put-away-derived unload law '
            f'(`staffing.declared_work`), so a dock charging a different price would press the '
            f'yard at some ratio other than the declared one -- above 1, a queue that never '
            f'clears')
    if g.get('inbound_trailer_bound') is not None:
        raise ValueError(
            f'a FILL TRIAL refuses INBOUND_TRAILER_BOUND ({g["inbound_trailer_bound"]!r}): the '
            f'bound was refuted as a ranking device and the fill trial excludes it by '
            f'decision (inbound-throughput Q23)')
    return {'span_days': float(span), 'ratio': float(ratio)}


def _futuresight_batches(raw):
    """Normalize INBOUND_FUTURESIGHT_BATCHES for the spec: None | 'all' | int >= 0.

    Anything else raises here, at spec build, rather than reaching a worker: the knob
    denominates a batch COUNT, so a negative, fractional, bool, or unrecognized-string
    value is a config error, never a silent clamp — and the error names the knob
    (a bare `int('oracle')` message would refuse loudly but signpost nothing)."""
    if raw is None or raw == 'all':
        return raw
    try:
        if isinstance(raw, bool):        # int(True) == 1 would pass the checks below
            raise ValueError
        w = int(raw)
        if w != raw or w < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(
            f"INBOUND_FUTURESIGHT_BATCHES must be None, 'all', or a non-negative "
            f'integer count of script batches; got {raw!r}') from None
    return w


#: Every `CONFIG['global']` key `inbound_spec()` reads — the inbound family's SHAPE, in one
#: place, so its CLI flags, its run-spec record and both restore sites are derived from one
#: list rather than three hand-maintained ones.  That is what seams 3 and 4 are: a knob missing
#: from any of them is accepted, logged, and then ignored (see `settings.py`'s four-seam note
#: and the fifth, `workunits._shared`, which the whole family already crosses as one record).
#:
#: Order is the order the flags and the run-spec record are emitted in, so it is read by a
#: human as much as by the loops.
INBOUND_KEYS: tuple[str, ...] = tuple(k.name for k in KNOBS if k.family == 'inbound')


def inbound_lead_law() -> dict | None:
    """The trailer pipeline's LEAD LAW alone -- `{'trailer_type', 'lead_s', 'lead_sigma'}`,
    or **None** when no trailer type is named (the pipeline is off, structurally).

    The three keys `inbound_spec()` reads for the lead, factored out because the coverage
    record needs them BEFORE the receiving crew exists: `era_coverage.lead_block` derives
    the day-grid transit at setup (`coverage.transit_day_law`), and `inbound_spec()`'s
    "a yard nobody can unload" guard would refuse that read under the era, where the crew
    is derived only after the fixed point has declared the levels.  No guard here: this
    accessor answers what the lead IS; `inbound_spec()` still refuses every contradiction
    the moment the worker payload is built.  Reads CONFIG at call time; the median is
    converted from minutes to seconds here and nowhere else (the same conversion
    `inbound_spec()` records as `lead_s`).
    """
    g = CONFIG['global']
    ttype = g.get('inbound_trailer_type')
    if not ttype:
        return None
    return {'trailer_type': str(ttype),
            'lead_s': float(g.get('inbound_lead_minutes') or 0.0) * 60.0,
            'lead_sigma': float(g.get('inbound_lead_spread') or 0.0)}


def inbound_spec(recv_crew_size: int | None = None) -> dict | None:
    """The trailer pipeline's configuration as a picklable record, or **None** for off.

    `recv_crew_size` is the DERIVED receiving crew under the calibrated era
    (`staffing.derive`), handed in by the caller that holds the derived block -- the
    `recv_crew_spec(size=)` pattern, and for the same reason: derived values are never
    CONFIG keys.  None reads the declared `recv_crew_size` key, which is every flag-off
    run.  It is read by exactly one guard, the standing yard's "a yard nobody can unload"
    refusal below; nothing else about the record depends on it.

    None rather than a dict with a flag, for the same reason `recv_crew_spec` returns None:
    the worker tests `args.get('inbound') is None` and skips the whole build -- no
    TrailerTransit, no trailer objects, and the manager keeps the batch lead queue it
    constructed for itself, byte-identically.

    Reads CONFIG at CALL time (the `recv_crew_spec` pattern, never `put_crew_spec`'s
    settings snapshot).  The lead MEDIAN is authored in MINUTES and converted to the sim's
    seconds exactly once, here -- the minutes-at-the-surface decision.  The SPREAD is
    dimensionless and crosses as-is; the draw itself lives at trailer creation
    (`Inbound.transit.TrailerTransit.lead_for`), keyed by the world seed carried below,
    so no RNG object ever has to cross the worker payload.

    THE STANDING YARD'S CONTRADICTIONS FAIL HERE, LOUDLY.  `INBOUND_STANDING_YARD` with
    no trailer type is a yard with no trailers; with no receiving crew it is a yard
    nobody can ever unload -- merchandise would stand deferred forever, the run would
    complete, and nothing would raise.  And a non-fifo yard/dock policy WITHOUT the
    standing yard is a policy nothing reads: the run would complete as v1 fifo under
    the policy's name -- the fake-arm hazard, worn as configuration.  So is a lead
    SPREAD without the standing yard, or over a zero median (see the guard below).  All
    are configuration errors, never a silent no-op: the flag's OFF state is the only
    inert one.
    """
    g = CONFIG['global']
    ttype = g.get('inbound_trailer_type')
    standing = bool(g.get('inbound_standing_yard'))
    # THE FILL TRIAL, validated ABOVE the no-trailer return: a fill with no trailer type
    # would otherwise come back as `None` -- an inbound-off run under the fill's name.
    _fill = fill_spec()
    lead_min = float(g.get('inbound_lead_minutes') or 0.0)
    lead_sigma = float(g.get('inbound_lead_spread') or 0.0)
    # The lead guards sit ABOVE the trailer-type return: with no type there is no standing
    # yard either (the next branch enforces that), so a spread here is unread by definition
    # -- and returning None on it would be the exact silent no-op the doctrine refuses.
    if lead_sigma > 0.0:
        if not standing:
            raise ValueError(
                f'INBOUND_LEAD_SPREAD ({lead_sigma}) is the standing yard\'s knob and is '
                f'half-read without INBOUND_STANDING_YARD: v1\'s dock ranks by dispatch '
                f'seq, so the arrival-batch shifts would land and the order scrambling '
                f'-- the whole point of a spread -- would not.  Set the flag or clear '
                f'the spread')
        if lead_min <= 0.0:
            raise ValueError(
                f'INBOUND_LEAD_SPREAD ({lead_sigma}) over a zero INBOUND_LEAD_MINUTES: '
                f'the lognormal is median * exp(sigma * Z), so a zero median makes every '
                f'draw zero and the spread silently degenerates to no spread at all.  '
                f'Set a median or clear the spread')
    if not ttype:
        if standing:
            raise ValueError(
                'INBOUND_STANDING_YARD is set but INBOUND_TRAILER_TYPE is None -- a '
                "standing yard with no trailers is a config contradiction; name a type "
                "('53'/'28') or clear the flag")
        return None
    # The crew that unloads the yard: the derived one when the caller holds it, else the
    # declared key.  Reading the declared key ALONE refused every era launch with the yard
    # on (inbound-optimization, "Verify the derived receiving crew"): under the era that
    # key is the flag-off input the derivation never writes -- the run spec records it as
    # 0 -- while the crew the dock is actually built with is `derived.receiving.crew`
    # (department-calibration, "Design the staffing record", decision 4).
    _crew = (int(g.get('recv_crew_size') or 0) if recv_crew_size is None
             else int(recv_crew_size))
    if standing and _crew < 1:
        _src = 'declared RECV_CREW_SIZE' if recv_crew_size is None else 'derived receiving crew'
        raise ValueError(
            f'INBOUND_STANDING_YARD needs a receiving crew ({_src} read {_crew}; flag-off '
            f'RECV_CREW_SIZE >= 1, under the era a derived crew >= 1): unloading a staged '
            f'trailer is crew labour, and with no crew the yard would stand forever with '
            f'nothing raising')
    allocation = str(g.get('inbound_crew_allocation') or 'split')
    if allocation not in ('split', 'merged'):
        raise ValueError(f'unknown INBOUND_CREW_ALLOCATION {allocation!r}; '
                         f"known: 'split', 'merged'")
    yard_policy = str(g.get('inbound_yard_policy') or 'fifo')
    dock_policy = str(g.get('inbound_dock_policy') or 'fifo')
    if not standing and (yard_policy, dock_policy) != ('fifo', 'fifo'):
        raise ValueError(
            f'INBOUND_YARD_POLICY/INBOUND_DOCK_POLICY '
            f'({yard_policy!r}/{dock_policy!r}) are the standing yard\'s knobs and '
            f'are UNREAD without INBOUND_STANDING_YARD: the run would complete as '
            f'v1 fifo under the policy\'s name, nothing raising.  Set the flag or '
            f'clear the knobs')
    # The door-team cap: trailer physics, and the standing yard's alone.  v1's release()
    # hands the whole drain over at once and never reaches the split unload, so a cap
    # declared without the flag would be recorded, carried across the worker boundary and
    # then read by nobody -- a run whose spec says 'ten receivers per door' and whose dock
    # put every receiver on one trailer, with nothing raising.  Same refusal as the two
    # policies above, for the same reason.
    door_team = g.get('inbound_door_team')
    if door_team is not None:
        door_team = int(door_team)
        if door_team < 1:
            raise ValueError(
                f'INBOUND_DOOR_TEAM ({door_team}) must be at least 1 receiver: a cap of '
                f'zero is a dock nobody may work at, which would defer every trailer '
                f'forever rather than cap anything.  Clear it for uncapped')
        if not standing:
            raise ValueError(
                f'INBOUND_DOOR_TEAM ({door_team}) is the standing yard\'s knob and is '
                f'UNREAD without INBOUND_STANDING_YARD: v1\'s release() hands the whole '
                f'drain over at once and never deals door teams, so the run would '
                f'complete uncapped under the cap\'s name.  Set the flag or clear the cap')
    return {
        'trailer_type': str(ttype),
        'doors': int(g.get('inbound_dock_doors') or 4),
        'lead_s': lead_min * 60.0,
        # The lognormal's shape and its key.  `lead_seed` is the WORLD seed, not a knob of
        # its own: a lead schedule is a fact about the world all arms share, so "same
        # --seed-world = same warehouse, catalogue and leads" stays one sentence.  A
        # lead-realization sweep, if ever wanted, adds INBOUND_LEAD_SEED here and nowhere
        # else -- the draw is stateless, so there is no generator to re-plumb.
        'lead_sigma': lead_sigma,
        'lead_seed': int(seed_world()),
        'local_policy': str(g.get('inbound_local_policy') or 'fifo'),
        'bound': g.get('inbound_trailer_bound'),
        # The plan-trace probe's cadence: None = off, else trace every Nth batch.
        # Validated here like every count, so a typo raises at spec build.
        'plan_trace': _plan_trace_every(g.get('inbound_plan_trace')),
        # The fill trial: None = off (every run today), else `fill_spec()`'s record.
        'fill': _fill,
        # The standing yard.  `standing` False keeps every key inert; the driver binds
        # the v1 transit and none of the rest is read.
        'standing': standing,
        'allocation': allocation,
        'yard_policy': yard_policy,
        'dock_policy': dock_policy,
        # The door-team cap, already validated above.  None = uncapped; the dealing rule
        # reads it in BOTH allocation modes, because the cap is the trailer's, not the
        # dealing rule's.
        'door_team': door_team,
        # The gate's two days-denominated knobs.  Explicit None tests, not `or`:
        # a 0.0 threshold (everything overdue from arrival) is a legal sweep point
        # that `or` would silently revert to the default.
        'fee_threshold_days': (2.0 if g.get('inbound_fee_threshold_days') is None
                               else float(g['inbound_fee_threshold_days'])),
        'urgency_horizon_days': (0.0 if g.get('inbound_urgency_horizon_days') is None
                                 else float(g['inbound_urgency_horizon_days'])),
        # The futuresight window, in script batches: None = inert, 'all' = the oracle
        # w=inf (the string sentinel a run spec records honestly), else a non-negative
        # int -- 0 is a legal pole (a futuresight arm that sees nothing ahead), so the
        # None test is explicit like the days-knobs above.  int() would silently
        # truncate a fractional value, so a non-integral number raises instead: a
        # window is a COUNT of batches, and 2.5 of them is a config error.
        'futuresight_batches': _futuresight_batches(
            g.get('inbound_futuresight_batches')),
        # The unload cost's own coefficients; None = the put-away default BY REFERENCE
        # (UnloadCost's field defaults), so unset changes no archive row.
        'unload_intercept': g.get('inbound_unload_intercept'),
        'unload_weight_coef': g.get('inbound_unload_weight_coef'),
        'unload_volume_coef': g.get('inbound_unload_volume_coef'),
    }


def put_queues_spec() -> dict | None:
    """The split put-away configuration as a picklable record, or **None** for one queue.

    None rather than a dict with `split=False`, for the same reason `recv_crew_spec` returns
    None: the worker tests `args.get('put_queues') is None` and skips the assignment
    entirely, so the no-op is "the manager keeps the `single_queue()` it built in
    `__init__`" rather than "something reconstructed the default".

    Reads CONFIG at CALL time. Copying the `put_crew_spec` of its day would have been the
    natural move and was a trap: it read `_s.PUT_CREW_SIZE` directly, there was no
    `CONFIG['global']['put_crew_*']` key at all, and a CLI flag writing CONFIG would
    therefore have been accepted and ignored forever.  (Since fixed.)

    THE CREW SIZES ARE NOT A DETAIL. Each queue gets its own crew and its own clock, so
    three queues of size 1 is three putters where the default is one -- roughly 3x the
    put-away throughput before any other difference. A sweep comparing split against single
    must size these against the single-queue total, or it is measuring headcount.

    Staging is carried as-is including None: `None` means an unbounded floor, and that is a
    meaningfully different configuration from a large one, because it is what decides
    whether the held list and the refill loop ever execute at all.
    """
    g = CONFIG['global']
    if not g.get('put_queue_split'):
        return None
    return {
        'cart_crew': int(g.get('put_cart_crew') or 1),
        'pallet_crew': int(g.get('put_pallet_crew') or 1),
        'ff_crew': int(g.get('put_ff_crew') or 1),
        # Explicit `is None` tests, never `or`: `--put-pallet-staging 1` is the tightest
        # meaningful floor and `or` would turn it into "unbounded", the exact inverse.
        'cart_staging': (None if g.get('put_cart_staging') is None
                         else int(g['put_cart_staging'])),
        'pallet_staging': (None if g.get('put_pallet_staging') is None
                           else int(g['put_pallet_staging'])),
        'ff_staging': (None if g.get('put_ff_staging') is None
                       else int(g['put_ff_staging'])),
        'swap_coef': float(g.get('put_swap_coef') or 0.0),
    }


def put_crew_spec(size: int | None = None) -> dict:
    """The put crew as a picklable record: `{size, mode, x_speed, y_speed}`.

    Reads CONFIG at CALL time -- `put_crew_size` / `put_crew_mode` -- like every other
    accessor in this file.  It used to read `_s.PUT_CREW_SIZE` / `_s.PUT_CREW_MODE`
    directly with no CONFIG key at all, which was THE trap the other accessors' docstrings
    warn about: a flag writing CONFIG would have been accepted and ignored forever, and a
    standalone re-analysis would have sized against this checkout's settings instead of
    the run's own.  Closed by "Design the staffing record", decision 7.

    `size` is the DERIVED crew under the calibrated era (`staffing.derive`), handed in by
    the caller that holds the derived block; None reads the declared key, which is every
    flag-off run.  A None IN the key (a pre-field run spec restored by
    `run_analysis._apply_run_shape`) resolves to the settings default -- the one walker
    every such run fielded.

    The speed comes from the crew's MODE, which is the point of having a mode: a crew
    labelled `foot` costed at the store's machine speed would write rows whose mode and
    duration contradict each other.  The mode is a DECLARED staffing input (it rides
    STAFFING_KEYS), so it is read through `staffing_spec()`'s resolution.
    """
    g = CONFIG['global']
    mode = g.get('put_crew_mode') or _s.PUT_CREW_MODE
    if mode not in ('foot', 'machine'):
        raise ValueError(f"put_crew_mode must be 'foot' or 'machine'; got {mode!r}")
    x, y = ((_s.PUT_MACHINE_X, _s.PUT_MACHINE_Y) if mode == 'machine'
            else (_s.PUT_FOOT_X, _s.PUT_FOOT_Y))
    if size is None:
        v = g.get('put_crew_size')
        size = _s.PUT_CREW_SIZE if v is None else int(v)
    if int(size) < 1:
        raise ValueError(f'put crew size must be a positive count; got {size!r}')
    return {'size': int(size), 'mode': mode, 'x_speed': x, 'y_speed': y}


def crew_cost_spec() -> dict:
    """The other crews' PRICE as a picklable record:
    `{put_intercept_scale, put_item_ratio, recv_intercept_scale}`.

    Put-away and receiving keep picking's cost shape and coefficients by reference
    (`PutawayCost.from_pick`, `UnloadCost.from_putaway`); these three scalars are the only
    place their numbers may differ.  Read from CONFIG at CALL time -- never from `_s.`
    directly, which was the trap `put_crew_spec` above carried until the era build -- and handed to the
    worker in its payload (`workunits._shared['crew_cost']`), because a spawned worker
    re-imports this module and would get pristine defaults.

    A None (a pre-field run spec restored by `run_analysis._apply_run_shape`) resolves to the
    kernel default: that run priced its put crew at the pre-charge literal, which no scale
    can reproduce, so the default is the honest reconstruction of "no scale was declared".
    """
    from Warehouse.kernel.cost_model import (
        DEFAULT_PUT_INTERCEPT_SCALE, DEFAULT_PUT_ITEM_RATIO, DEFAULT_RECV_INTERCEPT_SCALE)
    g = CONFIG['global']

    def _f(key, default):
        v = g.get(key)
        return float(default if v is None else v)

    return {
        'put_intercept_scale':  _f('put_intercept_scale', DEFAULT_PUT_INTERCEPT_SCALE),
        'put_item_ratio':       _f('put_item_ratio', DEFAULT_PUT_ITEM_RATIO),
        'recv_intercept_scale': _f('recv_intercept_scale', DEFAULT_RECV_INTERCEPT_SCALE),
    }


def store_fill() -> float:
    """The store's sizing fill headroom, read from CONFIG at CALL time.

    Was `_INITIAL_FILL`, an import-time scalar — which quietly broke this module's own rule
    that CONFIG is the single source of truth and is mutated in place: a runtime override
    (a CLI flag, a test) never reached the snapshot, and `sim_assets` writes this value into
    the warehouse DB as `target_fill`, so the run's own provenance recorded the stale
    number.

    FLAG-OFF ONLY since "Derive the fill headroom from the fragmentation": under the era the
    planner is handed a per-bucket hold map derived from the stationary fragmentation and
    floored at `min_headroom()`, `--store-fill` is refused, and the run spec records this
    key as None.  The value still stands in CONFIG there, read by nothing that sizes.
    """
    return CONFIG['channels']['store']['fill']


def ff_fill() -> float:
    """The fulfillment regime's fill headroom, read at call time.  Same rule as store_fill,
    flag-off only for the same reason."""
    return CONFIG['channels']['fulfillment']['fill']


def _checkpoint_every(n_batches: int) -> int:
    """Batches between per-strategy checkpoints (floor(n_batches * checkpoint_frac), ≥1) —
    matches the legacy ``max(1, N_BATCHES // 10)`` cadence at the default 0.1 fraction."""
    return max(1, int(n_batches * CONFIG['global']['checkpoint_frac']))


def regime_sizing_from_config() -> dict:
    """Assemble the per-regime warehouse-sizing dict from CONFIG (both regimes sized from the
    declared levels; store composition + caps; ff aisle shape), each with its own fill
    headroom -- the share of each bucket those levels occupy at setup.  Used
    by both the run (main) and the analysis rebuild (run_analysis) so the warehouse SHAPE — ff
    aisle layout + total_bins — matches; sizing the two paths differently would misgroup ff
    aisle stats and skew churn %."""
    return {name: {**CONFIG['channels'][name]['sizing'], 'fill': CONFIG['channels'][name]['fill']}
            for name in ('store', 'fulfillment')}


# ── logging ────────────────────────────────────────────────────────────────────

def _setup_logging(log_path: str) -> logging.Logger:
    log = logging.getLogger('comparison')
    log.setLevel(logging.INFO)
    # %(name)-22s gives a fixed-width column so the '<cell> <strategy>' worker labels align with
    # the main-process 'comparison' label in the same log file (widened from 14 to fit the cell tag).
    fmt = logging.Formatter(
        '%(asctime)s  %(name)-22s  %(message)s',
        datefmt='%H:%M:%S',
    )
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


# ── flat pool helpers ──────────────────────────────────────────────────────────

# Cart types a config entry may name via a 'cart' key (default: the store cart).
_CART_TYPES = {'StoreCart': StoreCart, 'FulfillmentCart': FulfillmentCart}

#: The field names `PickConfig` accepts, so a config dict can be filtered rather than
#: transcribed key-by-key.  Derived, never listed: a hand-written list is how
#: `Diagnostics/bucket_fill` came to be missing `cart`, `one_way` and `scheduler`.
#: `run_map_precompute` derives the same set for its archived-config rebuild.
_PICK_CONFIG_FIELDS = frozenset(f.name for f in _dataclass_fields(PickConfig))


def _config_name(cfg: dict) -> str:
    """A config's directory/identity name (explicit 'name', else a coeff fingerprint)."""
    return cfg.get('name') or (
        f"w{cfg.get('pick_weight_coef',1.1)}_v{cfg.get('pick_volume_coef',1e-3)}"
        f"_i{cfg.get('pick_intercept',1.0)}_c{cfg.get('cart_swap_coef',10.0)}"
    )


def _build_pick_cfg(cfg: dict, *, num_pickers: int, default_cart=StoreCart) -> PickConfig:
    """Turn a config dict (store or fulfillment) into a PickConfig.

    The one canonical dict→PickConfig conversion shared by both channels' sweeps.  Callers
    pass the channel's DECLARED picker count (`channel_pickers(name)`; a config dict's own
    'num_pickers', if present, must agree -- `workunits._channel_runs_for` raises otherwise) and
    the channel's default_cart (StoreCart / FulfillmentCart); a 'cart' key overrides it.

    A MISSING key falls through to `PickConfig`'s own dataclass default, and that is the whole
    point of the shape here.  This function used to restate a fallback per key — a SECOND
    default set, which had drifted from the first: `pick_weight_coef` 1.1 against the
    dataclass's 0.02 (55x), `pick_volume_coef` 1e-3 against 1e-4 (10x), `cart_swap_coef` 10.0
    against 5.0 (2x).  Nothing caught it because every registered pick-config declares all
    three, so neither set was ever exercised on the run path — and `run_map_precompute`
    rebuilds a `PickConfig` from an ARCHIVED leaf config by field-filtering, which has always
    taken the dataclass defaults.  Two default sets and two reconstruction paths is a silent
    55x waiting for the first archive vintage that omits a key.

    Keys the dataclass does not declare (`name`) are dropped; `cart` is resolved separately
    because the dict stores its NAME and the dataclass wants the class.
    """
    kw = {k: v for k, v in cfg.items() if k in _PICK_CONFIG_FIELDS}
    kw['num_pickers'] = num_pickers
    kw['cart'] = _CART_TYPES.get(cfg['cart'], default_cart) if 'cart' in cfg else default_cart
    return PickConfig(**kw)
