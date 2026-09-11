"""test_lead_aware_coverage.py -- the coverage record is declared AT the order-to-shelf lead.

"Declare the coverage against the inbound lead" (.scratch/department-calibration, 2026-09-10)
found that the era's reorder point stamped lead 0 off the catalogue while the trailer pipeline
realized ~1.8 site days, and the first yard-on era run failed fulfillment's supply clause on
every arm (0.148 against a stamped 0.025).  Built by "Build the lead-aware coverage record":
the lead is two additive stages -- the SKU's SUPPLIER lead (batches, read as days) plus the
pair's TRANSIT on the day grid, `E[ceil(L / D)]` over the trailer's lognormal law -- the fill
prices the shelf a line meets as the order-up-to position less the SKU's own prior lines still
in transit, the floor is solved at that lead, and the audit reports the realized lead beside
the stamp with the level it explains.

What this file pins, each against a hand computation or a seeded Monte-Carlo:

  * `coverage.transit_day_law`: spread 0 is `ceil(m / D)` literally, a zero median is no
    transit, and the pilot regime reads the ticket's table (1.766 at 480 min / 0.7 / 8 h);
  * the per-SKU lead (`sku_lead_days`) and how `rescale_section` stamps it -- byte-identical
    to the lead-free declaration at transit 0 with a batch read as a day;
  * `fill_rate`: EXACTLY the lead-free expression at lead 0 (same floats, same code path),
    Poisson-by-hand under a deterministic one-day transit, a two-point mixture, a seeded
    Monte-Carlo of the compound-Poisson form, the fast path against the law's own `pmf`;
  * the floor solved AT the lead is higher than the one solved at lead 0;
  * the record: `lead_block` / `transit_of`, the loop stamping the block and the curve, the
    rebuild reproducing the levels (and a pre-lead record declaring at transit 0);
  * the refusal: a supplier lead the trailer pipeline would discard is refused under the era
    with a trailer type, naming inbound 27 -- and only there;
  * the audit: `fill_at`, `realized_lead` by Little's law, the supply reading's lead block and
    the explained level, `expectations_for` reading the stamp or None, the batch frame
    carrying `units_ordered`;
  * the real tiny pair through `build_shared_assets`: inbound-on stamps the pilot transit and
    solves a higher floor, inbound-off stamps none, and an `lt1` sibling refuses.

Run:  python -m pytest Tests/unit/test_lead_aware_coverage.py -q
"""
from __future__ import annotations

import json
import logging
import math

import numpy as np
import pytest

from Optimization.config.sim_config import CONFIG, STAFFING_KEYS, INBOUND_KEYS, inbound_lead_law
from Optimization.simconfig import coverage as cov
from Optimization.simconfig import equilibrium as eq
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Demand import LineDistribution
from Warehouse.catalog.Order import Order

from test_coverage_rescale import _order, _Plan, _Meta, _fake_stage_a, _fake_seed, _tiny_pair
from test_equilibrium_check import _window, _check, _expectations, _staffing

_LOG = logging.getLogger('test_lead_aware_coverage')
D = 28800.0                                  # the site day, 8 h
PILOT = {'trailer_type': '53', 'lead_s': 480.0 * 60.0, 'lead_sigma': 0.7}
DAY = {'seconds': D, 'releases_per_day': 1}
C = 0.95
SIDE = math.sqrt(C)


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pois_cdf(lam, k):
    return sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    keys = (*STAFFING_KEYS, *INBOUND_KEYS, 'shift_drain_or_cap', 'releases_per_day')
    before = {k: CONFIG['global'].get(k) for k in keys}
    yield CONFIG['global']
    for k in keys:
        CONFIG['global'][k] = before[k]


# ═════════════════════════════════════════════════════════════════════════════════════════
# The transit on the day grid
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_transit_day_law_is_the_ceiling_of_the_lead_over_the_day():
    # Spread 0 is `ceil(m / D)` with probability one: exactly one day for a one-day median,
    # two for a day and a half, two for exactly two days; a zero median is no transit.
    one = cov.transit_day_law(480.0 * 60.0, 0.0, D)
    assert one['transit_days'] == 1.0 and one['pmf'] == {1: 1.0}
    assert cov.transit_day_law(1.5 * D, 0.0, D)['pmf'] == {2: 1.0}
    assert cov.transit_day_law(2.0 * D, 0.0, D)['pmf'] == {2: 1.0}
    none = cov.transit_day_law(0.0, 0.0, D)
    assert none['transit_days'] == 0.0 and none['pmf'] == {0: 1.0}
    assert cov.transit_day_law(0.0, 0.7, D)['pmf'] == {0: 1.0}
    # The pilot regime, by hand: P(k = 1) = Φ(ln(D / m) / σ) = Φ(0) = 1/2 exactly, and
    # E = 1 + Σ_k (1 - Φ(ln k / 0.7)) reads the ticket's 1.766.
    law = cov.transit_day_law(PILOT['lead_s'], PILOT['lead_sigma'], D)
    assert math.isclose(law['pmf'][1], 0.5, abs_tol=1e-12)
    want = 1.0 + sum(1.0 - _phi(math.log(k) / 0.7) for k in range(1, 5000))
    assert math.isclose(law['transit_days'], want, rel_tol=1e-9)
    assert math.isclose(law['transit_days'], 1.766, abs_tol=5e-4)
    assert math.isclose(sum(law['pmf'].values()), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(k * p for k, p in law['pmf'].items()), law['transit_days'],
                        abs_tol=1e-9)
    assert min(law['pmf']) == 1, 'a positive lead lands at the next drain at the earliest'
    assert (law['lead_s'], law['lead_sigma'], law['day_seconds']) == (28800.0, 0.7, D)


def test_transit_day_law_reads_the_tickets_table_and_refuses_bad_inputs():
    table = [(480, 0.3, 1.511), (480, 0.5, 1.600), (480, 1.0, 2.170), (240, 0.7, 1.192),
             (360, 0.7, 1.460), (600, 0.7, 2.084), (960, 0.7, 3.050)]
    for minutes, sigma, days in table:
        got = cov.transit_day_law(minutes * 60.0, sigma, D)['transit_days']
        assert math.isclose(got, days, abs_tol=6e-4), (minutes, sigma, got)
    # The continuous mean is the wrong number: the grid makes 1.28 read 1.77.
    assert cov.transit_day_law(PILOT['lead_s'], 0.7, D)['transit_days'] > \
        math.exp(0.7 ** 2 / 2.0)
    for bad in ((100.0, 0.5, 0.0), (-1.0, 0.0, D), (100.0, -0.1, D)):
        with pytest.raises(ValueError):
            cov.transit_day_law(*bad)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The lead per SKU, and the levels declared at it
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_lead_per_sku_is_the_supplier_lead_in_days_plus_the_transit():
    Order.next_sku = 1
    c = _order(1, freq=0.5, qty=4.0, lead=3.0, declare=False)
    assert cov.sku_lead_days(c) == 3.0                          # a batch read as a day
    assert cov.sku_lead_days(c, 1.766, 1.0) == pytest.approx(4.766)
    assert cov.sku_lead_days(c, 1.766, 0.5) == pytest.approx(3.266)   # two releases a day
    assert cov.sku_lead_days(_order(2, freq=0.5, qty=4.0, declare=False), 1.766) == 1.766
    assert cov.sku_lead_days(_order(3, freq=0.5, qty=4.0, lead=-2.0, declare=False)) == 0.0


def test_rescale_section_declares_at_the_lead_and_is_byte_identical_at_transit_zero():
    Order.next_sku = 1
    sec = [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
           _order(3, freq=0.25, qty=8.0, lead=3.0)]
    # The lead-free declaration is exactly what the defaults reproduce.
    base = cov.rescale_section(sec, 4.0, coverage_days=10.0, safety_days=2.0, floor_lines=1.0)
    levels0 = [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in sec]
    again = cov.rescale_section(sec, 4.0, coverage_days=10.0, safety_days=2.0, floor_lines=1.0,
                                transit_days=0.0, lead_unit_days=1.0)
    assert again == base
    assert [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in sec] == levels0
    assert (base['transit_days'], base['lead_unit_days']) == (0.0, 1.0)
    d = cov.daily_demand(sec, 4.0)
    assert math.isclose(base['lead_days'], d[3] * 3.0 / sum(d.values()))
    # At the pilot transit every SKU's lead is its attribute plus 1.766, and the levels and
    # the pipeline are the generator's formula at that lead.
    st = cov.rescale_section(sec, 4.0, coverage_days=10.0, safety_days=2.0, floor_lines=1.0,
                             transit_days=1.766, lead_unit_days=1.0)
    for c in sec:
        lead = c.lead_time_mean + 1.766
        q, rp = cov.stock_levels(d[c.sku], lead, 10.0, 2.0, cov.line_floor(c.demand.line, 1.0))
        assert (c.equilibrium_qty, c.reorder_point) == (q, rp)
        assert c.pipeline_qty == cov.pipeline_qty(d[c.sku], lead) == round(d[c.sku] * lead)
    assert sec[0].pipeline_qty > 0 and st['transit_days'] == 1.766
    assert math.isclose(st['lead_days'],
                        sum(d[c.sku] * (c.lead_time_mean + 1.766) for c in sec) / sum(d.values()))
    # ...and two releases a day halve the attribute's days, never the transit's.
    st2 = cov.rescale_section(sec, 4.0, coverage_days=10.0, safety_days=2.0, floor_lines=1.0,
                              transit_days=1.766, lead_unit_days=0.5)
    assert math.isclose(st2['lead_days'],
                        sum(d[c.sku] * (0.5 * c.lead_time_mean + 1.766) for c in sec)
                        / sum(d.values()))


# ═════════════════════════════════════════════════════════════════════════════════════════
# The lead-aware fill
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_at_lead_zero_the_fill_is_the_lead_free_expression_exactly():
    """The byte-identical discipline for a flag-off record: not a tolerance, the same code
    path.  A `transit` of no transit and a None transit answer the same floats, and the
    number is the lead-free hand formula of "Choose the coverage floor"."""
    Order.next_sku = 1
    one = [_order(1, freq=0.5, qty=2.0, eq=3, rp=2)]
    want = (3.0 - 8.0 * math.exp(-2.0)) / (2.0 + math.exp(-2.0))
    a = cov.fill_rate(one, 40.0)
    b = cov.fill_rate(one, 40.0, transit=cov.transit_day_law(0.0, 0.0, D))
    c = cov.fill_rate(one, 40.0, transit=None, lead_unit_days=1.0)
    assert a['fill_rate'] == b['fill_rate'] == c['fill_rate']
    assert math.isclose(a['fill_rate'], want, rel_tol=1e-12)
    assert a['lead_days'] == 0.0 and a['transit_days'] == 0.0 and a['pipeline_units'] == 0
    assert {k: v for k, v in a.items() if k not in ('lead_days', 'transit_days')} \
        == {k: v for k, v in b.items() if k not in ('lead_days', 'transit_days')}
    # A section of several lead-free SKUs, declared by `rescale_section`: same floats.
    Order.next_sku = 1
    sec = [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
           _order(3, freq=0.25, qty=8.0)]
    cov.rescale_section(sec, 4.0, coverage_days=10.0, safety_days=2.0, floor_lines=1.0)
    x = cov.fill_rate(sec, 4.0)['fill_rate']
    y = cov.fill_rate(sec, 4.0, transit=cov.transit_day_law(0.0, 0.7, D))['fill_rate']
    assert x == y


def _unit_line_sku(sku, *, eq, rp, pipeline, lead=0.0):
    """A SKU whose lines are always ONE unit (`max(1, Poisson(0))`), so the served units
    are `P(D <= S - 1)` and the compound Poisson collapses to a plain Poisson by hand.
    `Order.build` clamps the quantity rate to one unit, so the zero-rate law is stamped
    after the build (the SKU's `Demand` is a plain attribute)."""
    c = _order(sku, freq=1.0, qty=1.0, lead=lead, declare=False)
    c.demand.line = LineDistribution.poisson(0.0)
    assert c.demand.line.mean() == 1.0
    return c.declare_stock(eq, rp, pipeline_qty=pipeline)


def test_the_lead_aware_fill_is_poisson_by_hand_under_a_one_day_transit():
    """One SKU, unit lines at 2 lines a day, position S = Q + P = 3 + 1 = 4, a one-day
    transit: the prior lines in transit number Poisson(2), each one unit, and a line is
    served iff fewer than 4 are on the road: P(Poisson(2) <= 3) = e^-2 (1 + 2 + 2 + 4/3)."""
    Order.next_sku = 1
    one = [_unit_line_sku(1, eq=3, rp=2, pipeline=1)]
    law = cov.transit_day_law(1.0 * D, 0.0, D)                  # k = 1 with probability one
    got = cov.fill_rate(one, 2.0, transit=law)
    assert math.isclose(got['fill_rate'], _pois_cdf(2.0, 3), rel_tol=1e-12)
    assert math.isclose(got['fill_rate'], math.exp(-2.0) * 19.0 / 3.0, rel_tol=1e-12)
    assert got['lead_days'] == 1.0 and got['transit_days'] == 1.0 and got['pipeline_units'] == 1
    # A supplier lead of one batch ADDS a day on the grid: Poisson(4) <= 3.
    Order.next_sku = 1
    two = [_unit_line_sku(1, eq=3, rp=2, pipeline=1, lead=1.0)]
    got = cov.fill_rate(two, 2.0, transit=law)
    assert math.isclose(got['fill_rate'], _pois_cdf(4.0, 3), rel_tol=1e-12)
    assert got['lead_days'] == 2.0
    # ...and read as HALF a day (two releases a day) it rounds to no grid day at all.
    got = cov.fill_rate(two, 2.0, transit=law, lead_unit_days=0.5)
    assert math.isclose(got['fill_rate'], _pois_cdf(2.0, 3), rel_tol=1e-12)
    assert got['lead_days'] == 1.5
    # A two-point transit is the mixture of the two Poisson readings.
    mix = {'transit_days': 1.5, 'pmf': {1: 0.5, 2: 0.5}, 'lead_s': 0.0, 'lead_sigma': 0.0,
           'day_seconds': D}
    got = cov.fill_rate(one, 2.0, transit=mix)
    assert math.isclose(got['fill_rate'], 0.5 * _pois_cdf(2.0, 3) + 0.5 * _pois_cdf(4.0, 3),
                        rel_tol=1e-12)
    # More on the road serves less: the fill falls with the transit at fixed levels.
    fills = [cov.fill_rate(one, 2.0, transit=cov.transit_day_law(k * D, 0.0, D))['fill_rate']
             for k in (0, 1, 2, 3)]
    assert fills == sorted(fills, reverse=True) and fills[0] > fills[3]
    assert fills[0] == 1.0, 'nothing on the road: a unit line always meets a shelf of 4'


def test_the_lead_aware_fill_matches_a_monte_carlo_of_the_compound_poisson_form():
    """Poisson(3) lines at 1.5 lines a day, position 12, the pilot transit law: draw the
    grid day, the prior lines inside it and their units, and the line that meets the
    shelf; the closed form agrees to a quarter of a percent."""
    Order.next_sku = 1
    one = [_order(1, freq=1.0, qty=3.0, declare=False).declare_stock(8, 7, pipeline_qty=4)]
    law = cov.transit_day_law(PILOT['lead_s'], PILOT['lead_sigma'], D)
    got = cov.fill_rate(one, 1.5, transit=law)['fill_rate']
    rng = np.random.default_rng(7)
    M = 400_000
    ks = np.array(sorted(law['pmf']))
    ps = np.array([law['pmf'][k] for k in ks])
    k = rng.choice(ks, size=M, p=ps / ps.sum())
    N = rng.poisson(k * 1.5)
    n_max = int(N.max())
    units = np.maximum(1, rng.poisson(3.0, size=(M, n_max)))
    Dk = (units * (np.arange(n_max)[None, :] < N[:, None])).sum(axis=1)
    q = np.maximum(1, rng.poisson(3.0, size=M))
    served = np.minimum(q, np.maximum(0, 12 - Dk)).mean()
    mc = served / (3.0 + math.exp(-3.0))
    assert math.isclose(got, mc, rel_tol=2.5e-3), (got, mc)
    assert got < cov.fill_rate(one, 1.5)['fill_rate'] < 1.0


def test_the_fast_path_prices_the_laws_own_pmf_and_the_slow_path_agrees():
    lines = [LineDistribution.poisson(lam) for lam in (0.0, 0.5, 2.0, 7.3)]
    fast = cov._line_pmf(lines, 12)
    slow = np.stack([l.pmf(12) for l in lines])
    assert np.allclose(fast, slow, atol=1e-14)
    assert np.all(fast[:, 0] == 0.0)
    for r, l in enumerate(lines):
        assert math.isclose(fast[r, 1], math.exp(-l.params['lam']) * (1.0 + l.params['lam']))
        assert math.isclose(l.pmf(400).sum(), 1.0, abs_tol=1e-12)
    assert lines[0].pmf(5).tolist() == [0.0, 1.0, 0.0, 0.0, 0.0]      # a zero rate is one unit
    assert cov._line_pmf(lines, 1).shape == (4, 1) and cov._line_pmf(lines, 0).shape == (4, 0)
    # `_served_under_lead` at K = 0 is E[min(q, S)], the lead-free numerator.
    S = np.array([1, 4, 9, 30])
    got = cov._served_under_lead(lines, S, np.array([1.0] * 4), np.zeros(4, dtype=int), {0: 1.0})
    for r, l in enumerate(lines):
        assert math.isclose(got[r], l.expected_min(int(S[r])), rel_tol=1e-9)


def test_the_floor_solves_higher_at_the_lead_than_at_lead_zero():
    """The shelf side of the guarantee AT the lead ("Declare the coverage against the
    inbound lead", decision 5): the same confidence buys a higher floor once the previous
    line's lot is on the road, and the orders are left declared at that lead."""
    Order.next_sku = 1
    sec = [_order(i, freq=f, qty=q, declare=False)
           for i, (f, q) in enumerate([(0.5, 4.0), (0.3, 6.0), (0.2, 3.0), (0.4, 8.0)], 1)]
    n = 0.5                     # half a line a day: every SKU sits on its line floor
    at0 = cov.solve_floor_lines(sec, n, coverage_days=10.0, safety_days=2.0, fill_min=SIDE)
    assert not at0['at_lower_bound'], 'one line must not already clear the side'
    assert all(c.pipeline_qty == 0 for c in sec)
    law = cov.transit_day_law(PILOT['lead_s'], PILOT['lead_sigma'], D)
    at_lead = cov.solve_floor_lines(sec, n, coverage_days=10.0, safety_days=2.0, fill_min=SIDE,
                                    transit=law)
    assert at_lead['floor_lines'] > at0['floor_lines']
    assert at_lead['fill_rate'] >= SIDE and at0['fill_rate'] >= SIDE
    d = cov.daily_demand(sec, n)
    for c in sec:
        assert c.pipeline_qty == round(d[c.sku] * law['transit_days'])
    assert math.isclose(cov.fill_rate(sec, n, transit=law)['fill_rate'], at_lead['fill_rate'])
    # Priced at lead 0, the lead-solved floor over-clears; priced at the lead, the lead-0
    # floor falls short -- the two solves are two promises.
    cov.rescale_section(sec, n, coverage_days=10.0, safety_days=2.0,
                        floor_lines=at0['floor_lines'], transit_days=law['transit_days'])
    assert cov.fill_rate(sec, n, transit=law)['fill_rate'] < SIDE


# ═════════════════════════════════════════════════════════════════════════════════════════
# The record
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_lead_block_and_transit_of_round_trip_the_law():
    off = ec.lead_block(None, {'seconds': D, 'releases_per_day': None})
    assert off == {'transit_days': 0.0, 'provenance': 'derived', 'trailer_type': None,
                   'lead_s': 0.0, 'lead_sigma': 0.0, 'day_seconds': D,
                   'releases_per_day': None, 'lead_unit_days': 1.0}
    assert ec.transit_of(off) is None and ec.transit_of(None) is None
    assert ec.lead_block(None, {'seconds': D, 'releases_per_day': 2})['lead_unit_days'] == 0.5
    on = ec.lead_block(PILOT, DAY)
    assert math.isclose(on['transit_days'], 1.766, abs_tol=5e-4)
    assert (on['trailer_type'], on['lead_s'], on['lead_sigma'], on['day_seconds'],
            on['releases_per_day'], on['lead_unit_days'], on['provenance']) \
        == ('53', 28800.0, 0.7, D, 1, 1.0, 'derived')
    json.dumps(on)
    law = ec.transit_of(on)
    assert law == cov.transit_day_law(PILOT['lead_s'], PILOT['lead_sigma'], D)
    assert ec.transit_of(on, 2.0)['transit_days'] > law['transit_days']
    assert ec.transit_of(on, 0.0)['pmf'] == {0: 1.0}


def test_fixed_point_stamps_the_lead_and_the_curve_and_the_rebuild_reproduces_it(monkeypatch):
    Order.next_sku = 1
    section = [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
               _order(3, freq=0.25, qty=8.0)]
    monkeypatch.setattr(ec, 'seed_lines', _fake_seed(6.0))
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a(lambda sq: 6.0))
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    lead = ec.lead_block(PILOT, DAY)
    _plan, _meta, _sa, rec = ec.fixed_point(
        section, lambda: (_Plan(section), _Meta()), specs, coverage_days=10.0,
        safety_days=2.0, floor_lines=1.0, inputs={}, day_seconds=D, log=_LOG, max_rounds=2,
        lead=lead)
    assert rec['lead'] == lead
    st = rec['final']['store']
    assert st['transit_days'] == lead['transit_days'] and st['lead_unit_days'] == 1.0
    assert math.isclose(st['lead_days'], lead['transit_days'])      # every attribute is 0
    fill = st['fill']
    assert math.isclose(fill['lead_days'], lead['transit_days'])
    assert fill['pipeline_units'] == sum(c.pipeline_qty for c in section) > 0
    curve = fill['vs_transit']
    ts = [p['transit_days'] for p in curve]
    assert ts == sorted(ts) and len(set(ts)) == len(ts) and ts[0] == 0.0
    stamp = [p for p in curve if p['transit_days'] == lead['transit_days']]
    assert len(stamp) == 1 and stamp[0]['fill_rate'] == fill['fill_rate']
    fills = [p['fill_rate'] for p in curve]
    assert fills == sorted(fills, reverse=True), 'more on the road serves less'
    json.dumps(rec)
    levels = [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in section]
    # THE REBUILD: fresh orders, the record alone, the same declaration.
    Order.next_sku = 1
    fresh = [_order(1, freq=0.5, qty=4.0, declare=False),
             _order(2, freq=0.25, qty=2.0, declare=False),
             _order(3, freq=0.25, qty=8.0, declare=False)]
    ec.declare_from_record(fresh, specs, rec, log=_LOG)
    assert [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in fresh] == levels
    # A record written before the lead block declares at transit 0, a batch read as a day.
    old = {k: v for k, v in rec.items() if k != 'lead'}
    ec.declare_from_record(fresh, specs, old, log=_LOG)
    cov.rescale_section(section, ec.declared_at(rec)['store'], coverage_days=10.0,
                        safety_days=2.0, floor_lines=1.0)
    assert [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in fresh] \
        == [(c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in section]
    # No lead handed in: the block says no pipeline and the curve is None.
    _p, _m, _s, rec0 = ec.fixed_point(
        section, lambda: (_Plan(section), _Meta()), specs, coverage_days=10.0,
        safety_days=2.0, floor_lines=1.0, inputs={}, day_seconds=D, log=_LOG, max_rounds=2)
    assert rec0['lead']['trailer_type'] is None and rec0['lead']['transit_days'] == 0.0
    assert rec0['final']['store']['fill']['vs_transit'] is None
    assert rec0['final']['store']['fill']['pipeline_units'] == 0


def test_a_supplier_lead_the_pipeline_would_discard_is_refused_under_the_era_only():
    Order.next_sku = 1
    with_lead = [_order(1, freq=0.5, qty=4.0, declare=False),
                 _order(2, freq=0.5, qty=4.0, lead=1.0, declare=False)]
    era = {'first_time_confidence': C}
    on = ec.lead_block(PILOT, DAY)
    off = ec.lead_block(None, DAY)
    with pytest.raises(ValueError, match='Chain the supplier lead before the trailer'):
        ec.refuse_discarded_lead(with_lead, on, era)
    ec.refuse_discarded_lead(with_lead, off, era)                  # no trailer: honoured
    ec.refuse_discarded_lead(with_lead, on, {'first_time_confidence': None})   # flag-off
    # A lead that rounds to no batch was never honoured by either transit.
    Order.next_sku = 1
    tiny = [_order(1, freq=0.5, qty=4.0, lead=0.3, declare=False)]
    ec.refuse_discarded_lead(tiny, on, era)
    # ...and the loop refuses before it declares anything.
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    with pytest.raises(ValueError, match='inbound-optimization 27'):
        ec.fixed_point(with_lead, lambda: (_Plan(with_lead), _Meta()), specs,
                       coverage_days=10.0, safety_days=2.0, floor_lines=None, inputs=era,
                       day_seconds=D, log=_LOG, lead=on)
    assert not any(c.stock_declared() for c in with_lead)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The audit
# ═════════════════════════════════════════════════════════════════════════════════════════

CURVE = [{'transit_days': 0.0, 'fill_rate': 0.99}, {'transit_days': 1.0, 'fill_rate': 0.97},
         {'transit_days': 2.0, 'fill_rate': 0.90}, {'transit_days': 4.0, 'fill_rate': 0.70}]


def test_fill_at_interpolates_the_curve_and_holds_its_ends():
    assert eq.fill_at(CURVE, 1.0) == 0.97
    assert math.isclose(eq.fill_at(CURVE, 1.5), 0.935)
    assert math.isclose(eq.fill_at(CURVE, 3.0), 0.80)
    assert eq.fill_at(CURVE, -1.0) == 0.99 and eq.fill_at(CURVE, 9.0) == 0.70
    assert eq.fill_at(None, 1.0) is None and eq.fill_at([], 1.0) is None
    assert eq.fill_at(list(reversed(CURVE)), 1.5) == eq.fill_at(CURVE, 1.5)


def test_realized_lead_is_littles_law_over_the_window():
    rows = [{'batch_id': 0, 'work_day': 0, 'in_transit_qty': 100, 'units_ordered': 50},
            {'batch_id': 1, 'work_day': 1, 'in_transit_qty': 120, 'units_ordered': 60},
            {'batch_id': 2, 'work_day': 2, 'in_transit_qty': 900, 'units_ordered': 10}]
    r = eq.realized_lead(rows, [0, 1])
    assert r == {'days': 2.0, 'in_transit_mean': 110.0, 'ordered_mean': 55.0, 'n': 2}
    assert eq.realized_lead(rows, [5])['days'] is None
    none = eq.realized_lead([{'batch_id': 0, 'work_day': 0, 'in_transit_qty': 0,
                              'units_ordered': 0}], [0])
    assert none['days'] is None and none['n'] == 1
    assert eq.realized_lead([{'batch_id': 0, 'work_day': 0}], [0])['days'] is None


def test_the_supply_reading_reports_the_lead_and_the_explained_level_without_a_band():
    shift, batch, work, carry = _window(missed=0.05)
    for r in batch:                                   # 2 days of orders standing, per day
        r['in_transit_qty'] = 2 * 800
        r['units_ordered'] = 800
    exp = _expectations(expected_missed_share=0.05, lead_days=1.766, transit_days=1.766,
                        fill_vs_transit=CURVE)
    v = _check(shift, batch, work, carry, exp=exp)
    ld = v.clauses['supply'].reading['lead']
    assert ld['stamped_days'] == 1.766 and ld['transit_days'] == 1.766
    assert ld['realized_days'] == 2.0 and ld['in_transit_mean'] == 1600.0
    assert math.isclose(ld['explained'], 1.0 - eq.fill_at(CURVE, 2.0))
    assert v.clauses['supply'].passed, 'the lead is reported, never judged'
    assert v.clauses['supply'].reading['tol'] == eq.SUPPLY_LEVEL_TOL
    line = eq.summarize(v)
    assert 'lead stamped 1.766 d / realized 2.000 d, explained level 0.100' in line
    # A supplier lead inside the stamp is subtracted before the curve is read.
    exp2 = _expectations(expected_missed_share=0.05, lead_days=2.766, transit_days=1.766,
                         fill_vs_transit=CURVE)
    ld2 = _check(shift, batch, work, carry, exp=exp2).clauses['supply'].reading['lead']
    assert math.isclose(ld2['explained'], 1.0 - eq.fill_at(CURVE, 1.0))
    # No stamp, no curve, no orders: every number None and the line says so.
    v0 = _check(shift, batch, work, carry)
    ld0 = v0.clauses['supply'].reading['lead']
    assert ld0['stamped_days'] is None and ld0['explained'] is None
    assert ld0['realized_days'] == 2.0
    assert 'lead unstamped / realized 2.000 d' in eq.summarize(v0)
    for r in batch:
        r['units_ordered'] = 0
    assert _check(shift, batch, work, carry).clauses['supply'].reading['lead']['realized_days'] \
        is None


def test_expectations_for_reads_the_lead_off_the_fill_block_or_none():
    rec = _staffing()
    e = eq.expectations_for(rec, pair='pairA', channel='store')
    assert e['lead_days'] is None and e['transit_days'] is None and e['fill_vs_transit'] is None
    rec['calibration']['pairA']['coverage'] = {'final': {'store': {'fill': {
        'fill_rate': 0.93, 'lead_days': 1.766, 'transit_days': 1.766, 'vs_transit': CURVE}}}}
    e = eq.expectations_for(rec, pair='pairA', channel='store')
    assert (e['lead_days'], e['transit_days'], e['fill_vs_transit']) == (1.766, 1.766, CURVE)
    assert math.isclose(e['expected_missed_share'], 0.07)


def test_the_batch_frame_carries_the_two_littles_law_terms():
    from Optimization.Performance_Evaluations.common import frames
    from Optimization.persistence.Picking_Data import BatchStats
    bs = BatchStats(run_id=1, batch_id=0, duration=10.0, num_tasks=1, total_items=5,
                    avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5,
                    units_ordered=42, in_transit_qty=84)
    df = frames._bdf([bs])
    assert int(df['units_ordered'].iloc[0]) == 42 and int(df['in_transit_qty'].iloc[0]) == 84
    assert frames.SEMANTIC_USES['sim_db']['batch_stats.units_ordered'] == 'read'


def test_the_audit_table_carries_a_lead_row_under_the_supply_share():
    from Optimization.Performance_Evaluations.throughput import audit
    head = ['arm', '1', '1', '0', '0']
    row = audit._lead_row(head, {'lead': {'stamped_days': 1.766, 'realized_days': 2.1,
                                          'explained': 0.061}})
    assert row[len(head):] == ['order-to-shelf lead', '-', '1.766 d', '2.100 d', '-',
                               'reported, not judged · explains supply 0.061']
    assert audit._lead_row(head, {})[len(head):][-1] == 'n/a (unstamped, unmeasured)'
    assert 'no orders in the window' in audit._lead_row(
        head, {'lead': {'stamped_days': 1.0, 'realized_days': None, 'explained': None}})[-1]
    # In the table the row lands under the supply share only where a lead was stamped or
    # measured; a pre-lead archive's table keeps its two rows.
    shift, batch, work, carry = _window(missed=0.05)
    for r in batch:
        r['in_transit_qty'], r['units_ordered'] = 1600, 800
    v = _check(shift, batch, work, carry,
               exp=_expectations(expected_missed_share=0.05, lead_days=1.766,
                                 transit_days=1.766, fill_vs_transit=CURVE))
    labels = [r[len(head)] for r in audit._share_rows(head, v)]
    assert labels == ['supply share', 'order-to-shelf lead', 'cut share']
    plain = _check(*_window(missed=0.05))
    assert [r[len(head)] for r in audit._share_rows(head, plain)] == ['supply share', 'cut share']


# ═════════════════════════════════════════════════════════════════════════════════════════
# The real tiny pair through the setup path
# ═════════════════════════════════════════════════════════════════════════════════════════

def _build(inv_db, aff_db, tmp_path, name):
    from Optimization.simdriver import sim_assets
    return sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / name / 'warehouse.db'))


def test_the_era_declares_at_the_pilot_transit_and_solves_a_higher_floor(tmp_path, restore):
    restore.update(shift_drain_or_cap=True, coverage_days=10.0, safety_days=2.0,
                   floor_lines=None, store_demand=0.05, releases_per_day=1,
                   inbound_trailer_type=None, inbound_lead_minutes=None,
                   inbound_lead_spread=None)
    inv_db, aff_db = _tiny_pair(tmp_path)
    off = _build(inv_db, aff_db, tmp_path, 'off')['coverage']
    assert inbound_lead_law() is None
    assert off['lead']['trailer_type'] is None and off['lead']['transit_days'] == 0.0
    assert off['lead']['lead_unit_days'] == 1.0 and off['lead']['releases_per_day'] == 1
    assert off['final']['store']['fill']['vs_transit'] is None
    assert off['final']['store']['fill']['pipeline_units'] == 0
    assert off['final']['store']['fill']['lead_days'] == 0.0
    # The pilot regime: transit 1.766, every SKU's lead is it, the pipeline is stamped, the
    # floor is solved at it (never below the lead-free one) and the fill clears the side.
    restore.update(inbound_trailer_type='53', inbound_lead_minutes=480, inbound_lead_spread=0.7)
    on = _build(inv_db, aff_db, tmp_path, 'on')['coverage']
    assert on['lead']['trailer_type'] == '53'
    assert math.isclose(on['lead']['transit_days'], 1.766, abs_tol=5e-4)
    assert on['lead'] == ec.lead_block(inbound_lead_law(), {'seconds': D, 'releases_per_day': 1})
    st = on['final']['store']
    assert math.isclose(st['lead_days'], on['lead']['transit_days'])
    assert math.isclose(st['fill']['lead_days'], on['lead']['transit_days'])
    assert st['fill']['pipeline_units'] > 0 and st['fill']['fill_rate'] >= SIDE
    assert on['floor']['store']['provenance'] == 'derived'
    assert not on['floor']['store']['solved']['at_lower_bound']
    assert on['floor']['store']['floor_lines'] > off['floor']['store']['floor_lines']
    assert on['floor']['store']['solved']['fill_rate'] >= SIDE
    curve = st['fill']['vs_transit']
    assert curve and any(p['transit_days'] == on['lead']['transit_days'] for p in curve)
    assert (on['coverage_days'], on['safety_days']) == (off['coverage_days'], off['safety_days'])
    json.dumps(on)


def test_an_lt1_sibling_refuses_under_the_era_with_a_trailer_and_builds_without_one(tmp_path,
                                                                                    restore):
    restore.update(shift_drain_or_cap=True, coverage_days=10.0, safety_days=2.0,
                   floor_lines=None, store_demand=0.05, releases_per_day=1,
                   inbound_trailer_type='53', inbound_lead_minutes=480, inbound_lead_spread=0.7)
    inv_db, aff_db = _tiny_pair(tmp_path, lead_batches=1.0)
    with pytest.raises(ValueError, match='Chain the supplier lead before the trailer'):
        _build(inv_db, aff_db, tmp_path, 'refused')
    # Without the trailer the batch transit honours the attribute: one batch is one day, the
    # fill is priced at it, and the floor solves above the lead-free sibling's.
    restore.update(inbound_trailer_type=None, inbound_lead_minutes=None, inbound_lead_spread=None)
    rec = _build(inv_db, aff_db, tmp_path, 'batch')['coverage']
    assert rec['lead']['trailer_type'] is None and rec['lead']['transit_days'] == 0.0
    st = rec['final']['store']
    assert math.isclose(st['lead_days'], 1.0) and st['fill']['pipeline_units'] > 0
    assert math.isclose(st['fill']['lead_days'], 1.0) and st['fill']['transit_days'] == 0.0
    assert st['fill']['vs_transit'] is None and st['fill']['fill_rate'] >= SIDE
    lt0_db, lt0_aff = _tiny_pair(tmp_path / 'lt0')
    lt0 = _build(lt0_db, lt0_aff, tmp_path, 'lt0')['coverage']
    assert not rec['floor']['store']['solved']['at_lower_bound']
    assert rec['floor']['store']['floor_lines'] > lt0['floor']['store']['floor_lines']


def test_a_mixed_catalogue_shares_one_transit_and_solves_each_section_at_it(monkeypatch):
    """Two sections, one pair-level transit: each channel's floor is solved at the lead over
    its own section, the fulfillment floor moves above its lead-free value, and the rebuild
    reproduces both sections from the record alone."""
    from test_fulfillment_channels import _mixed_inventory
    from test_fill_headroom import _HoldPlan
    from Warehouse.kernel.regime import STORE, FULFILLMENT
    specs = [ec.ChannelSpec('store', STORE, None, 'store'),
             ec.ChannelSpec('fulfillment', FULFILLMENT, None, 'ff')]
    monkeypatch.setattr(ec, 'seed_lines', lambda *a, **k: {'store': 4.0, 'fulfillment': 3.0})
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a(lambda sq: 4.0))
    # The era's inputs: the floor solved per section, the fill derived (so the fake plan is
    # handed the hold map and builds every bucket the fragmentation chain packs into).
    era = {'first_time_confidence': C, 'min_headroom': 0.05}

    def run(lead):
        Order.next_sku = 1
        orders = _mixed_inventory(n_store=30, n_ff=12, seed=4)
        _p, _m, _s, rec = ec.fixed_point(
            orders, lambda **kw: (_HoldPlan(orders, kw.get('bucket_hold')), _Meta()), specs,
            coverage_days=10.0, safety_days=2.0, floor_lines=None, inputs=era, day_seconds=D,
            log=_LOG, max_rounds=1, lead=lead)
        return orders, rec

    _o0, off = run(None)
    orders, on = run(ec.lead_block(PILOT, DAY))
    for ch in ('store', 'fulfillment'):
        assert off['final'][ch]['transit_days'] == 0.0
        assert math.isclose(on['final'][ch]['transit_days'], on['lead']['transit_days'])
        assert math.isclose(on['final'][ch]['fill']['lead_days'], on['lead']['transit_days'])
        assert on['floor'][ch]['solved']['fill_rate'] >= SIDE
        assert on['floor'][ch]['floor_lines'] >= off['floor'][ch]['floor_lines']
    assert on['floor']['fulfillment']['floor_lines'] > off['floor']['fulfillment']['floor_lines']
    assert on['final']['fulfillment']['fill']['pipeline_units'] > 0
    levels = {c.sku: (c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in orders}
    Order.next_sku = 1
    fresh = _mixed_inventory(n_store=30, n_ff=12, seed=4)
    ec.declare_from_record(fresh, specs, on, log=_LOG)
    assert {c.sku: (c.equilibrium_qty, c.reorder_point, c.pipeline_qty) for c in fresh} == levels
