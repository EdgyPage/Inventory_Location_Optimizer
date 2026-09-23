"""test_cost_laws.py -- each cost class's `closed_form` law equals what that class charges.

`PickConfig`, `PutawayCost` and `UnloadCost` carry their per-event labour law as ONE expression
tree (`Warehouse/kernel/closed_form.py`: `pick_event_model`, `put_event_model`,
`unload_event_model`), bound to the object's own coefficients.  The same tree renders the LaTeX
a reader sees.  This file holds each law equal to the function the simulator actually bills
with -- `Pick._pick_time`, `putaway.put_cost`, `unload.unload_cost` -- so the page cannot say one
thing while the simulator charges another:

  1. over seeded random coefficients, for every handling transform the configs can name;
  2. over every REGISTERED pick config (store, fulfillment and the variants), and the put and
     receiving costs the run harness derives from them (`PutawayCost.from_pick`,
     `UnloadCost.from_putaway`) -- the chain picking -> put-away -> receiving as it runs;
  3. and the rendered law names the coefficients' values when an event is given.

Run:  python -m pytest Tests/unit/test_cost_laws.py -q
"""
from __future__ import annotations

import math
import random

import pytest

from Inbound.unload import UnloadCost, unload_cost
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.operations.putaway import PutawayCost, put_cost
from Warehouse.picking.Pick import PickConfig, _pick_time

TRANSFORMS = ('log', 'log:e', 'log:2', 'linear', 'sqrt', 'pow:1.5', 'pow:2.0')
BRACKETS = (((96.0, 1.0), (240.0, 1.2), (float('inf'), 1.4)),
            ((48.0, 1.0), (float('inf'), 1.5)))


def _event(rng):
    return {'x': rng.uniform(0, 2400), 'y': rng.uniform(0, 480), 'w': rng.uniform(0.1, 60),
            'vol': rng.uniform(1, 60000), 'q': rng.randint(1, 40),
            'vx': rng.uniform(0.5, 6), 'vy': rng.uniform(0.5, 6)}


def _close(a, b):
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


# ── 1. random coefficients, every transform ─────────────────────────────────────────────────

@pytest.mark.parametrize('wfn', TRANSFORMS)
@pytest.mark.parametrize('vfn', ('log', 'log:2', 'sqrt'))
def test_the_pick_law_is_the_pick_time(wfn, vfn):
    rng = random.Random(hash((wfn, vfn)) & 0xFFFF)
    for br in BRACKETS:
        for _ in range(50):
            cfg = PickConfig(pick_intercept=rng.uniform(0.5, 20), pick_per_item=rng.uniform(0, 1),
                             pick_weight_coef=rng.uniform(0, 1), pick_volume_coef=rng.uniform(0, 1),
                             pick_weight_fn=wfn, pick_volume_fn=vfn, height_brackets=br)
            e = _event(rng)
            law = cfg.closed_form.evaluate(y=e['y'], w=e['w'], vol=e['vol'], q=e['q'])
            assert _close(law, _pick_time(cfg, e['w'], e['vol'], e['q'], e['y']))


@pytest.mark.parametrize('wfn', TRANSFORMS)
def test_the_put_law_is_what_a_put_bills(wfn):
    rng = random.Random(len(wfn) * 97)
    for br in BRACKETS:
        for _ in range(50):
            cost = PutawayCost(intercept=rng.uniform(0.2, 10), per_item=rng.uniform(0, 0.5),
                               weight_coef=rng.uniform(0, 1), volume_coef=rng.uniform(0, 1),
                               weight_fn=wfn, volume_fn='log', height_brackets=br)
            e = _event(rng)
            want = put_cost(e['x'], e['y'], e['w'], e['vol'], e['q'],
                            SpeedProfile(e['vx'], e['vy']), cost)
            assert _close(cost.closed_form.evaluate(**e), want)


@pytest.mark.parametrize('wfn', TRANSFORMS)
def test_the_unload_law_is_what_a_pack_costs(wfn):
    rng = random.Random(len(wfn) * 31)
    for _ in range(100):
        cost = UnloadCost(intercept=rng.uniform(0.2, 10), per_item=rng.uniform(0, 0.5),
                          weight_coef=rng.uniform(0, 1), volume_coef=rng.uniform(0, 1),
                          weight_fn=wfn, volume_fn='log:2')
        e = _event(rng)
        law = cost.closed_form.evaluate(w=e['w'], vol=e['vol'], q=e['q'])
        assert _close(law, unload_cost(e['w'], e['vol'], e['q'], cost))


# ── 2. the registered configs, and the chain the run derives from them ───────────────────────

def test_every_registered_config_and_its_derived_crews_charge_their_laws():
    from Optimization.config.sim_config import _build_pick_cfg
    from Optimization.simconfig import PICK_CONFIGS
    rng = random.Random(2026)
    assert len(PICK_CONFIGS) >= 2
    for spec in PICK_CONFIGS:
        cfg = _build_pick_cfg(spec.cfg, num_pickers=1)
        put = PutawayCost.from_pick(cfg)
        recv = UnloadCost.from_putaway(put)
        for _ in range(100):
            e = _event(rng)
            assert _close(cfg.closed_form.evaluate(y=e['y'], w=e['w'], vol=e['vol'], q=e['q']),
                          _pick_time(cfg, e['w'], e['vol'], e['q'], e['y'])), spec.name
            assert _close(put.closed_form.evaluate(**e),
                          put_cost(e['x'], e['y'], e['w'], e['vol'], e['q'],
                                   SpeedProfile(e['vx'], e['vy']), put)), spec.name
            assert _close(recv.closed_form.evaluate(w=e['w'], vol=e['vol'], q=e['q']),
                          unload_cost(e['w'], e['vol'], e['q'], recv)), spec.name


# ── 3. what the laws read, and how they render ───────────────────────────────────────────────

def test_each_law_leaves_exactly_the_event_unbound():
    assert PickConfig().closed_form.event_inputs == {'y', 'w', 'vol', 'q'}
    assert PutawayCost().closed_form.event_inputs == {'x', 'y', 'vx', 'vy', 'w', 'vol', 'q'}
    assert UnloadCost().closed_form.event_inputs == {'w', 'vol', 'q'}


def test_a_law_renders_with_its_coefficients_substituted():
    cfg = PickConfig(pick_intercept=15.0, pick_per_item=0.5)
    page = cfg.closed_form.to_markdown(y=300.0, w=12.0, vol=800.0, q=3)
    assert r'c_{\mathrm{loc}} = M(y)\,\left(I + q\,p + q\,v_s\right)' in page
    assert '15' in page and '0.5' in page, 'the coefficients appear as numbers'
    bare = cfg.closed_form.to_markdown()
    assert '15' not in bare.split('**Inputs**')[1].split('$$')[1], \
        'without an event the law renders symbolically'
