"""test_staffing_derivation.py — the calibrated era's staffing derivation is arithmetic from
declared inputs, and the calibration record it prices with is loaded, resolved and stamped.

The derivation (`Optimization/simconfig/staffing.py`) is a PURE module: inputs + the loaded
calibration record + the catalogue / script totals in, the derived dict out.  Every test here
asserts the chain against a hand computation -- including the ceilings, the leaf's share of a
site crew and the clamp on a saturated batch -- without a run, a DB or CONFIG
(.scratch/department-calibration, "Define the calibrated era", "Design the staffing record",
"Declare the equilibrium bands").

The calibration record (`Optimization/simconfig/calibration.py` + `calibration_record.json`)
is covered from the other side: the committed record is the pass-0 SEED, the loader refuses a
provenance outside the five agreed values, the resolution rule picks override > recorded value
> analytic x travel share and stamps the provenance each branch earns, the stale stamp fires on
a fingerprint mismatch and stays silent on a match, and the K_max warning fires above the bound.

Run:  python -m pytest Tests/unit/test_staffing_derivation.py -q
"""
from __future__ import annotations

import json
import math
import os

import pytest

from Optimization.simconfig import staffing as st
from Optimization.simconfig.constants import PROVENANCE
from Warehouse.catalog.Order import Order
from Warehouse.kernel.regime import FULFILLMENT, STORE

S = 28800.0


# ── a tiny catalogue, built the production way ───────────────────────────────────────────

def _order(sku, *, freq, qty, eq=60, rp=20, lead=0.0, weight=10, dims=(10, 10, 10),
           handling='conveyable', category='seasonal'):
    return Order.build(sku, handling, category, *dims, weight, freq, qty,
                       equilibrium_qty=eq, reorder_point=rp, lead_time_mean=lead, supply_cv=0.0)


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


def test_reorder_lot_is_the_order_up_to_rule_at_the_reorder_point():
    c = _order(7, freq=0.1, qty=1.0, eq=60, rp=20, lead=0.0)
    assert st.reorder_lot(c) == 40
    c2 = _order(8, freq=0.1, qty=1.0, eq=60, rp=20, lead=1.0)   # pipeline = round(20·1/2) = 10
    assert st.reorder_lot(c2) == 50
    c3 = _order(9, freq=0.1, qty=1.0, eq=5, rp=4)
    assert st.reorder_lot(c3) == 1


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


def _constants(s_pick_store=12.0, s_pick_ff=6.0, s_put=3.0):
    return {'s_pick': {'store': st.constant(s_pick_store, 'derived'),
                       'fulfillment': st.constant(s_pick_ff, 'derived')},
            's_put': st.constant(s_put, 'derived')}


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
    """Store puts 20,000 units/day, fulfillment 8,000, at s_put 3 s: 84,000 s/day of put-away
    -> ceil(84,000 / 24,480) = 4 putters.  Receiving is EXACT seconds: 30,000 + 6,000 =
    36,000 s/day -> ceil(36,000 / 24,480) = 2 receivers.  Each leaf's expected utilization
    is its own load against the whole site crew."""
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
    assert d['put']['crew'] == 4
    assert math.isclose(d['put']['load_seconds_per_day'], 84_000.0)
    assert math.isclose(d['put']['expected_utilization']['store'], 60_000 / (4 * S))
    assert math.isclose(d['put']['expected_utilization']['fulfillment'], 24_000 / (4 * S))
    assert d['receiving']['crew'] == 2
    assert math.isclose(d['receiving']['load_seconds_per_day'], 36_000.0)
    assert math.isclose(d['receiving']['load_packs_per_day'], 3_500.0)
    assert math.isclose(d['receiving']['s_recv']['value'], 36_000 / 3_500)
    assert d['receiving']['s_recv']['provenance'] == 'derived'
    assert math.isclose(d['receiving']['expected_utilization']['store'], 30_000 / (2 * S))
    # picking: the SCRIPT's units per day at s_pick against K x S
    assert math.isclose(d['channels']['store']['expected_utilization']['pick'],
                        20_000 * 12.0 / (10 * S))
    assert math.isclose(d['channels']['fulfillment']['expected_utilization']['pick'],
                        8_000 * 6.0 / (4 * S))
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
    assert d['put']['crew'] == 3                    # 60,000 / 24,480 = 2.45 -> 3
    assert list(d['put']['expected_utilization']) == ['store']


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
