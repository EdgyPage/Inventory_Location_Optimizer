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
from dataclasses import fields as _dataclass_fields

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
def _load_env(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _, _val = _line.partition('=')
            _key = _key.strip()
            _val = _val.strip()
            # Strip optional r"..." / r'...' raw-string notation and plain quotes
            if _val.startswith(('r"', "r'")):
                _val = _val[2:].rstrip('"').rstrip("'")
            else:
                _val = _val.strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

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
# Physical aisle dimensions: 50 pallet-width columns × 10 extra_large-height levels.
# Actual bin counts per aisle depend on unit type and size distribution.
_AISLE_W = aisle_width_for(50)    # 50 × 48 = 2400 physical units
_AISLE_H = aisle_height_for(10)   # 10 × 48 = 480 physical units

# Picker-pool defaults per channel live in Optimization/simconfig/constants.py (imported above as
# _STORE_PICKERS / _FF_PICKERS) so the self-registering pick-config modules can reference them
# without a circular import.  A pick-config entry may still override its own 'num_pickers'.


def _clean_path(val: str) -> str:
    """Strip r\"...\" / r'...' notation or plain quotes from an env-var path value.

    Applied after os.getenv so that values set directly in the Windows session
    environment (with literal r\"...\" text) are normalised the same way as
    values parsed from the .env file.
    """
    if val.startswith(('r"', "r'")):
        return val[2:].rstrip('"').rstrip("'")
    return val.strip('"').strip("'")

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
        'max_skus'        : _s.MAX_SKUS,   # input-catalog cap (preserves the store/ff mix)
        # Batch-sampler VERSION — a results ERA, not a tuning knob.  'v2' (the Fenwick
        # sampler, introduced e7c9ed9, adopted as default 2026-08-20) draws the same
        # weight model as 'v1' in O((k·(1+partners))·log N) instead of O(k·N) — measured
        # 0.83s -> 0.05s per batch at 40k SKUs, 21.6s -> 0.48s at 160k — but its float
        # grouping differs, so its batch SEQUENCE differs: v2 runs are not row-comparable
        # with the pre-2026-08-20 archive.  `--sampler v1` reproduces that archive
        # exactly (byte-identical, digest-proven).  Batch caches are fingerprinted apart
        # per sampler, so the two eras can never contaminate each other.
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
        'inbound_trailer_type'  : _s.INBOUND_TRAILER_TYPE,
        'inbound_dock_doors'    : _s.INBOUND_DOCK_DOORS,
        'inbound_lead_minutes'  : _s.INBOUND_LEAD_MINUTES,
        'inbound_lead_spread'   : _s.INBOUND_LEAD_SPREAD,
        'inbound_global_policy' : _s.INBOUND_GLOBAL_POLICY,
        'inbound_local_policy'  : _s.INBOUND_LOCAL_POLICY,
        'inbound_trailer_bound' : _s.INBOUND_TRAILER_BOUND,
        # The standing yard (real doors, split yard/dock priorities, door-team crews,
        # own unload coefficients).  All riding inbound_spec below, so the whole family
        # crosses the worker payload as one record.
        'inbound_standing_yard'    : _s.INBOUND_STANDING_YARD,
        'inbound_crew_allocation'  : _s.INBOUND_CREW_ALLOCATION,
        'inbound_yard_policy'      : _s.INBOUND_YARD_POLICY,
        'inbound_dock_policy'      : _s.INBOUND_DOCK_POLICY,
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
    },
    'channels': {
        'store': {
            'regime'     : STORE,
            'configs'    : STORE_CONFIGS,
            'num_pickers': _s.STORE_PICKERS,
            'pick_mode'  : _s.STORE_PICK_MODE,
            'restocks'   : restocks_for('store'),
            'cart'       : _s.STORE_CART,
            'seed_offset': 0,
            'batch'      : {'mean': _s.STORE_BATCH_MEAN, 'std': _s.STORE_BATCH_STD},
            'fill'       : _s.STORE_FILL,
            # aisle_split (optional): cut each aisle into k shorter segments (~depth/k) with a
            # capacity_loss modeling throughway construction.  None/{'k':1} = no split
            # (byte-identical).  e.g. {'k': 2, 'capacity_loss': 0.15}.
            'sizing'     : {'mode': 'demand', 'min_bins': None, 'max_bins': None,
                            'max_aisles': None, 'composition': None, 'aisle_split': None},
            # Velocity zoning: restrict each unit's viable aisles to its velocity band
            # ("like-with-like"), composing with every arm.  enabled=False = byte-identical.
            # mode 'equal' (default, equal-count bands) | 'abc' (manual A/B/C by demand-mass
            # thresholds; aisles allocated by band footprint so the hot band is a small fraction).
            'velocity_zoning': _deepcopy(_s.ZONING_OFF),
        },
        'fulfillment': {
            'regime'     : FULFILLMENT,
            'configs'    : FULFILLMENT_CONFIGS,
            'num_pickers': _s.FF_PICKERS,
            'pick_mode'  : _s.FF_PICK_MODE,
            'restocks'   : restocks_for('fulfillment'),
            'cart'       : _s.FF_CART,
            'seed_offset': FF_BATCH_SEED_OFFSET,
            'batch'      : {'mean': _s.FF_BATCH_MEAN, 'std': _s.FF_BATCH_STD},
            'fill'       : _s.FF_FILL,
            # Fixed tier distribution (ignores ff demand mix) scaled to a bin target:
            # target_bins (or --ff-min-bins) sets the scale, else the demand-derived total.
            # depth_classes (optional): split each ff size tier's aisles into shallow/deep
            # SHAPES sharing one BinKey, so velocity zoning / trip-min can route hot SKUs to
            # shallow (low-travel) aisles.  Each = {'columns': n, 'share': w}; None = one width
            # (byte-identical).  e.g. [{'columns':10,'share':0.3},{'columns':40,'share':0.4},
            #                          {'columns':100,'share':0.3}]
            # aisle_split (optional): cut each aisle into k shorter segments with a capacity_loss
            # (throughway construction).  None/{'k':1} = byte-identical.  e.g. {'k':2,'capacity_loss':0.15}.
            'sizing'     : {'mode': 'fixed',
                            'distribution': {'ff_small': 0.5, 'ff_medium': 0.3, 'ff_large': 0.2},
                            'depth_classes': None, 'aisle_split': None,
                            'target_bins': None, 'min_bins': None, 'max_bins': None,
                            'max_aisles': None},
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


def k_pickers() -> int:
    """The store channel's picker count, read at call time."""
    return CONFIG['channels']['store']['num_pickers']


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


def recv_crew_spec() -> dict | None:
    """The receiving crew as a picklable record, or **None** when there is no crew.

    None rather than an empty dict, and that distinction is the feature's off-switch. The
    worker tests `args.get('recv_crew') is None` and skips its whole receive block: no Crew,
    no Workers, no clocks, no Dock, no WorkDay. The no-op is therefore "nothing was
    constructed" rather than "an empty thing exists" -- which matters because an empty dock
    would still be an object something could fold into a clock or a snapshot.

    COPIED FROM `work_day_spec`, NOT FROM `put_crew_spec`. That one reads `_s.PUT_CREW_SIZE`
    directly and there is no `CONFIG['global']['put_crew_*']` key at all -- so a CLI flag,
    which writes CONFIG, would be accepted and ignored forever, and a standalone re-analysis
    would size against this checkout's `settings.py` instead of the run's own. Reading CONFIG
    at CALL time is what makes the other three seams reachable.

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
    size = int(g.get('recv_crew_size') or 0)
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
INBOUND_KEYS: tuple[str, ...] = (
    'inbound_trailer_type', 'inbound_dock_doors',
    'inbound_lead_minutes', 'inbound_lead_spread',
    'inbound_global_policy', 'inbound_local_policy', 'inbound_trailer_bound',
    'inbound_standing_yard', 'inbound_crew_allocation',
    'inbound_yard_policy', 'inbound_dock_policy',
    'inbound_fee_threshold_days', 'inbound_urgency_horizon_days',
    'inbound_futuresight_batches',
    'inbound_unload_intercept', 'inbound_unload_weight_coef', 'inbound_unload_volume_coef',
)


def inbound_spec() -> dict | None:
    """The trailer pipeline's configuration as a picklable record, or **None** for off.

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
    if standing and int(g.get('recv_crew_size') or 0) < 1:
        raise ValueError(
            'INBOUND_STANDING_YARD needs a receiving crew (RECV_CREW_SIZE >= 1): '
            'unloading a staged trailer is crew labour, and with no crew the yard '
            'would stand forever with nothing raising')
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
        'global_policy': str(g.get('inbound_global_policy') or 'fifo'),
        'local_policy': str(g.get('inbound_local_policy') or 'fifo'),
        'bound': g.get('inbound_trailer_bound'),
        # The standing yard.  `standing` False keeps every key inert; the driver binds
        # the v1 transit and none of the rest is read.
        'standing': standing,
        'allocation': allocation,
        'yard_policy': yard_policy,
        'dock_policy': dock_policy,
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

    Reads CONFIG at CALL time. Copying `put_crew_spec` below would have been the natural
    move and is a trap: it reads `_s.PUT_CREW_SIZE` directly, there is no
    `CONFIG['global']['put_crew_*']` key at all, and a CLI flag writing CONFIG would
    therefore be accepted and ignored forever.

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


def put_crew_spec() -> dict:
    """The put crew as a picklable record: `{size, mode, x_speed, y_speed}`.

    Read at call time like every other tunable, and handed to the worker in its payload
    rather than re-imported there -- a spawned worker re-imports this module and would get
    pristine defaults.

    The speed comes from the crew's MODE, which is the point of having a mode: a crew
    labelled `foot` costed at the store's machine speed would write rows whose mode and
    duration contradict each other.
    """
    mode = _s.PUT_CREW_MODE
    x, y = ((_s.PUT_MACHINE_X, _s.PUT_MACHINE_Y) if mode == 'machine'
            else (_s.PUT_FOOT_X, _s.PUT_FOOT_Y))
    return {'size': _s.PUT_CREW_SIZE, 'mode': mode, 'x_speed': x, 'y_speed': y}


def crew_cost_spec() -> dict:
    """The other crews' PRICE as a picklable record:
    `{put_intercept_scale, put_item_ratio, recv_intercept_scale}`.

    Put-away and receiving keep picking's cost shape and coefficients by reference
    (`PutawayCost.from_pick`, `UnloadCost.from_putaway`); these three scalars are the only
    place their numbers may differ.  Read from CONFIG at CALL time -- never from `_s.`
    directly, which is the trap `put_crew_spec` above still carries -- and handed to the
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
    """
    return CONFIG['channels']['store']['fill']


def ff_fill() -> float:
    """The fulfillment regime's fill headroom, read at call time.  Same rule as store_fill."""
    return CONFIG['channels']['fulfillment']['fill']


def _checkpoint_every(n_batches: int) -> int:
    """Batches between per-strategy checkpoints (floor(n_batches * checkpoint_frac), ≥1) —
    matches the legacy ``max(1, N_BATCHES // 10)`` cadence at the default 0.1 fraction."""
    return max(1, int(n_batches * CONFIG['global']['checkpoint_frac']))


def regime_sizing_from_config() -> dict:
    """Assemble the per-regime warehouse-sizing dict from CONFIG (store demand/composition +
    caps; fulfillment fixed tier distribution + caps), each with its own fill headroom.  Used
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
    pass the config's own 'num_pickers' (store default k_pickers(), fulfillment default 20) and
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
