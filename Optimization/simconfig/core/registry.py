"""PickConfigSpec descriptor + @pick_config decorator + module-level registry.

Mirrors Optimization/Performance_Evaluations/core/registry.py: a frozen dataclass per item, a
flat list, and a by-key dict, populated by a decorator so a pick-config module self-registers on
import.  A pick-config is the plain dict Optimization/sim_config._build_pick_cfg consumes; the spec
makes explicit the CHANNEL split (store vs fulfillment) that used to be implicit in WHICH list the
dict lived in, plus a sweep ``order`` (list order is load-bearing — it sets config-dir identity and
sweep order, so it must NOT depend on filesystem walk order) and an ``enabled`` flag so a variant
can be kept as a discoverable menu entry without running.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PickConfigSpec:
    name:    str                 # identity / dir name (matches _config_name)
    channel: str                 # 'store' | 'fulfillment'
    cfg:     dict                # the pick-config dict _build_pick_cfg consumes
    order:   int = 0             # sweep order within a channel (list order is load-bearing)
    enabled: bool = True         # False = registered menu entry, excluded from the active sweep


PICK_CONFIGS: list[PickConfigSpec] = []
PICK_CONFIG_BY_KEY: dict[str, PickConfigSpec] = {}


def pick_config(*, name, channel, order=0, enabled=True):
    """Decorator: register the wrapped zero-arg builder's returned dict as a PickConfigSpec.

    Returns the builder unchanged so it stays directly callable/unit-testable.  Raises on a
    duplicate name or an unknown channel.
    """
    if channel not in ('store', 'fulfillment'):
        raise ValueError(f'pick_config channel must be store|fulfillment, got {channel!r}')

    def _wrap(fn):
        if name in PICK_CONFIG_BY_KEY:
            raise ValueError(f'duplicate pick-config name {name!r}')
        spec = PickConfigSpec(name=name, channel=channel, cfg=fn(), order=order, enabled=enabled)
        PICK_CONFIGS.append(spec)
        PICK_CONFIG_BY_KEY[name] = spec
        return fn
    return _wrap
