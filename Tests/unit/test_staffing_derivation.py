"""test_staffing_derivation.py — the calibrated era's staffing derivation is arithmetic from
declared inputs and the closed-form constants the harness resolves at setup.

The derivation (`Optimization/simconfig/staffing.py`) is a PURE module: inputs + the resolved
constants + the catalogue / script totals in, the derived dict out.  Every test here
asserts the chain against a hand computation -- including the ceilings, the leaf's share of a
site crew and the clamp on a saturated batch -- without a run, a DB or CONFIG
(.scratch/department-calibration, "Define the calibrated era", "Design the staffing record",
"Declare the equilibrium bands").

There is NO calibration record and no reference run ("Derive the expected-travel closed form"):
the constants are closed-form expectations computed at setup, so what this file covers on the
constants' side is their PROVENANCE and shape, not a loader.  `s_pick` and `s_put` are both
keyed by CHANNEL ("Give put-away a per-channel expected travel", 2026-09-08) -- the put price
was one site value until then, and averaging it across two geometries is the defect several
tests here now pin.

Run:  python -m pytest Tests/unit/test_staffing_derivation.py -q
"""
from __future__ import annotations

import math
import types

import pytest

from Optimization.simconfig import staffing as st
from Optimization.simconfig.constants import PROVENANCE
from Warehouse.catalog.Order import Order
from Warehouse.kernel.regime import FULFILLMENT, STORE

S = 28800.0


# ── a tiny catalogue, built the production way ───────────────────────────────────────────

def _order(sku, *, freq, qty, eq=60, rp=20, lead=0.0, weight=10, dims=(10, 10, 10),
           handling='conveyable', category='seasonal'):
    """Build the SKU the production way, then DECLARE its level the production way.

    `Order.build` takes no level since ADR-0002 -- the catalogue carries demand and geometry,
    a level is a run's declaration -- so `declare_stock` is the second half of the fixture.
    It is load-bearing here: `reorder_lot` reads `equilibrium_qty` / `reorder_point`, and the
    whole supply side of the derivation (`implied_reorders`) is priced off the lot."""
    return Order.build(sku, handling, category, *dims, weight, freq, qty,
                       lead_time_mean=lead, supply_cv=0.0).declare_stock(eq, rp)


@pytest.fixture()
def pricing():
    return st.PricingConfig(name='t', intercept=1.0, per_item=0.5, weight_coef=0.0,
                            volume_coef=0.0, weight_fn='log', volume_fn='log')


@pytest.fixture()
def catalogue():
    Order.next_sku = 1
    return [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
            _order(3, freq=0.25, qty=8.0)]


class _Batch:
    def __init__(self, items):
        self.items = items


# ── stage A: the catalogue alone ───────────────────────────────────────────────────────────

def test_analytic_pick_is_the_frequency_weighted_line_cost_per_unit(catalogue, pricing):
    """One intercept per LINE, the per-item charge per unit, handling zero here (coefs 0):
    Σ w·(1 + 0.5·q) ÷ Σ w·q, and units per line = Σ w·q ÷ Σ w -- where `q` is the MEAN of
    the SKU's stamped line law, `λ + e^-λ` for `max(1, Poisson(λ))`.  Before "Stamp the line
    distribution on the SKU" this read `max(1, λ)` = 4.5 units per line here; the `e^-λ` term
    is the drift the stamp ended."""
    a = st.analytic_pick(catalogue, pricing)
    q = {sku: lam + math.exp(-lam) for sku, lam in ((1, 4.0), (2, 2.0), (3, 8.0))}
    w_q = 0.5 * q[1] + 0.25 * q[2] + 0.25 * q[3]
    assert w_q > 4.5, 'the exact mean sits above the old max(1, λ) reading'
    sec = 0.5 * (1 + 0.5 * q[1]) + 0.25 * (1 + 0.5 * q[2]) + 0.25 * (1 + 0.5 * q[3])
    assert math.isclose(a['seconds_per_unit'], sec / w_q)
    assert math.isclose(a['units_per_line'], w_q / 1.0)
    assert a['n_skus'] == 3 and a['pricing_config'] == 't'
    assert a['line_families'] == {'poisson_max1': 3}      # every derived number names its law


def test_analytic_pick_of_an_empty_section_is_zero_not_an_error(pricing):
    a = st.analytic_pick([], pricing)
    assert a == {'seconds_per_unit': 0.0, 'units_per_line': 0.0, 'n_skus': 0,
                 'pricing_config': 't', 'line_families': {}}


def test_the_chain_from_pickers_to_batch_content_by_hand():
    """K=10, S=28,800, ρ=0.85 buys 244,800 s; at 12 s/unit that is 20,400 units/day; at 4.5
    units per line that is 4,533.3 lines over 30,000 SKUs = a mean fraction of 0.1511, with
    the declared coefficient of variation (0.05/0.15) kept."""
    cap = st.pick_capacity(10, S, 0.85)
    assert cap == 244_800.0
    D = st.daily_demand(cap, 12.0)
    assert D == 20_400.0
    b = st.batch_content(D, 4.5, 30_000, declared_mean=0.15, declared_std=0.05)
    assert math.isclose(b['mean_lines'], 20_400 / 4.5)
    assert math.isclose(b['mean_fraction'], (20_400 / 4.5) / 30_000)
    assert math.isclose(b['std_fraction'], b['mean_fraction'] / 3)
    assert b['saturated'] is False


def test_a_crew_that_asks_for_more_lines_than_skus_is_clamped_and_flagged():
    b = st.batch_content(1_000_000.0, 1.0, 300, declared_mean=0.15, declared_std=0.05)
    assert b['saturated'] is True and b['mean_fraction'] == 1.0
    assert math.isclose(b['std_fraction'], 1.0 / 3)


def test_zero_s_pick_is_refused_not_divided_by():
    with pytest.raises(ValueError):
        st.daily_demand(1.0, 0.0)


# ── stage B: the script ────────────────────────────────────────────────────────────────────

def test_script_totals_count_units_lines_and_unknown_skus(catalogue, pricing):
    by_sku = {c.sku: c for c in catalogue}
    t = st.script_totals([_Batch({1: 4, 2: 2}), _Batch({1: 3, 99: 5})], by_sku, pricing)
    assert t.batches == 2 and t.units == 9 and t.lines == 3 and t.unknown_skus == 1
    assert t.per_sku_units == {1: 7.0, 2: 2.0}
    # each line: 1 + 0.5·q (handling zero) -> (1+2) + (1+1) + (1+1.5)
    assert math.isclose(t.analytic_pick_s, 3 + 2 + 2.5)
    assert t.per_day(t.units) == 4.5


def test_reorder_lot_is_the_order_up_to_rule_at_the_reorder_point_above_the_floor():
    c = _order(7, freq=0.1, qty=1.0, eq=60, rp=20, lead=0.0)
    assert st.reorder_lot(c) == 40
    c2 = _order(8, freq=0.1, qty=1.0, eq=60, rp=20, lead=1.0)   # pipeline = round(20·1/2) = 10
    assert st.reorder_lot(c2) == 50
    # The era's STAMPED pipeline replaces the heuristic (`Order.pipeline_allowance`).
    c2.pipeline_qty = 4
    assert st.reorder_lot(c2) == 44


def test_reorder_lot_under_base_stock_is_the_mean_line():
    """`rp = Q - 1` ("Choose the coverage floor", decision 1): the sampler draws distinct
    SKUs per batch, so every line fires an order for exactly what it took -- the expected lot
    is E[line] = λ + e^-λ, NOT the old `max(1, Q + pipeline - rp)` = 1, which priced one PACK
    per unit and over-counted a ten-unit line's receiving load tenfold."""
    c3 = _order(9, freq=0.1, qty=1.0, eq=5, rp=4)
    assert math.isclose(st.reorder_lot(c3), 1.0 + math.exp(-1.0))
    c4 = _order(10, freq=0.1, qty=10.0, eq=11, rp=10, lead=2.0)
    assert math.isclose(st.reorder_lot(c4), 10.0 + math.exp(-10.0))
    c4.pipeline_qty = 7                                  # a line clears P + 1 = 8: every line fires
    assert math.isclose(st.reorder_lot(c4), 10.0 + math.exp(-10.0))
    c4.pipeline_qty = 12                                 # a line does not: the lot is at least P + 1
    assert st.reorder_lot(c4) == 13.0
    # A duck-typed order with no line law takes the position-at-rp rule.
    class _Duck:
        equilibrium_qty, reorder_point, lead_time_mean = 5, 4, 0.0
    assert st.reorder_lot(_Duck()) == 1


def test_implied_reorders_pack_each_lot_and_price_receiving_exactly(catalogue, pricing):
    """SKU 1: 8 units demanded over a 40-unit lot = 0.2 reorders; the packer decides the
    packs; receiving is priced per PACK (intercept + per-item once, handling per unit) with
    the put-away scale chain 0.5 / 0.2 / 1.0."""
    from Inbound.pack import receive
    from Inbound.unload import unload_cost
    by_sku = {c.sku: c for c in catalogue}
    t = st.script_totals([_Batch({1: 8})], by_sku, pricing)
    st.implied_reorders(t, by_sku, pricing, f_put=1.0, f_recv=1.0,
                        put_intercept_scale=0.5, put_item_ratio=0.2, recv_intercept_scale=1.0)
    c = by_sku[1]
    plan = receive(c, 40)
    assert plan.packed_qty == 40
    put_cost = pricing.put(intercept_scale=0.5, item_ratio=0.2)
    assert (put_cost.intercept, put_cost.per_item) == (0.5, 0.1)
    recv_cost = pricing.recv(put_intercept_scale=0.5, put_item_ratio=0.2,
                             recv_intercept_scale=1.0)
    exp_recv = sum(unload_cost(c.weight, c.volume(), u.quantity, recv_cost) for u in plan.units)
    assert math.isclose(t.packs, 0.2 * plan.unit_count)
    assert math.isclose(t.put_units, 0.2 * 40)
    assert math.isclose(t.recv_s, 0.2 * exp_recv)
    # put-away at ground: per pack 0.5 + q·0.1 (handling zero)
    assert math.isclose(t.put_s, 0.2 * sum(0.5 + u.quantity * 0.1 for u in plan.units))


def test_f_put_and_f_recv_scale_their_loads_and_nothing_else(catalogue, pricing):
    by_sku = {c.sku: c for c in catalogue}
    base = st.implied_reorders(st.script_totals([_Batch({1: 8})], by_sku, pricing), by_sku,
                               pricing, f_put=1.0, f_recv=1.0, put_intercept_scale=0.5,
                               put_item_ratio=0.2, recv_intercept_scale=1.0)
    grow = st.implied_reorders(st.script_totals([_Batch({1: 8})], by_sku, pricing), by_sku,
                               pricing, f_put=2.0, f_recv=0.5, put_intercept_scale=0.5,
                               put_item_ratio=0.2, recv_intercept_scale=1.0)
    assert math.isclose(grow.put_units, 2 * base.put_units)
    assert math.isclose(grow.put_s, 2 * base.put_s)
    assert math.isclose(grow.packs, 0.5 * base.packs)
    assert math.isclose(grow.recv_s, 0.5 * base.recv_s)
    assert grow.units == base.units and grow.lines == base.lines


# ── the crews and the bands ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('load, expected', [
    (0.0, 0),                      # no work, nobody
    (1.0, 1),                      # any work, at least one person
    (24_480.0, 1),                 # exactly S·ρ -> 1
    (24_480.0 + 1e-6, 2),          # a hair over -> the ceiling bites
    (100_000.0, 5),                # 100,000 / 24,480 = 4.08 -> 5
])
def test_crew_size_is_the_ceiling_with_a_floor_of_one(load, expected):
    assert st.crew_size(load, S, 0.85) == expected


def test_expected_utilization_is_after_the_ceiling_and_the_leafs_share():
    """A site crew of 5 sized for 100,000 s/day works at 100,000 / (5 x 28,800) = 0.694, not
    at ρ; a leaf carrying 40,000 of that load reads 0.278 against the WHOLE site crew."""
    crew = st.crew_size(100_000.0, S, 0.85)
    assert math.isclose(st.expected_utilization(100_000.0, crew, S), 100_000 / (5 * S))
    assert math.isclose(st.expected_utilization(40_000.0, crew, S), 40_000 / (5 * S))
    assert st.expected_utilization(1.0, 0, S) == 0.0


def _constants(s_pick_store=12.0, s_pick_ff=6.0, s_put_store=1.5, s_put_ff=1.125):
    """Both seconds-per-unit constants keyed by CHANNEL ("Give put-away a per-channel
    expected travel").  The put defaults are the `_script` fixtures' OWN ratios --
    30,000/20,000 = 1.5 on the store and 9,000/8,000 = 1.125 on fulfillment -- because a
    derived put price IS `put_s / put_units` for its channel.  A test that wants the old
    site-wide behaviour declares one price on both channels instead."""
    return {'s_pick': {'store': st.constant(s_pick_store, 'derived'),
                       'fulfillment': st.constant(s_pick_ff, 'derived')},
            's_put': {'store': st.constant(s_put_store, 'derived'),
                      'fulfillment': st.constant(s_put_ff, 'derived')}}


def _inputs(**over):
    base = {'store_pickers': 10, 'ff_pickers': 4, 'rho_pick': 0.85, 'rho_put': 0.85,
            'rho_recv': 0.85, 'f_put': 1.0, 'f_recv': 1.0, 'band_tol': 0.10,
            'put_crew_mode': 'foot'}
    base.update(over)
    return base


def _script(units_per_day, put_units_per_day, put_s_per_day, packs_per_day, recv_s_per_day,
            days=10):
    t = st.ScriptTotals(batches=days, units=units_per_day * days, lines=100.0 * days,
                        analytic_pick_s=5.0 * units_per_day * days,
                        put_units=put_units_per_day * days, put_s=put_s_per_day * days,
                        packs=packs_per_day * days, recv_s=recv_s_per_day * days)
    return t


def test_derive_sizes_the_two_site_crews_over_both_channels_by_hand():
    """Store puts 20,000 units/day at ITS OWN 1.5 s = 30,000 s; fulfillment 8,000 at ITS OWN
    1.125 s = 9,000 s.  39,000 s/day of put-away -> ceil(39,000 / 24,480) = 2 putters.
    Receiving is EXACT seconds: 30,000 + 6,000 = 36,000 s/day -> ceil(36,000 / 24,480) = 2
    receivers.  Each leaf's expected utilization is its own load against the whole site crew.

    The put numbers are the per-channel ones since "Give put-away a per-channel expected
    travel".  This test used to price both channels at ONE site value of 3.0 s -- 84,000 s/day
    and 4 putters -- which is the defect: the store's real price is 1.33x the site average
    here and fulfillment's is 0.75x, so the site crew was sized off a total that was right
    while each leaf's band sat far from what it would actually do."""
    channels = {
        'store': {'pickers': 10, 'daily_demand_units': 20_400.0,
                  'analytic': {'seconds_per_unit': 5.0, 'units_per_line': 4.5, 'n_skus': 3,
                               'pricing_config': 'store'},
                  'batch': {'mean_lines': 1.0, 'mean_fraction': 0.1, 'std_fraction': 0.03,
                            'saturated': False}, 'n_skus': 3},
        'fulfillment': {'pickers': 4, 'daily_demand_units': 16_320.0,
                        'analytic': {'seconds_per_unit': 2.0, 'units_per_line': 1.5,
                                     'n_skus': 2, 'pricing_config': 'ful'},
                        'batch': {'mean_lines': 1.0, 'mean_fraction': 0.2,
                                  'std_fraction': 0.05, 'saturated': False}, 'n_skus': 2},
    }
    scripts = {'store': _script(20_000, 20_000, 30_000, 500, 30_000),
               'fulfillment': _script(8_000, 8_000, 9_000, 3_000, 6_000)}
    d = st.derive(inputs=_inputs(), constants=_constants(), day_seconds=S, channels=channels,
                  scripts=scripts, pricing_names={'store': 'store', 'fulfillment': 'ful'})
    assert d['provenance'] == 'derived'
    assert d['put']['crew'] == 2
    assert math.isclose(d['put']['load_seconds_per_day'], 39_000.0)
    assert math.isclose(d['put']['expected_utilization']['store'], 30_000 / (2 * S))
    assert math.isclose(d['put']['expected_utilization']['fulfillment'], 9_000 / (2 * S))
    # The record carries the price PER CHANNEL and no site scalar beside it: a site-wide
    # `s_put` is exactly what a future consumer could price something from.
    assert 'value' not in d['put']['s_put'], 'put.s_put must be a channel map, not a constant'
    assert set(d['put']['s_put']) == {'store', 'fulfillment'}
    assert math.isclose(d['put']['s_put']['store']['value'], 1.5)
    assert math.isclose(d['put']['s_put']['fulfillment']['value'], 1.125)
    # `load_units_per_day` is the OTHER half of the site average a reader must now compute
    # for themselves, so it is pinned here rather than left as an unread field.
    assert math.isclose(d['put']['load_units_per_day'], 28_000.0)
    assert math.isclose(d['put']['load_seconds_per_day'] / d['put']['load_units_per_day'],
                        39_000 / 28_000)
    assert d['receiving']['crew'] == 2
    assert math.isclose(d['receiving']['load_seconds_per_day'], 36_000.0)
    assert math.isclose(d['receiving']['load_packs_per_day'], 3_500.0)
    assert math.isclose(d['receiving']['s_recv']['value'], 36_000 / 3_500)
    assert d['receiving']['s_recv']['provenance'] == 'derived'
    assert math.isclose(d['receiving']['expected_utilization']['store'], 30_000 / (2 * S))
    # picking: the SERVED units per day (`daily_demand_units` = capacity / s_pick, the fixed
    # point) at s_pick against K x S -- which is ρ by construction, the declared headroom.
    # The script's DEMANDED units (20,000 / 8,000 a day here) are recorded beside it and do
    # not move the band ("Build the line floor": under the floor they exceed the served ones
    # by the first-pass shortfall, and pricing them read the crews at 0.98 with no headroom).
    assert math.isclose(d['channels']['store']['expected_utilization']['pick'],
                        20_400 * 12.0 / (10 * S))
    assert math.isclose(d['channels']['store']['expected_utilization']['pick'], 0.85)
    assert math.isclose(d['channels']['fulfillment']['expected_utilization']['pick'],
                        16_320 * 6.0 / (4 * S))
    assert math.isclose(d['channels']['fulfillment']['expected_utilization']['pick'], 0.85)
    assert d['channels']['store']['script']['units_per_day'] == 20_000
    assert d['channels']['store']['pick_capacity_s'] == 10 * S * 0.85
    assert d['channels']['store']['script']['analytic_s_pick'] == 5.0
    assert 'k_max_exceeded' not in d, 'k_max retired with the calibration record'


def test_an_absent_channel_contributes_nothing_and_is_recorded_as_absent():
    channels = {'store': {'pickers': 10, 'daily_demand_units': 1.0,
                          'analytic': {}, 'batch': {}, 'n_skus': 1}}
    scripts = {'store': _script(20_000, 20_000, 30_000, 500, 30_000)}
    d = st.derive(inputs=_inputs(), constants=_constants(), day_seconds=S, channels=channels,
                  scripts=scripts, pricing_names={'store': 'store'})
    assert d['channels']['fulfillment'] is None
    assert d['put']['crew'] == 2                    # 30,000 / 24,480 = 1.23 -> 2
    assert list(d['put']['expected_utilization']) == ['store']
    assert list(d['put']['s_put']) == ['store'], 'an absent channel carries no put price'
    # A STORE-ONLY run is a strict NO-OP under the per-channel price, because a one-channel
    # site average IS that channel's own ratio.  Stated absolutely, so the revert would fail:
    # the load must be the script's own put seconds.
    assert math.isclose(d['put']['load_seconds_per_day'],
                        scripts['store'].per_day(scripts['store'].put_s)), \
        'a single-channel site has nothing to average, so the load is its own put_s'


def test_a_declared_put_price_reaches_the_load_and_an_undeclared_one_takes_the_scripts_seconds():
    """The one behavioural guard that `derive` reads the CONSTANT rather than the script.

    Derived, each channel's constant IS `put_s / put_units`, so `units x price` reproduces the
    scripts' own seconds and the site load is 39,000.  Declared, that channel alone is charged
    `units x the declaration` -- so an implementation that shortcut to `t.per_day(t.put_s)`
    would pass every other test in this file (the derived numbers agree by construction) and
    fail only here, with a declared price that never reaches a number.

    What this does NOT cover: whether the HARNESS builds the declared constant in the first
    place. That lives in `workunits.put_constant`, which has its own tests."""
    channels = {'store': {'pickers': 10, 'daily_demand_units': 20_400.0,
                          'analytic': {}, 'batch': {}, 'n_skus': 3},
                'fulfillment': {'pickers': 4, 'daily_demand_units': 16_320.0,
                                'analytic': {}, 'batch': {}, 'n_skus': 2}}
    scripts = {'store': _script(20_000, 20_000, 30_000, 500, 30_000),
               'fulfillment': _script(8_000, 8_000, 9_000, 3_000, 6_000)}
    names = {'store': 'store', 'fulfillment': 'ful'}

    # both DERIVED: units x (put_s / put_units) reproduces the script's own seconds exactly
    d = st.derive(inputs=_inputs(), constants=_constants(), day_seconds=S, channels=channels,
                  scripts=scripts, pricing_names=names)
    assert math.isclose(d['put']['load_seconds_per_day'], 39_000.0)      # 30,000 + 9,000

    # the store DECLARED at 3.0: that channel alone moves, fulfillment is untouched
    c = _constants()
    c['s_put']['store'] = st.constant(3.0, 'declared', source='override', expected=1.5)
    d2 = st.derive(inputs=_inputs(), constants=c, day_seconds=S, channels=channels,
                   scripts=scripts, pricing_names=names)
    assert math.isclose(d2['put']['load_seconds_per_day'], 69_000.0)     # 60,000 + 9,000
    assert d2['put']['s_put']['store']['provenance'] == 'declared'
    assert math.isclose(d2['put']['s_put']['store']['expected'], 1.5)
    assert math.isclose(d2['put']['expected_utilization']['fulfillment'],
                        9_000 / (d2['put']['crew'] * S))


def test_the_site_total_is_what_a_single_average_price_used_to_get_right():
    """The regression this change must NOT cause: pricing each channel at its own expectation
    leaves the SITE load -- and therefore the crew SIZE -- where a correctly-computed site
    average put it.  That is why the crew was right while both bands were wrong.

    The identity holds because the old site price was `sum(put_s) / sum(put_units)` over the
    same channels, so `sum(units_ch) x that` telescopes back to `sum(put_s)`.  It needs every
    channel on the same batch count (the next test) and it is a float computation, so this
    compares with a tolerance rather than `==`."""
    channels = {'store': {'pickers': 10, 'daily_demand_units': 20_400.0,
                          'analytic': {}, 'batch': {}, 'n_skus': 3},
                'fulfillment': {'pickers': 4, 'daily_demand_units': 16_320.0,
                                'analytic': {}, 'batch': {}, 'n_skus': 2}}
    scripts = {'store': _script(20_000, 20_000, 30_000, 500, 30_000),
               'fulfillment': _script(8_000, 8_000, 9_000, 3_000, 6_000)}
    site_price = ((scripts['store'].put_s + scripts['fulfillment'].put_s)
                  / (scripts['store'].put_units + scripts['fulfillment'].put_units))
    assert math.isclose(site_price, 39_000 / 28_000)      # the old constant, by hand
    d = st.derive(inputs=_inputs(), constants=_constants(), day_seconds=S, channels=channels,
                  scripts=scripts, pricing_names={'store': 'store', 'fulfillment': 'ful'})
    flat = st.derive(inputs=_inputs(),
                     constants=_constants(s_put_store=site_price, s_put_ff=site_price),
                     day_seconds=S, channels=channels, scripts=scripts,
                     pricing_names={'store': 'store', 'fulfillment': 'ful'})
    # ABSOLUTE first -- comparing derive() to derive() is scale- and offset-invariant, so on
    # its own it would pass an implementation that doubled every load.
    assert math.isclose(d['put']['load_seconds_per_day'], 39_000.0), \
        f"per-channel load {d['put']['load_seconds_per_day']} should be the scripts' own seconds"
    assert math.isclose(flat['put']['load_seconds_per_day'], 39_000.0), \
        f"site-average load {flat['put']['load_seconds_per_day']} should telescope to the same"
    assert math.isclose(d['put']['load_seconds_per_day'],
                        flat['put']['load_seconds_per_day'], rel_tol=1e-9)
    assert d['put']['crew'] == flat['put']['crew'] == 2
    # ...and the SPLIT is what moves: the store is under-charged and fulfillment over-charged
    # by a single average, in opposite directions.  Absolute, so a no-op passes nothing.
    assert math.isclose(d['put']['expected_utilization']['store'], 30_000 / (2 * S))
    assert math.isclose(flat['put']['expected_utilization']['store'],
                        20_000 * site_price / (2 * S))
    assert d['put']['expected_utilization']['store'] > \
        flat['put']['expected_utilization']['store'], \
        (f"store {d['put']['expected_utilization']['store']:.4f} should exceed the flat-priced "
         f"{flat['put']['expected_utilization']['store']:.4f}")
    assert d['put']['expected_utilization']['fulfillment'] < \
        flat['put']['expected_utilization']['fulfillment'], \
        (f"fulfillment {d['put']['expected_utilization']['fulfillment']:.4f} should sit below "
         f"the flat-priced {flat['put']['expected_utilization']['fulfillment']:.4f}")


def test_derive_refuses_channels_that_ran_different_batch_counts():
    """One batch is one site day, so two channels on different counts are two different days
    summed into one crew.  Guaranteed by construction in the single production caller (one
    `n_batches` for both), unasserted until the per-channel put price made the sum's meaning
    load-bearing."""
    channels = {'store': {'pickers': 10, 'daily_demand_units': 20_400.0,
                          'analytic': {}, 'batch': {}, 'n_skus': 3},
                'fulfillment': {'pickers': 4, 'daily_demand_units': 16_320.0,
                                'analytic': {}, 'batch': {}, 'n_skus': 2}}
    scripts = {'store': _script(20_000, 20_000, 30_000, 500, 30_000, days=10),
               'fulfillment': _script(8_000, 8_000, 9_000, 3_000, 6_000, days=20)}
    with pytest.raises(ValueError, match='different batch counts'):
        st.derive(inputs=_inputs(), constants=_constants(), day_seconds=S, channels=channels,
                  scripts=scripts, pricing_names={'store': 'store', 'fulfillment': 'ful'})


def test_constant_refuses_a_provenance_outside_the_five():
    with pytest.raises(ValueError):
        st.constant(1.0, 'guessed')
    for p in PROVENANCE:
        assert st.constant(1.0, p)['provenance'] == p


def test_derived_differs_compares_floats_with_a_tolerance_and_names_the_path():
    a = {'put': {'crew': 3, 'load': 1.0}, 'x': 'foot'}
    assert st.derived_differs(a, {'put': {'crew': 3, 'load': 1.0 + 1e-12}, 'x': 'foot'}) == []
    assert st.derived_differs(a, {'put': {'crew': 4, 'load': 1.0}, 'x': 'foot'}) == ['/put/crew']
    assert st.derived_differs(a, {'put': {'crew': 3}, 'x': 'foot'}) == ['/put/load']
    assert st.derived_differs(None, None) == []


def test_regime_orders_filters_by_regime_and_none_is_the_whole_catalogue(catalogue):
    ff = _order(50, freq=0.1, qty=1.0, handling='non-conveyable', category='fulfillment')
    if st.regime_of(ff) != FULFILLMENT:
        pytest.skip('fixture does not build a fulfillment order on this catalogue schema')
    orders = catalogue + [ff]
    assert st.regime_orders(orders, None) == orders
    assert st.regime_orders(orders, FULFILLMENT) == [ff]
    assert len(st.regime_orders(orders, STORE)) == 3


# ── the put price's hidden precondition ────────────────────────────────────────────────

def test_a_binkey_names_its_own_regime_so_the_two_sections_can_never_share_a_class():
    """The per-channel put price is only per-channel BECAUSE the two sections' BinKeys are
    disjoint, and that was true by side effect rather than by statement.

    `expected_travel.put_site_pricer` prices a pack through `Geometry.class_mean_travel(key)`
    over the WHOLE SITE's aisles -- in the class-uniform branch it never reads the channel's
    placement distribution at all.  What keeps the store's 98.9 s/unit apart from
    fulfillment's 28.3 is that no BinKey is ever reachable from both sections: if one were,
    `class_mean_travel` would silently average both sections' aisles into one class and
    reinstate exactly the cross-geometry averaging that "Give put-away a per-channel expected
    travel" removed -- with no error anywhere.

    THE INVARIANT IS OVER THE WHOLE KEY, not its first two slots.  `binkey_of` returns
    `(handling, category, storage_size, unit_category)` and `regime_of` reads `unit_category`
    FIRST ("the strongest signal") before falling back to the handling/category pair -- so a
    unit can be fulfillment on slot 3 alone, with neither of the first two slots marked.  Both
    routes are exercised below; an earlier draft of this test asserted the marker was in
    `key[:2]` and was simply wrong.

    Both BRANCHES of `binkey_of` are covered too.  `Geometry.by_class` is keyed by AISLE keys,
    so the contamination this test exists to rule out can only arrive through the aisle branch
    -- checking only the unit branch would leave the real path green.
    """
    from Warehouse.inventory.inventory_common import binkey_of
    from Warehouse.kernel.regime import regime_of

    def _from_key(k):
        return FULFILLMENT if FULFILLMENT in k else STORE

    # ── the unit branch: (order.shc.handling, order.shc.category, size, unit_category) ──
    # Two REAL orders off the production builder, so a change in how the catalogue tags a
    # section breaks this rather than drifting past a hand-built namespace.
    Order.next_sku = 1
    real_store = _order(1, freq=0.5, qty=4.0, handling='conveyable', category='food')
    real_ff = _order(2, freq=0.5, qty=4.0, handling=FULFILLMENT, category=FULFILLMENT)
    assert regime_of(real_store) == STORE
    assert regime_of(real_ff) == FULFILLMENT

    def _unit(order, size, unit_category):
        return types.SimpleNamespace(storage_size=size, unit_category=unit_category,
                                     order=order)

    units = [
        _unit(real_store, 'medium', 'pallet'),          # store, marked nowhere
        _unit(real_store, 'singleton', 'singleton'),    # store, forward pick
        _unit(real_ff, 'ff_medium', FULFILLMENT),       # fulfillment on slots 0, 1 AND 3
        # fulfillment on slot 3 ALONE -- the case the `key[:2]` reading got wrong.
        _unit(real_store, 'ff_small', FULFILLMENT),
    ]
    for u in units:
        assert regime_of(u) == _from_key(binkey_of(u)), \
            f'unit {binkey_of(u)} is {regime_of(u)} but its key says {_from_key(binkey_of(u))}'

    # ── the aisle / bin branch: (handling_type, storage_type, size, unit_type) ──
    # This is the branch `Geometry.by_class` keys on, so it is the one that could actually
    # blend two sections' aisles into a single class.
    def _bin(handling, storage, size, unit_type):
        return types.SimpleNamespace(handling_type=handling, storage_type=storage,
                                     storage_size=size, unit_type=unit_type)

    bins = [
        _bin('conveyable', 'food', 'medium', 'pallet'),
        _bin('non-conveyable', 'furniture', 'large', 'pallet'),
        _bin(FULFILLMENT, FULFILLMENT, 'ff_medium', FULFILLMENT),
        _bin('conveyable', 'food', 'ff_small', FULFILLMENT),      # marked on slot 3 alone
    ]
    for b in bins:
        assert regime_of(b) == _from_key(binkey_of(b)), \
            f'bin {binkey_of(b)} is {regime_of(b)} but its key says {_from_key(binkey_of(b))}'

    # ...and therefore the two sections' class sets cannot intersect, on either branch.
    for objs in (units, bins):
        store_keys = {binkey_of(o) for o in objs if regime_of(o) == STORE}
        ff_keys = {binkey_of(o) for o in objs if regime_of(o) == FULFILLMENT}
        assert not (store_keys & ff_keys), \
            f'a class reachable from both sections would blend their aisles: {store_keys & ff_keys}'


# ── the harness seam that builds the per-channel constant ──────────────────────────────

def test_put_constant_prices_a_channel_at_its_own_ratio_and_carries_a_declaration_across():
    """`workunits.put_constant` is the other half of the per-channel put price: `derive` reads
    the constant, this builds it.  It had no test at all -- the whole derivation seam is
    covered only by source-text greps -- so each of these failed silently."""
    from Optimization.simdriver.workunits import put_constant

    store = _script(20_000, 20_000, 30_000, 500, 30_000)        # 1.5 s/unit
    ff = _script(8_000, 8_000, 9_000, 3_000, 6_000)             # 1.125 s/unit

    c = put_constant(store, None)
    assert math.isclose(c['value'], 1.5), 'a derived price is the channel own put_s / put_units'
    assert c['provenance'] == 'derived' and c['source'] == 'expected_travel'
    assert c['placement'] == 'uniform'
    assert math.isclose(put_constant(ff, None)['value'], 1.125)

    # ONE declared `--s-put` reaches BOTH channels, and each keeps its OWN displaced
    # expectation -- so the two constants differ in `expected` under a single flag.  A harness
    # that stamped the site ratio as `expected`, or stamped only one channel, breaks here.
    ds, df = put_constant(store, 3.0), put_constant(ff, 3.0)
    assert ds['provenance'] == df['provenance'] == 'declared'
    assert ds['source'] == df['source'] == 'override'
    assert math.isclose(ds['value'], 3.0) and math.isclose(df['value'], 3.0)
    assert math.isclose(ds['expected'], 1.5) and math.isclose(df['expected'], 1.125)
    assert not math.isclose(ds['expected'], df['expected']), \
        'one declaration, two channels, two different displaced expectations'

    # A section the script implies no put-away for prices at zero rather than dividing by it.
    empty = _script(0, 0, 0, 0, 0)
    assert put_constant(empty, None)['value'] == 0.0
    assert math.isclose(put_constant(empty, 2.0)['value'], 2.0)
    assert put_constant(empty, 2.0)['expected'] == 0.0
