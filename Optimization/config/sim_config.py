"""sim_config.py — the run's single source of tunable truth.

Everything a run can be tuned by lives here: the .env-backed output/profile dirs,
the per-channel pick-config sweeps (STORE_CONFIGS / FULFILLMENT_CONFIGS), the nested
CONFIG dict (global + per-channel sections), the derived read-only aliases, and the
config→PickConfig conversion.  run_simulation re-exports the public names so
`rs.CONFIG` / `rs.REGRESSION_CONFIGS` consumers (tests, Diagnostics/bucket_fill)
keep working.

CONFIG identity rule: the re-export binds the SAME dict object — tests mutate
`rs.CONFIG['global'][...]` in place — so nobody may ever REBIND CONFIG (or the
mutation would silently detach from internal readers).
"""
import logging
import os
import sys

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
from Optimization.config.strategies import restocks_for
from Optimization.config.channels import FF_BATCH_SEED_OFFSET
from Optimization.simconfig import PICK_CONFIGS                          # fires the registry import_all()
from Optimization.simconfig.constants import _STORE_PICKERS, _FF_PICKERS

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
_ACTIVE_PICK_CONFIGS = sorted((s for s in PICK_CONFIGS if s.enabled),
                              key=lambda s: (s.channel, s.order, s.name))
STORE_CONFIGS       = [s.cfg for s in _ACTIVE_PICK_CONFIGS if s.channel == 'store']
FULFILLMENT_CONFIGS = [s.cfg for s in _ACTIVE_PICK_CONFIGS if s.channel == 'fulfillment']
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
        'seed_world'      : 42,
        'seed_batches'    : 1337,
        'n_batches'       : 100,
        'workers'         : 1,
        'checkpoint_frac' : 0.1,     # checkpoint every ceil(n_batches * frac) batches
        # Keyframes are no longer how spatial state is RECONSTRUCTED — bin_placement +
        # bin_eviction + picks fold to exact bin state at every batch, with no keyframe
        # involved.  What a keyframe is now: an INDEPENDENT audit of that fold (the viewer
        # compares the two) and a quantity anchor that bounds a pick scan to one interval.
        # Neither job needs 5: that wrote 20 full-warehouse snapshots per 100-batch arm,
        # ~3.1M rows, to re-answer a question the log answers exactly.  25 keeps both roles
        # at a fifth of the cost.  0 still disables the sidecar entirely.
        'keyframe_interval': 25,
        'max_skus'        : None,    # global input-catalog cap (preserves the store/ff mix)
        # Batch-sampler VERSION — a results ERA, not a tuning knob.  'v2' (the Fenwick
        # sampler, introduced e7c9ed9, adopted as default 2026-08-20) draws the same
        # weight model as 'v1' in O((k·(1+partners))·log N) instead of O(k·N) — measured
        # 0.83s -> 0.05s per batch at 40k SKUs, 21.6s -> 0.48s at 160k — but its float
        # grouping differs, so its batch SEQUENCE differs: v2 runs are not row-comparable
        # with the pre-2026-08-20 archive.  `--sampler v1` reproduces that archive
        # exactly (byte-identical, digest-proven).  Batch caches are fingerprinted apart
        # per sampler, so the two eras can never contaminate each other.
        'sampler'         : 'v2',
    },
    'channels': {
        'store': {
            'regime'     : STORE,
            'configs'    : STORE_CONFIGS,
            'num_pickers': _STORE_PICKERS,
            'restocks'   : restocks_for('store'),
            'cart'       : 'StoreCart',
            'seed_offset': 0,
            'batch'      : {'mean': 0.15, 'std': 0.05},
            'fill'       : 0.85,
            # aisle_split (optional): cut each aisle into k shorter segments (~depth/k) with a
            # capacity_loss modeling throughway construction.  None/{'k':1} = no split
            # (byte-identical).  e.g. {'k': 2, 'capacity_loss': 0.15}.
            'sizing'     : {'mode': 'demand', 'min_bins': None, 'max_bins': None,
                            'max_aisles': None, 'composition': None, 'aisle_split': None},
            # Velocity zoning: restrict each unit's viable aisles to its velocity band
            # ("like-with-like"), composing with every arm.  enabled=False = byte-identical.
            # mode 'equal' (default, equal-count bands) | 'abc' (manual A/B/C by demand-mass
            # thresholds; aisles allocated by band footprint so the hot band is a small fraction).
            'velocity_zoning': {'enabled': False, 'n_bands': 3, 'mode': 'equal',
                                'abc': {'mass_thresholds': [0.7, 0.9]}},
        },
        'fulfillment': {
            'regime'     : FULFILLMENT,
            'configs'    : FULFILLMENT_CONFIGS,
            'num_pickers': _FF_PICKERS,
            'restocks'   : restocks_for('fulfillment'),
            'cart'       : 'FulfillmentCart',
            'seed_offset': FF_BATCH_SEED_OFFSET,
            'batch'      : {'mean': 0.20, 'std': 0.05},
            'fill'       : 0.85,
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
            'velocity_zoning': {'enabled': False, 'n_bands': 3, 'mode': 'equal',
                                'abc': {'mass_thresholds': [0.7, 0.9]}},
        },
    },
}

# Derived read-only aliases for external consumers (bucket_fill.py, README) —
# CONFIG is authoritative; internal code reads CONFIG, not these.
SEED_WORLD     = CONFIG['global']['seed_world']
SEED_BATCHES   = CONFIG['global']['seed_batches']
N_BATCHES      = CONFIG['global']['n_batches']
K_PICKERS      = CONFIG['channels']['store']['num_pickers']
STORE_RESTOCKS = CONFIG['channels']['store']['restocks']


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


def _config_name(cfg: dict) -> str:
    """A config's directory/identity name (explicit 'name', else a coeff fingerprint)."""
    return cfg.get('name') or (
        f"w{cfg.get('pick_weight_coef',1.1)}_v{cfg.get('pick_volume_coef',1e-3)}"
        f"_i{cfg.get('pick_intercept',1.0)}_c{cfg.get('cart_swap_coef',10.0)}"
    )


def _build_pick_cfg(cfg: dict, *, num_pickers: int, default_cart=StoreCart) -> PickConfig:
    """Turn a config dict (store or fulfillment) into a PickConfig.

    The one canonical dict→PickConfig conversion shared by both channels' sweeps.  Callers
    pass the config's own 'num_pickers' (store default K_PICKERS, fulfillment default 20) and
    the channel's default_cart (StoreCart / FulfillmentCart); a 'cart' key overrides it.
    """
    return PickConfig(
        num_pickers      = num_pickers,
        x_speed          = cfg.get('x_speed',          4.0),   # ft/s (positions are inches)
        y_speed          = cfg.get('y_speed',          2.0),   # ft/s
        pick_intercept   = cfg.get('pick_intercept',   1.0),
        pick_weight_coef = cfg.get('pick_weight_coef', 1.1),
        pick_volume_coef = cfg.get('pick_volume_coef', 1e-3),
        pick_weight_fn   = cfg.get('pick_weight_fn',   'log'),  # base function per term
        pick_volume_fn   = cfg.get('pick_volume_fn',   'log'),
        cart_swap_coef   = cfg.get('cart_swap_coef',   10.0),
        cart             = _CART_TYPES.get(cfg['cart'], default_cart) if 'cart' in cfg else default_cart,
        height_brackets  = cfg.get('height_brackets',  DEFAULT_HEIGHT_BRACKETS),
        one_way          = cfg.get('one_way',          False),   # one-way lanes; default off = today
        scheduler        = cfg.get('scheduler',        'round_robin'),  # 'lpt' load-balances makespan
    )
