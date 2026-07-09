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
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))

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

from Warehouse.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.Pick import PickConfig, DEFAULT_HEIGHT_BRACKETS
from Warehouse.Storage_Primitive import StoreCart, FulfillmentCart
from Warehouse.regime import STORE, FULFILLMENT
from Optimization.strategies import restocks_for
from Optimization.channels import FF_BATCH_SEED_OFFSET

# ── warehouse geometry (structural; shared by both channels) ────────────────────
# Physical aisle dimensions: 50 pallet-width columns × 10 extra_large-height levels.
# Actual bin counts per aisle depend on unit type and size distribution.
_AISLE_W = aisle_width_for(50)    # 50 × 48 = 2400 physical units
_AISLE_H = aisle_height_for(10)   # 10 × 48 = 480 physical units

# Picker-pool defaults per channel (a pick-config entry may override its own 'num_pickers').
_STORE_PICKERS = 25
_FF_PICKERS    = 20


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
# SKU is placeable.  See Warehouse/Inventory_Management.py.


REGRESSION_CONFIGS = [
    {
        'name'            : 'store',
        'pick_intercept'  : 15,
        'pick_weight_coef': 0.58,
        'pick_weight_fn'  : 'pow:1.5',
        'pick_volume_coef': 0.7,
        'pick_volume_fn'  : 'log:2',
        'cart_swap_coef'  : 300,
        'x_speed'         : 3,    # ft/s
        'y_speed'         : 2,    # ft/s
        'num_pickers'     : _STORE_PICKERS,   # machine order-picker pool size
        'height_brackets' : ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4)),
    },
    {
        'name'            : 'store_high_weight',
        'pick_intercept'  : 15,
        'pick_weight_coef': 0.58,
        'pick_weight_fn'  : 'pow:2.0',
        'pick_volume_coef': 0.7,
        'pick_volume_fn'  : 'log:2',
        'cart_swap_coef'  : 300,
        'x_speed'         : 3,    # ft/s
        'y_speed'         : 2,    # ft/s
        'num_pickers'     : _STORE_PICKERS,   # machine order-picker pool size
        'height_brackets' : ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4)),
    },
#    {
#        'name'            : 'store_high_weight_high_height',
#        'pick_intercept'  : 15,
#        'pick_weight_coef': 0.58,
#        'pick_weight_fn'  : 'pow:2.0',
#        'pick_volume_coef': 0.7,
#        'pick_volume_fn'  : 'log:2',
#        'cart_swap_coef'  : 300,
#        'x_speed'         : 3,    # ft/s
#        'y_speed'         : 2,    # ft/s
#        'num_pickers'     : _STORE_PICKERS,   # machine order-picker pool size
#        'height_brackets' : ((96.0, 1.0), (240.0, 1.4), (float('inf'), 1.8)),
#    },
#    {
#        'name'            : 'store_high_height',
#        'pick_intercept'  : 15,
#        'pick_weight_coef': 0.58,
#        'pick_weight_fn'  : 'pow:1.5',
#        'pick_volume_coef': 0.7,
#        'pick_volume_fn'  : 'log:2',
#        'cart_swap_coef'  : 300,
#        'x_speed'         : 3,    # ft/s
#        'y_speed'         : 2,    # ft/s
#        'num_pickers'     : _STORE_PICKERS,   # machine order-picker pool size
#        'height_brackets' : ((96.0, 1.0), (240.0, 1.4), (float('inf'), 1.8)),
#    },
]

# ── per-channel config sweeps ───────────────────────────────────────────────────
# Store and fulfillment are INDEPENDENT warehouse sections (see channels.py): each
# sweeps its OWN set of pick-time regression configs, runs its own restock suite,
# writes its own DB subtree, and is combined only post-analysis by run_channel_rollup.
# The sweep is a UNION, not a cross product: a mixed catalog runs len(STORE_CONFIGS)
# store runs + len(FULFILLMENT_CONFIGS) fulfillment runs (a store-only catalog runs
# only the store set).  REGRESSION_CONFIGS above IS the store set; STORE_CONFIGS is the
# preferred name (the alias keeps existing `rs.REGRESSION_CONFIGS` consumers working).
STORE_CONFIGS = REGRESSION_CONFIGS

# Fulfillment (human-walker) configs.  IDENTICAL dict schema to the store set (every config
# carries its own 'num_pickers' pool size + optional 'cart').  The FIRST entry is the single
# source of truth for the default walker cost — channels.fulfillment_pick_config() reads it
# back (it used to carry its own copy, which drifted).  Keep names DISTINCT from store config
# names (config.json is written per config dir; a shared name would collide — see the runner's
# _prepare_channel_run).
FULFILLMENT_CONFIGS = [
    {
        'name'            : 'ful_calibrated',
        'pick_intercept'  : 10,
        'pick_weight_coef': 0.7,
        'pick_weight_fn'  : 'log',
        'pick_volume_coef': 0.09,
        'pick_volume_fn'  : 'log',
        'cart_swap_coef'  : 240,
        'cart'            : 'FulfillmentCart',
        'x_speed'         : 2,    # ft/s
        'y_speed'         : 4,    # ft/s
        'num_pickers'     : _FF_PICKERS,   # walker pool size (independent of store pickers)
        # height_brackets omitted → DEFAULT (no-op for ff bins, all M=1).
    },
    {
        'name'            : 'ful_calibrated_fast_walkers',
        'pick_intercept'  : 10,
        'pick_weight_coef': 0.7,
        'pick_weight_fn'  : 'log',
        'pick_volume_coef': 0.09,
        'pick_volume_fn'  : 'log',
        'cart_swap_coef'  : 240,
        'cart'            : 'FulfillmentCart',
        'x_speed'         : 4,    # ft/s
        'y_speed'         : 4,    # ft/s
        'num_pickers'     : _FF_PICKERS,   # walker pool size (independent of store pickers)
        # height_brackets omitted → DEFAULT (no-op for ff bins, all M=1).
    },
]


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
        'keyframe_interval': 5,
        'max_skus'        : None,    # global input-catalog cap (preserves the store/ff mix)
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
            'fill'       : 0.9,
            'sizing'     : {'mode': 'demand', 'min_bins': None, 'max_bins': None,
                            'max_aisles': None, 'composition': None},
        },
        'fulfillment': {
            'regime'     : FULFILLMENT,
            'configs'    : FULFILLMENT_CONFIGS,
            'num_pickers': _FF_PICKERS,
            'restocks'   : restocks_for('fulfillment'),
            'cart'       : 'FulfillmentCart',
            'seed_offset': FF_BATCH_SEED_OFFSET,
            'batch'      : {'mean': 0.20, 'std': 0.05},
            'fill'       : 0.92,
            # Fixed tier distribution (ignores ff demand mix) scaled to a bin target:
            # target_bins (or --ff-min-bins) sets the scale, else the demand-derived total.
            'sizing'     : {'mode': 'fixed',
                            'distribution': {'ff_small': 0.5, 'ff_medium': 0.3, 'ff_large': 0.2},
                            'target_bins': None, 'min_bins': None, 'max_bins': None,
                            'max_aisles': None},
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
_INITIAL_FILL  = CONFIG['channels']['store']['fill']


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
    # %(name)-14s gives a fixed-width column so A/B/C worker labels align with
    # the main-process 'comparison' label in the same log file.
    fmt = logging.Formatter(
        '%(asctime)s  %(name)-14s  %(message)s',
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
    )
