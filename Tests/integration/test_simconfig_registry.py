"""test_simconfig_registry.py

Locks the simconfig pick-config registry (Phase 2 Commit A): sim_config rebuilds STORE_CONFIGS /
FULFILLMENT_CONFIGS from the self-registering @pick_config modules under Optimization/simconfig/
configs/, byte-identical to the old inline literals, deterministically ordered, with enabled=False
variants registered (a discoverable menu) but excluded from the active sweep.  The legacy aliases
(REGRESSION_CONFIGS, CONFIG['channels'][*]['configs']) keep their identity.

Run:  python -m pytest Tests/test_simconfig_registry.py -q
"""
from __future__ import annotations

import pytest

from Optimization.config import sim_config as sc
from Optimization.simconfig import PICK_CONFIGS, PICK_CONFIG_BY_KEY
from Optimization.simconfig.core.registry import pick_config

_EXP_STORE = {'name': 'store', 'pick_intercept': 15, 'pick_weight_coef': 0.58,
              'pick_weight_fn': 'pow:1.5', 'pick_volume_coef': 0.7, 'pick_volume_fn': 'log:2',
              'cart_swap_coef': 300, 'x_speed': 3, 'y_speed': 2, 'num_pickers': 25,
              'height_brackets': ((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4))}
_EXP_FF = {'name': 'ful_calibrated', 'pick_intercept': 10, 'pick_weight_coef': 0.7,
           'pick_weight_fn': 'log', 'pick_volume_coef': 0.09, 'pick_volume_fn': 'log',
           'cart_swap_coef': 240, 'cart': 'FulfillmentCart', 'x_speed': 2, 'y_speed': 4,
           'one_way': True, 'num_pickers': 20}


def test_active_sweeps_rebuilt_from_registry():
    assert [c['name'] for c in sc.STORE_CONFIGS] == ['store']
    assert [c['name'] for c in sc.FULFILLMENT_CONFIGS] == ['ful_calibrated']
    assert sc.REGRESSION_CONFIGS is sc.STORE_CONFIGS                     # legacy alias identity
    assert sc.CONFIG['channels']['store']['configs'] is sc.STORE_CONFIGS  # CONFIG references it


def test_config_holds_copies_of_the_registrys_dicts():
    """The LIST aliases (asserted above); the DICTS inside must not.

    `_apply_cell` writes `cfg['scheduler']` into every entry of
    CONFIG['channels'][*]['configs'] once per what-if cell.  While those were the registry's
    own dicts that write landed in `PICK_CONFIG_BY_KEY[…].cfg` permanently — a frozen
    dataclass does not stop you mutating a dict it holds.  Registry = declaration,
    CONFIG = run state, one copy between them.
    """
    assert sc.STORE_CONFIGS[0] == PICK_CONFIG_BY_KEY['store'].cfg
    assert sc.STORE_CONFIGS[0] is not PICK_CONFIG_BY_KEY['store'].cfg
    assert sc.FULFILLMENT_CONFIGS[0] is not PICK_CONFIG_BY_KEY['ful_calibrated'].cfg


def test_pick_config_dicts_byte_identical():
    """Asserted against the REGISTRY, not CONFIG.

    Reading `sc.STORE_CONFIGS[0]` here made this test order-dependent: any earlier test in
    the same process that ran a what-if cell added a `scheduler` key that is in neither
    expected literal, and the failure would have looked like a config-module edit.  The
    registry is what the author declared, so the registry is what a byte-identity check
    should read.
    """
    assert PICK_CONFIG_BY_KEY['store'].cfg == _EXP_STORE
    assert PICK_CONFIG_BY_KEY['ful_calibrated'].cfg == _EXP_FF


def test_disabled_variants_registered_but_excluded():
    assert {'store', 'ful_calibrated', 'store_high_height', 'store_high_weight_high_height'} \
        <= set(PICK_CONFIG_BY_KEY)
    # the two store variants are registered enabled=False, so NOT in the active sweep
    assert PICK_CONFIG_BY_KEY['store_high_height'].enabled is False
    assert [s.name for s in PICK_CONFIGS if s.enabled and s.channel == 'store'] == ['store']


def test_duplicate_name_raises():
    with pytest.raises(ValueError):
        pick_config(name='store', channel='store')(lambda: {'name': 'store'})  # already registered


def test_bad_channel_raises():
    with pytest.raises(ValueError):
        pick_config(name='zzz_unused', channel='warehouse')
