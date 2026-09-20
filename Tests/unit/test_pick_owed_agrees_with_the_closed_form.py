"""test_pick_owed_agrees_with_the_closed_form.py — the cheap placement score and an
independent evaluation of the same placements must put them in the same order.

`pick_owed_s` is a PROXY.  It walks each SKU's own bins, weights by the run's planned
script, and prices a line as the mean at-location cost over that SKU's locations.  It knows
nothing about carts, routes, aisle visits or how many lines share a trip.  The phase-2
ranking is decided by it, which is only defensible if an evaluation that DOES model those
things agrees about which placement is better.

`simconfig/expected_travel` is that evaluation — the closed form the staffing derivation
already uses: carts routed through aisles, weighted by relative frequency, integrating the
SKU's own line-quantity law over its bins, returning seconds per unit.  Two entirely
different models.  They will never agree on a VALUE (different grain, different weights),
and a test asserting they did would be asserting that one was computed from the other.
What has to hold is the ORDER.

## THE LIMIT THIS FILE FOUND, and why it is pinned rather than tuned away

The closed form models a day as ONE SWEEP PER AISLE: every aisle with any demand is walked
once, and the day's travel is that traversal amortised over every line picked from it.  On
this fixture that makes travel **0.4% of the modelled day** -- 94 s of travel against
17,149 s of handling and 7,126 s of cart swaps -- and `travel_x_s` moves by six decimal
places between a placement that puts the hottest SKUs in the near aisle and one that puts
them in the far one.

So the closed form is NOT sensitive to which aisle a SKU lands in, as long as the aisle is
visited anyway.  What it IS sensitive to is bay HEIGHT (through the same height brackets
the proxy uses) and within-aisle SPAN.  The agreement this file pins is therefore real but
narrower than "two independent models of pick work agree": it is agreement through the
terms they share, plus a demonstration that the term they do not share is small in the
closed form and large in the proxy.

That is worth having and worth saying.  It is NOT worth tuning a fixture until the two
appear to agree on travel -- they cannot, and a fixture that made them would be measuring
the fixture.  The consequence for the ranking is recorded on `run_unload_ranking`'s
`exact_check` note: a disagreement there is informative, an agreement is weaker evidence
than it looks, and neither is enforced.

## What this pins, and what it deliberately does not

It pins agreement on placements that differ the way an unloading policy makes them differ:
the same stock, in the same warehouse, at better or worse locations.  It does NOT claim the
two agree on every conceivable pair -- the run-time check
(`batch_stats.pick_owed_exact_s`, taken at keyframe cadence and reported by
`run_unload_ranking`'s `exact_check`) is what watches for that on real runs.  This file is
the offline half, and it fails on the day the proxy's arithmetic stops tracking pick work
at all, which is the failure no single run could attribute.

REAL DOMAIN TYPES, not stubs.  `Order.build` and `PickConfig` are what both models read in
production; a duck-typed pair would let the two models drift onto different assumptions
about the catalogue and this file would keep passing.  The first draft used stubs and the
closed form immediately asked for `demand.line`, which no stub would have thought to carry.

Run:  python -m pytest Tests/unit/test_pick_owed_agrees_with_the_closed_form.py -q
"""
from __future__ import annotations

import pytest

from Optimization.simconfig import expected_travel as et
from Optimization.simdriver.strategy_runner import expected_pick_over
from Warehouse.catalog.Order import Order
from Warehouse.kernel.cost_model import handle_var, height_multiplier, per_pick, sec_per_inch
from Warehouse.picking.Pick import PickConfig

# ── a warehouse both models can read ─────────────────────────────────────────────────
#
# Aisles of ONE BinKey, so the closed form's class machinery has a single class and every
# difference between the placements below is a LOCATION difference and nothing else.

_KEY = ('standard', 'general', 'medium', 'pallet')
#: Eight bays deep by three high.  Deep enough that the NARROWEST geometry tested
#: below (two aisles, 48 slots) still holds all forty SKUs -- see `_placement`, which
#: refuses rather than dropping the overflow.
_BAYS_X, _BAYS_Y = 8, 3
_X_STEP, _Y_STEP = 40, 50

_CFG = PickConfig(x_speed=4.0, y_speed=2.0)

#: Forty SKUs with a Zipf-shaped demand curve: sharply unequal, so moving the HOT ones is
#: what separates the placements, and MORE SKUS THAN ONE AISLE HOLDS, which is the property
#: that makes this fixture able to test anything.
#:
#: The first draft used ten SKUs in a four-aisle warehouse of eighteen bays each.  All ten
#: fit in aisle 0 under every placement, so the only difference between them was bay order
#: INSIDE one aisle -- and the two models then disagreed by 0.2%, which is not a finding
#: about the proxy, it is two models being asked to rank three things that are nearly the
#: same thing.  Aisle choice is the dimension warehouse travel is actually about, and a
#: fixture that cannot vary it cannot check that the proxy tracks it.
_N_SKUS = 40
_FREQ = tuple(1.0 / (i + 1) ** 1.2 for i in range(_N_SKUS))
_ORDERS = [Order.build(sku=i, handling='standard', category='general',
                       length=20, width=20, height=20, weight=30,
                       relative_frequency=f, qty_rate=4)
           for i, f in enumerate(_FREQ)]
_SKUS = [o.sku for o in _ORDERS]
_QTY = {o.sku: 20.0 for o in _ORDERS}
#: The planned script.  Same shape as the draw weights, which is what a real run's batches
#: look like when they are drawn from those weights.
_LINES = {o.sku: f for o, f in zip(_ORDERS, _FREQ)}


def _geometry(n_aisles=4):
    return et.Geometry([et.AisleGeom(i, _KEY, _BAYS_X, _BAYS_Y, _X_STEP, _Y_STEP)
                        for i in range(n_aisles)])


# ── the cheap score, spelled out exactly as `Inventory_Manager.pick_owed` computes it ─

def _cheap(bin_map: dict) -> float:
    """`pick_owed` over a bin map, without a built warehouse behind it.

    The real method needs a warehouse, a catalogue and a stocking pass; this needs ten SKUs
    in known bins.  The arithmetic is copied deliberately and `test_pick_owed.py` is what
    ties the copy to the real method — a shared helper would let both tests pass on a
    helper that was wrong.
    """
    xs, ys = sec_per_inch(_CFG.x_speed), sec_per_inch(_CFG.y_speed)
    hand = {o.sku: per_pick(1.0, _CFG.pick_intercept,
                            handle_var(o.weight, o.volume(),
                                       _CFG.pick_weight_coef, _CFG.pick_volume_coef,
                                       _CFG.pick_weight_fn, _CFG.pick_volume_fn),
                            _QTY.get(o.sku, 0.0), _CFG.pick_per_item)
            for o in _ORDERS}
    owed = 0.0
    for sku, w in _LINES.items():
        bins = bin_map.get(sku) or []
        if not bins:
            continue
        total = 0.0
        for _aid, bx, by, _q in bins:
            x_phys, y_phys = bx * _X_STEP, by * _Y_STEP
            total += (xs * x_phys + ys * y_phys
                      + height_multiplier(_CFG.height_brackets, y_phys) * hand[sku])
        owed += w * (total / len(bins))
    return owed


def _exact(bin_map: dict, geometry) -> float:
    """Seconds per unit from the closed form over the same bin map."""
    return float(expected_pick_over(bin_map, geometry, _ORDERS, _CFG,
                                    lines=5000.0, cv=0.3)['s_pick'])


# ── the placements ───────────────────────────────────────────────────────────────────

def _placement(order_of_skus, aisles=4):
    """Lay the SKUs across the bays, cheapest location to the first SKU given.

    Slot order is (aisle, bayX, bayY): aisle 0's bay (1,1) is the near, low, cheap one and
    the far aisle's top bay is the dearest.  Bays are 1-indexed because that is what the
    closed form's `Site` reads (`s.col - 1`, `s.row - 1`).
    """
    slots = [(a, x, y) for a in range(aisles)
             for x in range(1, _BAYS_X + 1) for y in range(1, _BAYS_Y + 1)]
    # REFUSE rather than zip-truncate.  A geometry too small for the catalogue drops the
    # TAIL of whatever order it was handed -- the coldest SKUs under `hot_first` and the
    # HOTTEST ones under `cold_first` -- and an unplaced SKU is free to both models, so the
    # inverted placement would score as the cheap one. That is the census inversion, and
    # finding it here as a silent pass rather than as a failure is exactly what this whole
    # file exists to prevent elsewhere.
    if len(order_of_skus) > len(slots):
        raise AssertionError(
            f'{aisles} aisles hold {len(slots)} bins and the fixture has '
            f'{len(order_of_skus)} SKUs; the overflow would be UNPLACED, which both models '
            f'price at zero, and the comparison would be about availability')
    return {sku: [(a, x, y, int(_QTY[sku]))]
            for sku, (a, x, y) in zip(order_of_skus, slots)}


_HOT_FIRST = list(_SKUS)                    # demand-descending: hot SKUs in the near aisle
_COLD_FIRST = list(reversed(_SKUS))         # the inversion: hot SKUs in the far one


# ── the tests ────────────────────────────────────────────────────────────────────────

def test_both_models_call_the_inverted_placement_the_dearer_one():
    """THE CHECK THE PROXY'S USE RESTS ON, at the resolution both models actually have.

    Demand-descending against demand-ascending: the hottest SKUs in the near, low bins
    versus the same SKUs in the far, high ones.  Both models must call the second dearer.
    Separations are asserted to be MATERIAL, so this cannot pass on two nearly-equal
    numbers falling the right way -- which is what the first draft of this test did.
    """
    geom = _geometry()
    hot, cold = _placement(_HOT_FIRST), _placement(_COLD_FIRST)
    c_hot, c_cold = _cheap(hot), _cheap(cold)
    e_hot, e_cold = _exact(hot, geom), _exact(cold, geom)

    assert c_hot < c_cold, f'the proxy: {c_hot} vs {c_cold}'
    assert e_hot < e_cold, f'the closed form: {e_hot} vs {e_cold}'
    assert (c_cold - c_hot) / c_hot > 0.05, (
        f'the proxy separates these two by {(c_cold - c_hot) / c_hot:.2%}, which is small '
        f'enough that the direction is not evidence')
    assert (e_cold - e_hot) / e_hot > 0.005, (
        f'the closed form separates these two by {(e_cold - e_hot) / e_hot:.2%}. It is '
        f'insensitive to aisle choice by construction (one sweep per aisle per day), so '
        f'its whole signal here is the height bracket; below this it has none.')


def test_the_closed_forms_agreement_comes_from_height_not_from_travel():
    """THE LIMIT, pinned as a fact rather than discovered again later.

    Someone reading `exact_check: agree` in a ranking document will take it as two
    independent models of pick work agreeing.  It is weaker than that, and this measures by
    how much: the closed form walks every aisle once a day, so its travel term is a
    rounding error beside handling, and the placement signal it does carry is the height
    multiplier -- a term the proxy shares.

    The proxy is the opposite: travel IS its signal.  That is why the two are compared as
    ORDERS and why a disagreement is the informative direction.
    """
    geom = _geometry()
    hot, cold = _placement(_HOT_FIRST), _placement(_COLD_FIRST)
    a = expected_pick_over(hot, geom, _ORDERS, _CFG, lines=5000.0, cv=0.3)
    b = expected_pick_over(cold, geom, _ORDERS, _CFG, lines=5000.0, cv=0.3)

    travel_share = (a['travel_x_s'] + a['travel_y_s']) / a['total_s']
    assert travel_share < 0.02, (
        f'travel is now {travel_share:.2%} of the closed-form day, up from the 0.4% this '
        f'test was written against. If that is deliberate -- a smaller cart, a per-task '
        f'sweep -- the closed form has become a real independent check of travel and this '
        f'assertion should be replaced by one, not relaxed.')

    d_travel = abs((b['travel_x_s'] + b['travel_y_s']) - (a['travel_x_s'] + a['travel_y_s']))
    d_hand = abs(b['handling_s'] - a['handling_s'])
    assert d_hand > 100 * d_travel, (
        f'inverting the placement moved the closed form\'s handling by {d_hand:.3f}s and '
        f'its travel by {d_travel:.3f}s. The claim this pins is that height, not distance, '
        f'is where its placement signal lives.')


def test_the_two_models_are_not_the_same_number():
    """NON-VACUITY, and the reason this file compares ORDERS.

    If the two returned the same value, agreement would be trivial.  They are different
    models at different grains — seconds owed by a whole planned script, against seconds
    per unit — and the gap is large.
    """
    geom = _geometry()
    p = _placement(_HOT_FIRST)
    c, e = _cheap(p), _exact(p, geom)
    assert c > 0 and e > 0
    assert c / e > 10.0, (
        f'the proxy ({c}) and the closed form ({e}) are now within one order of magnitude '
        f'of each other; check they have not been collapsed into the same computation, '
        f'which would make the agreement test vacuous')


def test_moving_one_hot_sku_to_the_far_aisle_moves_both_the_same_way():
    """The same claim with one variable.

    Everything is held fixed except the location of the single hottest SKU.  Both models
    must get DEARER.  A proxy that moved the other way on a one-SKU change is one that
    inverts on exactly the comparison an unloading policy produces.
    """
    geom = _geometry()
    before = _placement(_HOT_FIRST)
    far = (3, _BAYS_X, _BAYS_Y)
    after = dict(before)
    after[_SKUS[0]] = [(*far, int(_QTY[_SKUS[0]]))]

    assert _cheap(after) > _cheap(before), 'the proxy did not get dearer'
    assert _exact(after, geom) > _exact(before, geom), 'the closed form did not get dearer'

    # NON-VACUITY: moving the COLDEST SKU the same distance moves the proxy far less, so
    # it is the demand weight doing the work and not the distance alone.
    cold = dict(before)
    cold[_SKUS[-1]] = [(*far, int(_QTY[_SKUS[-1]]))]
    assert (_cheap(after) - _cheap(before)) > (_cheap(cold) - _cheap(before)) * 10


def test_an_unplaced_hot_sku_is_free_to_the_proxy_and_the_closed_form_cannot_price_it_either():
    """Why the census exists, demonstrated against the independent model.

    Dropping the hottest SKU off the shelf makes the PROXY CHEAPER — there is no bin to
    walk to, so its lines cost nothing.  That is the inversion `unservable_weight` is
    reported for.  The closed form counts the same SKU in `unplaced_skus` rather than
    charging it, so the two AGREE that neither can price absence — which is exactly why
    the ranking refuses on a material census instead of trusting either.
    """
    geom = _geometry()
    full = _placement(_HOT_FIRST)
    starved = {k: v for k, v in full.items() if k != _SKUS[0]}

    assert _cheap(starved) < _cheap(full), (
        'dropping the hottest SKU off the shelf did not make the proxy cheaper, so the '
        'inversion the census exists to catch is no longer there to catch')

    dist = et.PlacementDist.initial(starved, geom)
    rates = et.accumulate(_ORDERS, _CFG, dist, geom)
    assert rates.unplaced_skus == 1, (
        f'the closed form recorded {rates.unplaced_skus} unplaced SKUs, not the one that '
        f'was taken off the shelf')


def test_the_proxy_separates_a_spread_placement_that_the_closed_form_cannot():
    """The other half of the limit above, stated from the proxy's side.

    Dealing the hot SKUs ONE PER AISLE instead of packing them into the near one is what a
    placement rule with no sense of distance produces, and it is the shape an unloading
    policy that shelves stock wherever there is room lands in. The proxy prices it between
    the two extremes. The closed form CANNOT: it walks every aisle once a day either way,
    so spreading the demand across aisles changes nothing it measures.

    This is the case that decides the metric choice. If the ranking scored the closed form
    it would call these two placements identical; the proxy is what has an opinion.
    """
    geom = _geometry()
    per = [[] for _ in range(4)]
    for i, sku in enumerate(_SKUS):
        per[i % 4].append(sku)
    spread = _placement([sku for group in per for sku in group])
    hot, cold = _placement(_HOT_FIRST), _placement(_COLD_FIRST)

    assert _cheap(hot) < _cheap(spread) < _cheap(cold), (
        f'the proxy no longer prices a spread placement between the two extremes: '
        f'{_cheap(hot)} / {_cheap(spread)} / {_cheap(cold)}')
    assert abs(_exact(spread, geom) - _exact(hot, geom)) < 1e-6, (
        f'the closed form now distinguishes a spread placement from a packed one '
        f'({_exact(spread, geom)} vs {_exact(hot, geom)}). That would make it a real '
        f'independent check of aisle choice -- good news, and the limit documented in this '
        f'module and on `run_unload_ranking.exact_check` needs rewriting rather than this '
        f'assertion relaxing.')


@pytest.mark.parametrize('n_aisles', [2, 4, 8])
def test_the_agreement_is_not_an_artifact_of_one_geometry(n_aisles):
    """The same claim over three widths: a narrow site where every bin is close, and a
    wide one where the far aisles dominate the walk."""
    geom = _geometry(n_aisles)
    hot = _placement(_HOT_FIRST, n_aisles)
    cold = _placement(_COLD_FIRST, n_aisles)
    assert _cheap(hot) < _cheap(cold), (n_aisles, _cheap(hot), _cheap(cold))
    assert _exact(hot, geom) < _exact(cold, geom), (n_aisles, _exact(hot, geom),
                                                    _exact(cold, geom))
