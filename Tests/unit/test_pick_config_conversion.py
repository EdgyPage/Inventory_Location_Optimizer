"""test_pick_config_conversion.py — one dict→PickConfig conversion, one default set.

There were three ways to turn a config into a `PickConfig`, and they disagreed.

  * `sim_config._build_pick_cfg` — the canonical one, which restated a fallback per key
  * `Diagnostics/bucket_fill._build_pick_cfg` — a copy that silently omitted `cart`,
    `one_way` and `scheduler`, so that probe modelled a StoreCart two-way round-robin
    picker whatever the config asked for
  * `run_map_precompute._workload_params` — rebuilds from an ARCHIVED config.json by
    field-filtering, so it has always used `PickConfig`'s dataclass defaults

Which means two default sets, and they had drifted: `pick_weight_coef` 1.1 against the
dataclass's 0.02, `pick_volume_coef` 1e-3 against 1e-4, `cart_swap_coef` 10.0 against 5.0.
A 55x difference in the weight term between the live path and the archive path, waiting
for the first config or archive vintage that omits a key.

Nothing had ever exercised it: every registered pick-config declares all three. So the
reconciliation is byte-identical, and the direction is the one that leaves a single
declaration — `_build_pick_cfg` filters the dict and lets the dataclass supply what is
missing, exactly as the archive path already did.

Run:  python -m pytest Tests/unit/test_pick_config_conversion.py -q
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from Optimization.config.sim_config import (
    _CART_TYPES, _PICK_CONFIG_FIELDS, _build_pick_cfg,
)
from Optimization.metrics.Workload import WorkloadParams
from Optimization.simconfig import PICK_CONFIGS
from Warehouse.layout.Storage_Primitive import FulfillmentCart, StoreCart
from Warehouse.picking.Pick import PickConfig

_DISAGREED = ('pick_weight_coef', 'pick_volume_coef', 'cart_swap_coef')


# ── one default set ───────────────────────────────────────────────────────────────

def test_an_empty_config_is_the_dataclass_defaults():
    """The reconciliation, stated as an equality.  These two used to differ on three
    coefficients."""
    built  = _build_pick_cfg({}, num_pickers=1)
    direct = PickConfig(num_pickers=1)
    assert dataclasses.asdict(built) == dataclasses.asdict(direct)


@pytest.mark.parametrize('field', _DISAGREED)
def test_the_three_that_drifted_now_agree(field):
    assert getattr(_build_pick_cfg({}, num_pickers=1), field) == \
           getattr(PickConfig(num_pickers=1), field)


def test_the_two_paths_agree_on_workload_params_too():
    """WorkloadParams is the analytical mirror; if the two constructions diverge, the
    predicted-vs-realised equivalence tests compare two different models."""
    a = WorkloadParams.from_pick_config(_build_pick_cfg({}, num_pickers=1))
    b = WorkloadParams.from_pick_config(PickConfig(num_pickers=1))
    assert dataclasses.asdict(a) == dataclasses.asdict(b)


def test_the_fallbacks_were_dead_code_for_every_registered_config():
    """Why the reconciliation is byte-identical rather than merely defensible: no
    registered pick-config, enabled or not, omits any of the three."""
    for spec in PICK_CONFIGS:
        missing = [k for k in _DISAGREED if k not in spec.cfg]
        assert not missing, f'{spec.name} omits {missing}; the reconciliation is NOT a no-op'


# ── the conversion still converts ─────────────────────────────────────────────────

def test_a_declared_value_always_wins_over_the_default():
    cfg = {'name': 'probe', 'x_speed': 7.0, 'pick_intercept': 42.0, 'one_way': True,
           'scheduler': 'lpt', 'pick_weight_coef': 0.9}
    pc = _build_pick_cfg(cfg, num_pickers=3)
    assert (pc.x_speed, pc.pick_intercept, pc.one_way, pc.scheduler) == (7.0, 42.0, True, 'lpt')
    assert pc.pick_weight_coef == 0.9
    assert pc.num_pickers == 3


def test_a_non_field_key_is_dropped_rather_than_raising():
    """Every config dict carries `name`, which is not a PickConfig field."""
    assert _build_pick_cfg({'name': 'store'}, num_pickers=1).num_pickers == 1


def test_the_cart_name_resolves_to_the_class():
    assert _build_pick_cfg({'cart': 'FulfillmentCart'}, num_pickers=1).cart is FulfillmentCart
    assert _build_pick_cfg({}, num_pickers=1, default_cart=FulfillmentCart).cart is FulfillmentCart
    assert _build_pick_cfg({}, num_pickers=1).cart is StoreCart


def test_an_unknown_cart_name_falls_back_to_the_channel_default():
    assert _build_pick_cfg({'cart': 'NoSuchCart'}, num_pickers=1,
                           default_cart=FulfillmentCart).cart is FulfillmentCart


@pytest.mark.parametrize('spec', PICK_CONFIGS, ids=lambda s: s.name)
def test_every_registered_config_converts(spec):
    pc = _build_pick_cfg(spec.cfg, num_pickers=spec.cfg.get('num_pickers', 1))
    assert isinstance(pc, PickConfig) and pc.num_pickers >= 1


# ── the field set is derived, and there is no fourth converter ────────────────────

def test_the_field_set_is_derived_from_the_dataclass():
    """A hand-written list is how bucket_fill's copy came to be missing three keys."""
    assert _PICK_CONFIG_FIELDS == frozenset(f.name for f in dataclasses.fields(PickConfig))


def test_no_module_builds_a_pickconfig_from_a_config_dict_on_its_own():
    """A ratchet.  `_build_pick_cfg` is the only dict→PickConfig conversion, and
    `run_map_precompute` is the one archived-config rebuild — both filtering the SAME
    derived field set.  A new `PickConfig(` beside a `cfg.get(` is a fourth default set.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {
        'Optimization/config/sim_config.py',        # the canonical converter
        'Optimization/run_map_precompute.py',       # the archived-config rebuild
    }
    offenders = []
    for path in sorted(root.glob('Optimization/**/*.py')) + \
                sorted(root.glob('Diagnostics/**/*.py')) + \
                sorted(root.glob('Warehouse/**/*.py')):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        src = path.read_text(encoding='utf-8')
        if 'PickConfig(' in src and 'cfg.get(' in src:
            offenders.append(rel)
    assert not offenders, (
        f'{offenders} construct a PickConfig from a config dict with their own fallbacks; '
        f'route them through sim_config._build_pick_cfg instead')
