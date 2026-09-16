"""test_declared_once.py — a value two layers need is ONE value, not two equal ones.

`settings.py` states the rule and applies it to the three crew-price scalars:

    The defaults are the kernel's declaration (Warehouse/kernel/cost_model.py), imported
    rather than restated so the dataclass defaults and the run defaults cannot drift apart.

`Warehouse/operations/putaway.py` records what it costs when nobody applies it: "this project
has already paid for two default sets that drifted 55x apart".

This file pins the pairs that rule now covers. It asserts IDENTITY, not equality, and the
distinction is the whole point: two literals that both read 0.85 today satisfy `==` and are
exactly the state this test exists to forbid. `is` holds only while there is one object, which
for a module-level constant means one `NAME = value` statement in the repo.

DIRECTION is not stylistic. `context/architecture.yml` forbids `warehouse_core -> optimization`,
`wh_* -> optimization` and `inbound -> optimization`, so the domain DECLARES and `settings.py`
IMPORTS. A "tidy-up" that inverts one of these pairs fails the boundary verifier rather than
this test — the two checks fence one rule from opposite sides.

Run:  python -m pytest Tests/unit/test_declared_once.py -q
"""
from __future__ import annotations

import inspect

import pytest

import Optimization.config.settings as settings
from Inbound.gain import DEFAULT_FEE_THRESHOLD_DAYS, DEFAULT_URGENCY_HORIZON_DAYS
from Inbound.transit import DEFAULT_DOCK_DOORS
from Warehouse.inventory.inventory_planning import DEFAULT_TARGET_FILL
from Warehouse.inventory.inventory_zoning import DEFAULT_ZONING_BANDS
from Warehouse.kernel.cost_model import (
    DEFAULT_PUT_INTERCEPT_SCALE, DEFAULT_PUT_ITEM_RATIO, DEFAULT_RECV_INTERCEPT_SCALE,
)


#: (settings name, the domain declaration it must BE). Extend when a pair is unified; an entry
#: costs one line and buys a permanent fence.
PAIRS = [
    ('STORE_FILL',                   DEFAULT_TARGET_FILL),
    ('FF_FILL',                      DEFAULT_TARGET_FILL),
    ('INBOUND_DOCK_DOORS',           DEFAULT_DOCK_DOORS),
    ('INBOUND_FEE_THRESHOLD_DAYS',   DEFAULT_FEE_THRESHOLD_DAYS),
    ('INBOUND_URGENCY_HORIZON_DAYS', DEFAULT_URGENCY_HORIZON_DAYS),
    # the three that already worked this way, pinned so a regression is caught here too
    ('PUT_INTERCEPT_SCALE',          DEFAULT_PUT_INTERCEPT_SCALE),
    ('PUT_ITEM_RATIO',               DEFAULT_PUT_ITEM_RATIO),
    ('RECV_INTERCEPT_SCALE',         DEFAULT_RECV_INTERCEPT_SCALE),
]


@pytest.mark.parametrize('name,declared', PAIRS, ids=[p[0] for p in PAIRS])
def test_the_setting_is_the_domain_declaration(name, declared):
    """`settings.<name>` must BE the domain's object, not a second literal equal to it."""
    got = getattr(settings, name)
    assert got is declared, (
        f'settings.{name} is a SECOND declaration of the same value. It reads {got!r} and the '
        f'domain declares {declared!r}; today they agree, which is exactly how the 55x drift '
        f'recorded in Warehouse/operations/putaway.py began. Import the domain constant.')


def test_zoning_bands_is_one_value_in_all_four_places():
    """`n_bands` had FOUR literals: the manager's instance default, `configure_zoning`'s
    signature default, `settings.ZONING_OFF`, and — the dangerous one — a `.get('n_bands', 3)`
    fallback inside the worker (`strategy_runner`).

    A worker fallback is invisible to the five seams. Had `ZONING_OFF` changed, that literal
    would have kept the old value in the spawned process, where nothing reads back and no
    run_spec records it.
    """
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    from Warehouse.inventory.inventory_zoning import ZoningMixin
    from Optimization.simdriver import strategy_runner

    assert settings.ZONING_OFF['n_bands'] is DEFAULT_ZONING_BANDS

    sig = inspect.signature(ZoningMixin.configure_zoning)
    assert sig.parameters['n_bands'].default is DEFAULT_ZONING_BANDS

    src = inspect.getsource(Inventory_Manager.__init__)
    assert 'self._zoning_bands: int = DEFAULT_ZONING_BANDS' in src, (
        'the manager restated the band count instead of reading the declaration')

    wsrc = inspect.getsource(strategy_runner)
    assert "_zcfg.get('n_bands', DEFAULT_ZONING_BANDS)" in wsrc, (
        "the worker's n_bands fallback is a bare literal again — a value the five seams "
        'cannot see and no run records')


def test_the_identity_check_can_fail():
    """Non-vacuity: `is` must actually distinguish a second literal from the shared object,
    or every assertion above passes for free.

    Floats are the case that matters. CPython interns small ints, so `3 is 3` holds for two
    independent literals and `DEFAULT_ZONING_BANDS` is fenced by the source checks above
    rather than by identity. The floats are not interned, so `is` has teeth where it must.
    """
    second_literal = 0.85
    assert second_literal == DEFAULT_TARGET_FILL, 'premise: the two agree in value'
    assert second_literal is not DEFAULT_TARGET_FILL, (
        'a separate 0.85 literal is indistinguishable from the shared object on this '
        'interpreter, so `is` fences nothing and these tests are vacuous')


def test_the_excluded_defaults_are_excluded_on_purpose():
    """Three domain defaults deliberately do NOT track a setting. Pinned so a later sweep does
    not "finish the job" and change behaviour believing it is tidying.

      * `PutQueueSpec.swap_coef = 0.0` — the "no cart model" SENTINEL. If an operator set
        `PUT_SWAP_COEF = 5.0`, a queue built with no cart must still charge nothing.
      * `BatchConfig.sampler = 'v1'` against `settings.SAMPLER = 'v3'` — a recorded decision
        (`channels.py`: non-runner constructions "keep their frozen historical meaning").
      * `BatchConfig.mean_fraction = 0.20` — the dataclass's own generic default. It equals
        `FF_BATCH_MEAN` by coincidence and differs from `STORE_BATCH_MEAN` (0.15), so binding
        it to either would assert a relationship that does not exist.
    """
    from Warehouse.inventory.put_queue import PutQueueSpec, ANY
    from Warehouse.picking.Workload_Builder import BatchConfig

    assert PutQueueSpec('q', accepts=ANY).swap_coef == 0.0

    cfg = BatchConfig(inventory_size=10)
    assert cfg.sampler == 'v1', 'the frozen-historical default moved'
    assert settings.SAMPLER == 'v3', 'the run default moved'
    assert cfg.sampler != settings.SAMPLER, (
        'the two agree now, so the recorded reason for keeping them apart no longer holds — '
        'either re-record the decision or unify them')

    assert cfg.mean_fraction == settings.FF_BATCH_MEAN
    assert cfg.mean_fraction != settings.STORE_BATCH_MEAN, (
        'store and fulfillment batch shapes converged; the "coincidence" argument for leaving '
        'BatchConfig.mean_fraction free needs restating')
