"""test_coverage_rescale.py — stock coverage is a RUNTIME rescaling under declared days,
floored at ONE LINE of the SKU's own demand.

Under the calibrated era every SKU's order-up-to quantity and reorder point are re-derived
at setup from its DAILY demand by the generator's own formula with the day as the unit,
never below the SKU's line floor (a floored SKU runs BASE STOCK: `rp = Q - 1`), and the
warehouse is sized from those levels through a pair-level fixed point
(.scratch/department-calibration, "Rescale stock coverage at setup", "Choose the coverage
floor", "Build the line floor").  Three layers, three groups of tests:

  * the pure module (`Optimization/simconfig/coverage.py`) against hand computations: the
    demand per day, the line floor, the formula in days on both levels, the stamped
    pipeline, the shares it reports, the expected first-pass fill rate for a known Poisson;
  * the loop (`Optimization/simdriver/era_coverage.fixed_point`) with a FAKE planner and a
    fake stage A: it rescales at the previous round's `n`, re-plans, stops on convergence
    or at `max_rounds`, records every round and the fill rate at the planned levels;
  * the seams: the three knobs ride STAFFING_KEYS (so the flags, the run-spec record, both
    restore sites and the worker payload come by construction), `build_shared_assets`
    flag-off calls the planner exactly ONCE and never enters the loop (its plan is the plan
    the planner returns directly -- the byte-identical discipline; the planned file carries
    no pipeline stamp), and under the era the planned inventory carries the rescaled levels
    AND the stamp, and the derivation's stage A is reused.

The placement fingerprint that rides beside the per-arm expected day is pinned at the end.

Run:  python -m pytest Tests/unit/test_coverage_rescale.py -q
"""
from __future__ import annotations

import inspect
import logging
import math
import os
import random

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import (CONFIG, STAFFING_KEYS, _SCALAR_DEFAULTS,
                                            staffing_spec)
from Optimization.simconfig import coverage as cov
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Order import Order
from Warehouse.kernel.regime import STORE

_LOG = logging.getLogger('test_coverage_rescale')


# ── a tiny catalogue, built the production way ───────────────────────────────────────────

def _order(sku, *, freq, qty, eq=60, rp=20, lead=0.0):
    return Order.build(sku, 'conveyable', 'seasonal', 10, 10, 10, 10, freq, qty,
                       equilibrium_qty=eq, reorder_point=rp, lead_time_mean=lead, supply_cv=0.0)


@pytest.fixture()
def section():
    Order.next_sku = 1
    return [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
            _order(3, freq=0.25, qty=8.0, lead=3.0)]


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    keys = (*STAFFING_KEYS, 'shift_drain_or_cap')
    before = {k: CONFIG['global'].get(k) for k in keys}
    yield CONFIG['global']
    CONFIG['global'].update(before)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The pure module
# ═════════════════════════════════════════════════════════════════════════════════════════

def _mean(lam):
    """The mean of `max(1, Poisson(lam))` BY HAND -- the test's own oracle for the stamped law."""
    return lam + math.exp(-lam)


def test_the_module_carries_no_line_law_of_its_own():
    """"Stamp the line distribution on the SKU": the units a line demands are read off
    `Demand.line`, never re-derived here -- the old `line_units(λ)` is gone."""
    assert not hasattr(cov, 'line_units')
    src = inspect.getsource(cov)
    assert 'math.exp' not in src and 'quantity_rate' not in src


def test_daily_demand_is_lines_times_line_share_times_the_stamped_mean(section):
    d = cov.daily_demand(section, 100.0)
    assert math.isclose(d[1], 100.0 * 0.5 * _mean(4.0), rel_tol=1e-12)
    assert math.isclose(d[2], 100.0 * 0.25 * _mean(2.0), rel_tol=1e-12)
    assert math.isclose(sum(d.values()),
                        100.0 * (0.5 * _mean(4.0) + 0.25 * _mean(2.0) + 0.25 * _mean(8.0)),
                        rel_tol=1e-12)
    # ...and it IS the object's reading: a SKU stamped with a different rate moves it.
    for c in section:
        assert math.isclose(d[c.sku], 100.0 * (c.demand.relative_frequency / 1.0)
                            * c.demand.line.mean(), rel_tol=1e-12)
    assert cov.daily_demand(section, 0.0) == {1: 0.0, 2: 0.0, 3: 0.0}
    assert cov.daily_demand([], 10.0) == {}


def test_the_line_floor_is_the_mean_line_rounded_up_scaled_by_the_declared_lines(section):
    # ceil(floor_lines x E[line]): the fixture's means are 4.018, 2.135 and 8.0003.
    assert [cov.line_floor(c.demand.line, 1.0) for c in section] == [5, 3, 9]
    assert [cov.line_floor(c.demand.line, 2.0) for c in section] == [9, 5, 17]
    assert cov.line_floor(section[1].demand.line, 0.25) == 1           # never below one unit
    # An integer mean stays put (no float creep): 2.0 lines of 2.5 units is exactly 5.
    class _L:
        def mean(self):
            return 2.5
    assert cov.line_floor(_L(), 2.0) == 5
    with pytest.raises(ValueError, match='floor_lines'):
        cov.line_floor(section[0].demand.line, 0.0)


def test_the_pipeline_is_demand_over_the_lead_rounded():
    assert cov.pipeline_qty(0.6, 3.0) == 2                     # 1.8 -> 2
    assert cov.pipeline_qty(0.6, 0.0) == 0                     # lead 0: nothing in transit
    assert cov.pipeline_qty(2.135, 2.0) == 4
    assert cov.pipeline_qty(1.0, -1.0) == 0                    # a negative lead is zero


def test_stock_levels_are_the_generators_formula_with_the_day_as_the_unit_above_the_floor():
    # Q = max(round(cov x d), L); rp = min(Q - 1, max(round(d x (lead + safety)), L)).
    assert cov.stock_levels(3.2, 0.0, 10.0, 2.0, 2) == (32, 6)
    assert cov.stock_levels(0.6, 3.0, 10.0, 2.0, 2) == (6, 3)     # lead counts: 0.6 x 5 = 3
    assert cov.stock_levels(0.6, 0.0, 10.0, 2.0, 2) == (6, 2)     # 0.6 x 2 = 1.2 -> 1, LIFTED to L
    assert cov.stock_levels(0.6, 0.0, 10.0, 20.0, 2) == (6, 5)    # capped one below Q
    # The safety stock is line-sized: a SKU above the floor whose lead + safety demand is
    # less than a line still holds a line at the reorder point.
    assert cov.stock_levels(1.2, 0.0, 10.0, 1.0, 5) == (12, 5)


def test_a_floored_sku_runs_base_stock(section):
    """`rp = Q - 1`: every pick fires an order-up-to for what it took (decision 1)."""
    # d = 0.04 units/day, L = 5: the coverage term rounds to 0, the floor holds, rp = 4.
    assert cov.stock_levels(0.04, 0.0, 10.0, 2.0, 5) == (5, 4)
    # ...whatever the lead: the pipeline is stamped separately, never folded into rp.
    assert cov.stock_levels(0.04, 30.0, 10.0, 2.0, 5) == (5, 4)
    # The coverage term landing exactly ON the floor is the floor too.
    assert cov.stock_levels(0.5, 0.0, 10.0, 2.0, 5) == (5, 4)
    # A one-unit floor keeps the generator's Q = 1 branch (Order.build clamps rp to 1 there).
    assert cov.stock_levels(0.04, 0.0, 10.0, 2.0, 1) == (1, 1)
    assert cov.stock_levels(0.16, 0.0, 10.0, 0.0, 1) == (2, 1)
    # Through the section: at 0.05 lines a day every SKU sits on its floor at rp = Q - 1.
    cov.rescale_section(section, 0.05, coverage_days=10.0, safety_days=2.0, floor_lines=1.0)
    assert [(c.equilibrium_qty, c.reorder_point) for c in section] == [(5, 4), (3, 2), (9, 8)]


def test_stock_levels_refuse_bad_days_and_a_zero_floor():
    with pytest.raises(ValueError, match='coverage_days'):
        cov.stock_levels(1.0, 0.0, 0.0, 2.0, 1)
    with pytest.raises(ValueError, match='safety_days'):
        cov.stock_levels(1.0, 0.0, 10.0, -1.0, 1)
    with pytest.raises(ValueError, match='floor_units'):
        cov.stock_levels(1.0, 0.0, 10.0, 2.0, 0)


def test_rescale_section_mutates_the_levels_stamps_the_pipeline_resets_the_plan(section):
    for c in section:
        c.stock_plan = [(False, 5, 2)]
    st = cov.rescale_section(section, 100.0, coverage_days=10.0, safety_days=2.0,
                             floor_lines=1.0)
    d = cov.daily_demand(section, 100.0)
    for c in section:
        L = cov.line_floor(c.demand.line, 1.0)
        q, rp = cov.stock_levels(d[c.sku], c.lead_time_mean, 10.0, 2.0, L)
        assert (c.equilibrium_qty, c.reorder_point) == (q, rp)
        assert c.pipeline_qty == cov.pipeline_qty(d[c.sku], c.lead_time_mean)
        assert c.stock_plan is None, 'a plan written for the old quantity must not survive'
    assert section[2].pipeline_qty == round(d[3] * 3.0) > 0        # the one SKU with a lead
    assert section[0].pipeline_qty == 0
    assert st['n_skus'] == 3 and st['sum_q'] == sum(c.equilibrium_qty for c in section)
    assert st['line_families'] == {'poisson_max1': 3}      # the record names the law it read
    assert st['floor_lines'] == 1.0
    assert math.isclose(st['units_per_day'], sum(d.values()), rel_tol=1e-12)
    assert st['floor_line_skus'] == 0 and st['above_floor_skus'] == 3
    assert st['base_stock_skus'] == 0 and st['floor_line_demand_share'] == 0.0
    assert math.isclose(st['realized_coverage_days'], st['sum_q'] / st['units_per_day'],
                        rel_tol=1e-12)
    assert 'fill' not in st, 'the fill rate is priced on the PLANNED levels, by the loop'


def test_the_floor_shares_count_skus_and_the_demand_they_carry(section):
    # 0.05 lines a day: every SKU sits on its line floor, runs base stock and carries ALL
    # the demand; nobody is above the floor.
    st = cov.rescale_section(section, 0.05, coverage_days=10.0, safety_days=2.0,
                             floor_lines=1.0)
    assert st['floor_line_skus'] == 3 and st['floor_line_share'] == 1.0
    assert st['base_stock_skus'] == 3 and st['base_stock_share'] == 1.0
    assert math.isclose(st['floor_line_demand_share'], 1.0)
    assert st['realized_coverage_days'] == 0.0 and st['above_floor_skus'] == 0
    # 4 lines a day: SKU 1 (d = 8.04, L 5), 2 (d = 2.14, L 3) and 3 (d = 8.00, L 9) all clear
    # the floor and every reorder point clears its line too (16, 4, 40 against 5, 3, 9).
    st = cov.rescale_section(section, 4.0, coverage_days=10.0, safety_days=2.0,
                             floor_lines=1.0)
    assert st['floor_line_skus'] == 0 and st['floor_rp_skus'] == 0 and st['base_stock_skus'] == 0
    assert [(c.equilibrium_qty, c.reorder_point) for c in section] == [(80, 16), (21, 4), (80, 40)]
    # ...and at 0.6 lines a day with one safety day SKU 2 (d = 0.32) rounds to 3 units = its
    # floor (base stock), while SKUs 1 and 3 clear their floors on Q (12 each) but not on the
    # reorder point (1.2 -> 1 against L 5; 1.2 x 4 -> 5 against L 9): the rp lift is counted
    # separately, only above the floor.
    st = cov.rescale_section(section, 0.6, coverage_days=10.0, safety_days=1.0,
                             floor_lines=1.0)
    assert st['floor_line_skus'] == 1 and st['base_stock_skus'] == 1 and st['floor_rp_skus'] == 2
    assert [(c.equilibrium_qty, c.reorder_point) for c in section] == [(12, 5), (3, 2), (12, 9)]


def test_the_fill_rate_is_the_units_a_shelf_serves_off_a_line_for_a_known_poisson():
    """One SKU, Poisson(2) lines, a shelf of 3: E[min(q, 3)] = Σ_{j<3} P(q > j)
    = 1 + (1 - 3e^-2) + (1 - 5e^-2) = 3 - 8e^-2 BY HAND, over E[q] = 2 + e^-2."""
    Order.next_sku = 1
    one = [_order(1, freq=0.5, qty=2.0, eq=3, rp=2)]
    got = cov.fill_rate(one, 40.0)
    want = (3.0 - 8.0 * math.exp(-2.0)) / _mean(2.0)
    assert math.isclose(got['fill_rate'], want, rel_tol=1e-12)
    assert math.isclose(got['expected_missed_share'], 1.0 - want, rel_tol=1e-12)
    assert got['base_stock_skus'] == 1 and got['base_stock_share'] == 1.0     # rp = Q - 1
    assert got['n_skus'] == 1
    # The weights are LINES per SKU (n · π_s), so the rate is independent of n...
    assert math.isclose(cov.fill_rate(one, 4000.0)['fill_rate'], want, rel_tol=1e-12)
    # ...and a second SKU with a shelf far above any line serves every unit: the section's
    # rate is the line-weighted mean of the two, (E[min_A] + E[q_B]) / (E[q_A] + E[q_B]) at
    # equal line shares.
    two = one + [_order(2, freq=0.5, qty=4.0, eq=100, rp=20)]
    got2 = cov.fill_rate(two, 40.0)
    want2 = ((3.0 - 8.0 * math.exp(-2.0)) + _mean(4.0)) / (_mean(2.0) + _mean(4.0))
    assert math.isclose(got2['fill_rate'], want2, rel_tol=1e-9)
    assert got2['base_stock_skus'] == 1 and got2['base_stock_share'] == 0.5
    assert math.isclose(got2['units_per_day'], sum(cov.daily_demand(two, 40.0).values()))
    assert cov.fill_rate([], 40.0)['fill_rate'] == 0.0


def test_implied_coverage_reads_the_catalogues_own_levels(section):
    d = cov.daily_demand(section, 10.0)
    got = cov.implied_coverage(section, 10.0)
    assert got['sum_q'] == 180
    assert math.isclose(got['demand_weighted_days'], 180.0 / sum(d.values()), rel_tol=1e-12)
    covs = sorted(60.0 / d[c.sku] for c in section)
    assert math.isclose(got['median_days'], covs[1], rel_tol=1e-12)


def test_converged_needs_every_channel_inside_the_tolerance():
    assert cov.converged({'store': 100.0}, {'store': 100.5}, 0.01)
    assert not cov.converged({'store': 100.0}, {'store': 102.0}, 0.01)
    assert not cov.converged({'store': 100.0, 'fulfillment': 50.0},
                             {'store': 100.0, 'fulfillment': 51.0}, 0.01)
    assert not cov.converged({}, {'store': 100.0})
    assert not cov.converged({'store': 100.0}, {'store': 100.0, 'fulfillment': 1.0})
    assert not cov.converged({'store': 0.0}, {'store': 0.0})


# ═════════════════════════════════════════════════════════════════════════════════════════
# The loop, with a fake planner and a fake stage A
# ═════════════════════════════════════════════════════════════════════════════════════════

class _Plan:
    def __init__(self, orders):
        self.sampled = list(orders)
        self.total_aisles = 3
        self.total_bins = 30


class _Meta:
    aisles = ()


def _fake_stage_a(lines_of_sum_q):
    """A stage A whose fixed point depends only on the section's total stock: `n` = f(ΣQ).
    Stands in for the closed form so the loop's arithmetic is the thing under test."""
    def stage_a(orders, geometry, specs, *, inputs, day_seconds, log):
        out = {}
        for s in specs:
            n = lines_of_sum_q(sum(c.equilibrium_qty for c in orders))
            out[s.name] = {'n': n, 'expected': {'lines': n}, 'orders': list(orders)}
        return out
    return stage_a


def test_fixed_point_rescales_at_the_previous_rounds_n_and_stops_when_converged(
        monkeypatch, section):
    calls = []

    def plan_fn():
        calls.append([c.equilibrium_qty for c in section])
        return _Plan(section), _Meta()

    # n = 60 + 0.001 x ΣQ -- a contraction, as the real fixed point is (n moves weakly with
    # the geometry): the first rescaling moves n ~4%, the second ~0.2%.
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a(lambda sq: 60.0 + 0.001 * sq))
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    plan, meta, sa, rec = ec.fixed_point(section, plan_fn, specs, coverage_days=10.0,
                                         safety_days=2.0, floor_lines=1.0, inputs={},
                                         day_seconds=28800.0, log=_LOG, tol=0.01, max_rounds=6)
    # Round 0 planned the catalogue's own levels (ΣQ = 180 -> n = 60.18).
    assert calls[0] == [60, 60, 60]
    assert rec['rounds'][0]['round'] == 0
    assert math.isclose(rec['rounds'][0]['lines_per_day']['store'], 60.0 + 0.001 * 180)
    assert rec['catalogue']['store']['sum_q'] == 180
    # Every later round rescaled the SECTION at the previous round's n before planning.
    for r in rec['rounds'][1:]:
        prev = r['rescaled_at']['store']
        d = cov.daily_demand(section, prev)
        want = [cov.stock_levels(d[c.sku], c.lead_time_mean, 10.0, 2.0,
                                 cov.line_floor(c.demand.line, 1.0))[0] for c in section]
        assert calls[r['round']] == want
        assert r['stats']['store']['sum_q'] == sum(want)
    assert rec['converged'] is True
    assert len(calls) == len(rec['rounds'])
    assert 1 < len(rec['rounds']) <= 7
    assert all(abs(v) < 0.01 for v in rec['residual'].values())
    assert rec['lines_per_day'] == {'store': sa['store']['n']}
    assert rec['final'] is rec['rounds'][-1]['stats']
    assert rec['planned_sum_q'] == sum(c.equilibrium_qty for c in section)
    assert (rec['coverage_days'], rec['safety_days'], rec['floor_lines'], rec['tol']) \
        == (10.0, 2.0, 1.0, 0.01)
    # The fill rate rides `final`, priced ONCE on the planned orders at the fixed point's n
    # (the fake planner plans the section itself, so "planned" = the last rescaling).
    fill = rec['final']['store']['fill']
    assert fill == cov.fill_rate(section, rec['lines_per_day']['store'])
    assert 0.0 < fill['fill_rate'] <= 1.0 and fill['n_skus'] == 3
    assert 'fill' not in rec['rounds'][1]['stats']['store'] or len(rec['rounds']) == 2


def test_fixed_point_stops_at_max_rounds_and_records_the_residual(monkeypatch, section):
    # n alternates with ΣQ so it never converges (180 -> 50 lines -> ~2,270 units -> 5
    # lines -> ~227 units -> 50 lines ...): the loop must stop and say so.
    monkeypatch.setattr(ec, 'stage_a',
                        _fake_stage_a(lambda sq: 5.0 if sq > 1000 else 50.0))
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    plan, meta, sa, rec = ec.fixed_point(section, lambda: (_Plan(section), _Meta()), specs,
                                         coverage_days=10.0, safety_days=2.0, floor_lines=1.0,
                                         inputs={}, day_seconds=28800.0, log=_LOG, tol=0.01,
                                         max_rounds=3)
    assert rec['converged'] is False
    assert len(rec['rounds']) == 4                           # round 0 + 3
    assert any(abs(v) >= 0.01 for v in rec['residual'].values())


def test_next_guess_iterates_plainly_until_bracketed_then_takes_the_log_secant():
    assert ec.next_guess([(100.0, 150.0)]) == 150.0              # f > 0 only: plain iterate
    assert ec.next_guess([(100.0, 150.0), (150.0, 160.0)]) == 160.0
    # Bracketed: f(ln 100) = ln 1.5 > 0, f(ln 400) = ln 0.5 < 0 -> the secant in log-space.
    pts = [(100.0, 150.0), (400.0, 200.0)]
    la, lb = math.log(100.0), math.log(400.0)
    fa, fb = math.log(1.5), math.log(0.5)
    want = math.exp(la - fa * (lb - la) / (fb - fa))
    assert math.isclose(ec.next_guess(pts), want, rel_tol=1e-12)
    assert 100.0 < ec.next_guess(pts) < 400.0
    # A secant landing within 5% of an end is pulled to the log-midpoint.
    pts = [(100.0, 100.01), (400.0, 40.0)]
    assert math.isclose(ec.next_guess(pts), math.exp(0.5 * (la + lb)), rel_tol=1e-12)
    with pytest.raises(ValueError):
        ec.next_guess([])


def test_fixed_point_converges_on_a_decreasing_map_with_gain_above_one(monkeypatch, section):
    # n_out = K / ΣQ^1.5: more lines -> more stock -> fewer lines, with |gain| = 1.5 at the
    # root, so plain iteration oscillates and only the bracketed secant closes.  ΣQ ~ 45.4 n,
    # so the root sits near n = 200.
    K = 45.4 ** 1.5 * 200.0 ** 2.5
    monkeypatch.setattr(ec, 'stage_a', _fake_stage_a(lambda sq: K / max(sq, 1) ** 1.5))
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    plan, meta, sa, rec = ec.fixed_point(section, lambda: (_Plan(section), _Meta()), specs,
                                         coverage_days=10.0, safety_days=2.0, floor_lines=1.0,
                                         inputs={}, day_seconds=28800.0, log=_LOG, tol=0.01,
                                         max_rounds=25)
    assert rec['converged'] is True
    assert abs(rec['residual']['store']) < 0.01
    assert 150.0 < rec['lines_per_day']['store'] < 260.0
    # Plain iteration from the same start would have oscillated: the first two rounds do.
    r1, r2 = rec['rounds'][1], rec['rounds'][2]
    assert r1['lines_per_day']['store'] < r1['rescaled_at']['store']
    assert r2['lines_per_day']['store'] > r2['rescaled_at']['store']


def test_fixed_point_rescales_each_channel_section_by_its_own_regime(monkeypatch, section):
    seen = {}

    def stage_a(orders, geometry, specs, *, inputs, day_seconds, log):
        return {s.name: {'n': 10.0, 'expected': {'lines': 10.0}, 'orders': orders}
                for s in specs}

    monkeypatch.setattr(ec, 'stage_a', stage_a)
    real = cov.rescale_section

    def spy(orders, n, **kw):
        seen[len(orders)] = n
        return real(orders, n, **kw)

    monkeypatch.setattr(cov, 'rescale_section', spy)
    specs = [ec.ChannelSpec('store', STORE, None, 'store')]
    ec.fixed_point(section, lambda: (_Plan(section), _Meta()), specs, coverage_days=10.0,
                   safety_days=2.0, floor_lines=1.0, inputs={}, day_seconds=28800.0, log=_LOG,
                   max_rounds=1)
    assert seen == {3: 10.0}, 'the store regime filter keeps all three store SKUs'


# ═════════════════════════════════════════════════════════════════════════════════════════
# The seams
# ═════════════════════════════════════════════════════════════════════════════════════════

_KNOBS = ('coverage_days', 'safety_days', 'floor_lines')


def test_the_three_knobs_are_declared_assumptions_with_their_flags_named():
    assert (_s.COVERAGE_DAYS, _s.SAFETY_DAYS, _s.FLOOR_LINES) == (10.0, 2.0, 1.0)
    src = inspect.getsource(_s)
    for flag in ('--coverage-days', '--safety-days', '--floor-lines'):
        assert flag in src, f'{flag} is not named beside its setting'


def test_the_three_knobs_ride_staffing_keys_and_resolve_a_none_to_their_default(restore):
    defaults = (_s.COVERAGE_DAYS, _s.SAFETY_DAYS, _s.FLOOR_LINES)
    for k, dflt in zip(_KNOBS, defaults):
        assert k in STAFFING_KEYS, k
        assert _SCALAR_DEFAULTS[k] == dflt, k
        assert k in CONFIG['global'], k
    restore.update(coverage_days=None, safety_days=None, floor_lines=None)
    spec = staffing_spec()
    assert tuple(spec[k] for k in _KNOBS) == defaults
    restore.update(coverage_days=30.0, safety_days=0.5, floor_lines=1.5)
    spec = staffing_spec()
    assert tuple(spec[k] for k in _KNOBS) == (30.0, 0.5, 1.5)
    assert set(spec) == set(STAFFING_KEYS)


@pytest.mark.parametrize('flag', ['--coverage-days', '--safety-days', '--floor-lines'])
def test_each_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), flag


def test_the_asset_builder_hands_the_floor_to_the_loop():
    from Optimization.simdriver import sim_assets
    src = inspect.getsource(sim_assets.build_shared_assets)
    assert "floor_lines=float(_inputs['floor_lines'])" in src


def test_a_stamped_file_reads_as_unstamped_flag_off_and_keeps_its_stamp_under_the_era(
        tmp_path, restore):
    """Flag-off byte-identity is a property of the CODE, not of the file handed in: a frozen
    planned inventory stamped by an era run must fire the manager's heuristic under a
    flag-off run.  Both production load sites go through `load_run_inventory`."""
    from Optimization.simdriver import sim_assets, strategy_runner
    from Warehouse.generation.generate_inventory import save_inventory_to_db
    Order.next_sku = 1
    inv_orders = [_order(1, freq=0.5, qty=4.0, lead=2.0), _order(2, freq=0.5, qty=2.0, lead=2.0)]
    inv_orders[0].pipeline_qty, inv_orders[1].pipeline_qty = 3, 0
    from Warehouse.catalog.Inventory_Builder import Inventory
    path = str(tmp_path / 'stamped_planned.db')
    save_inventory_to_db(Inventory(inv_orders), path, {'test': True})
    off = sim_assets.load_run_inventory(path, era=False)
    assert [c.pipeline_qty for c in off.orders] == [None, None]
    assert [c.pipeline_allowance() for c in off.orders] == [round(20 * 2 / 3)] * 2   # the heuristic
    on = sim_assets.load_run_inventory(path, era=True)
    assert [c.pipeline_qty for c in on.orders] == [3, 0]
    assert [c.pipeline_allowance() for c in on.orders] == [3, 0]
    # The flag is explicit because a spawned worker's CONFIG is pristine (`era_on()` there is
    # the settings default): the parent passes `era_on()`, the worker its payload's flag.
    assert 'load_run_inventory(_src_db, limit=max_skus, era=era_on())' in \
        inspect.getsource(sim_assets.build_shared_assets)
    sr = inspect.getsource(strategy_runner)
    assert 'load_run_inventory(inv_db, limit=max_skus, era=_drain_or_cap)' in sr
    assert 'load_inventory_from_db(inv_db' not in sr
    assert "_drain_or_cap = bool(_wd.get('drain_or_cap'))" in sr


def test_the_asset_builder_enters_the_loop_only_under_the_era_and_only_where_it_samples():
    from Optimization.simdriver import sim_assets
    src = inspect.getsource(sim_assets.build_shared_assets)
    assert 'if _sample and era_on():' in src
    assert '_era_cov.fixed_point(' in src
    assert 'plan = _plan()' in src, 'flag-off the planner is called directly, once'
    assert 'if warehouse_meta is None:' in src, 'flag-off the build happens where it always did'


def _tiny_pair(tmp_path):
    """A 90-SKU store-only inventory DB + an empty affinity DB, the production way."""
    from Warehouse.generation.generate_inventory import (
        build_inventory_with_profile, save_inventory_to_db, DEFAULT_DIM_SPEC,
        DEFAULT_WEIGHT_SPEC)
    Order.next_sku = 1
    inv = build_inventory_with_profile(
        num_skus=90, seed=11, handling_splits=[0.5, 0.5], category_splits=[1 / 6] * 6,
        singleton_fraction=0.3, dim_spec=DEFAULT_DIM_SPEC, weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0, reorder_safety_batches=2.0)
    inv_db = str(tmp_path / 'inventory.db')
    save_inventory_to_db(inv, inv_db, {'name': 'coverage_test', 'num_skus': 90})
    aff_db = str(tmp_path / 'affinity.db')
    from Warehouse.catalog.Affinity_Store import AffinityStore
    AffinityStore(aff_db)                     # creates the (empty) tables
    return inv_db, aff_db


def test_flag_off_the_planner_runs_once_and_its_plan_is_the_plan(tmp_path, monkeypatch, restore):
    from Optimization.simdriver import sim_assets
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    restore['shift_drain_or_cap'] = False
    inv_db, aff_db = _tiny_pair(tmp_path)
    calls = []
    real = Inventory_Manager.plan_warehouse.__func__

    def spy(cls, orders, **kw):
        calls.append(kw)
        return real(cls, orders, **kw)

    monkeypatch.setattr(Inventory_Manager, 'plan_warehouse', classmethod(spy))
    monkeypatch.setattr(ec, 'fixed_point',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('loop entered')))
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    assert len(calls) == 1 and calls[0]['sample'] is True
    assert shared['coverage'] is None and shared['era_stage_a'] is None
    # The plan the builder kept IS the planner's plan: the same sampled levels, the same
    # shape, from the same seed -- what the pre-loop code produced.
    from Warehouse.generation.generate_inventory import load_inventory_from_db
    from Optimization.config.sim_config import seed_world
    Order.next_sku = 1
    ref = load_inventory_from_db(inv_db)
    # The builder's own sampling RNG was consumed by its call; the reference draws a fresh
    # one from the same seed, exactly as the builder constructed it.
    ref_plan = real(Inventory_Manager, ref.orders,
                    **{**calls[0], 'rng': random.Random(seed_world() + 1)})
    assert {c.sku: c.equilibrium_qty for c in shared['inventory'].orders} == \
           {c.sku: c.equilibrium_qty for c in ref_plan.sampled}
    assert (shared['total_aisles'], shared['total_bins']) == \
           (ref_plan.total_aisles, ref_plan.total_bins)
    # ...and the planned DB the workers load carries exactly those levels -- and NO pipeline
    # stamp: flag-off the manager keeps its rp x lead / (lead + 1) heuristic, byte for byte.
    planned = load_inventory_from_db(shared['planned_inv_db'])
    assert {c.sku: (c.equilibrium_qty, c.reorder_point) for c in planned.orders} == \
           {c.sku: (c.equilibrium_qty, c.reorder_point) for c in ref_plan.sampled}
    assert all(c.pipeline_qty is None for c in planned.orders)
    assert all(c.pipeline_qty is None for c in shared['inventory'].orders)


def test_under_the_era_the_planned_inventory_carries_the_rescaled_levels_and_the_stamp(
        tmp_path, restore):
    from Optimization.simdriver import sim_assets
    from Warehouse.generation.generate_inventory import load_inventory_from_db
    restore.update(shift_drain_or_cap=True, coverage_days=10.0, safety_days=2.0, floor_lines=1.0)
    inv_db, aff_db = _tiny_pair(tmp_path)
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    rec = shared['coverage']
    assert rec is not None and len(rec['rounds']) >= 2
    assert set(rec['final']) == {'store'} and set(rec['catalogue']) == {'store'}
    assert rec['rounds'][0]['aisles'] > 0
    assert rec['floor_lines'] == 1.0
    # The levels the run fields: each sampled SKU's Q is AT LEAST the rescaled one (the
    # planner grows a level into leftover capacity, never shrinks it) and its reorder point
    # keeps the rescaled ratio; and they are what the planned DB holds, with the stamped
    # pipeline beside them (zero here: the tiny pair has no lead -- stamped, not absent).
    last = rec['rounds'][-1]
    n_prev = last['rescaled_at']['store']
    Order.next_sku = 1
    cat = load_inventory_from_db(inv_db)
    d = cov.daily_demand(cat.orders, n_prev)
    want = {c.sku: cov.stock_levels(d[c.sku], c.lead_time_mean, 10.0, 2.0,
                                    cov.line_floor(c.demand.line, 1.0)) for c in cat.orders}
    planned = load_inventory_from_db(shared['planned_inv_db'])
    assert len(planned.orders) > 0
    for c in planned.orders:
        assert c.equilibrium_qty >= want[c.sku][0], c.sku
        assert c.equilibrium_qty >= cov.line_floor(c.demand.line, 1.0), 'below the line floor'
        assert 1 <= c.reorder_point <= max(1, c.equilibrium_qty - 1)
        assert c.pipeline_qty == cov.pipeline_qty(d[c.sku], c.lead_time_mean) == 0
        assert c.pipeline_allowance() == 0
    assert rec['planned_sum_q'] == sum(c.equilibrium_qty for c in planned.orders)
    # The fill rate in the record was priced on exactly these planned levels.
    fill = rec['final']['store']['fill']
    Order.next_sku = 1
    again = cov.fill_rate(load_inventory_from_db(shared['planned_inv_db']).orders,
                          rec['lines_per_day']['store'])
    assert math.isclose(fill['fill_rate'], again['fill_rate'], rel_tol=1e-12)
    assert fill['n_skus'] == len(planned.orders)
    # The stage-A pricing the loop ended on is handed to the derivation.
    sa = shared['era_stage_a']
    assert sa['n_orders'] == len(shared['inventory'].orders)
    assert sa['aisles'] == len(shared['warehouse_meta'].aisles)
    assert math.isclose(sa['channels']['store']['n'], rec['lines_per_day']['store'])


def test_the_derivation_reuses_the_loops_stage_a_and_records_the_coverage():
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits._derive_staffing_for_pair)
    assert "cached = shared.get('era_stage_a')" in src
    assert '_era_cov.stage_a(' in src, 'one function prices the section in both places'
    assert "'coverage': shared.get('coverage')" in src
    assert 'def stage_a(' in inspect.getsource(ec)
    # Nothing prices a section by hand any more: the old inline stage A is gone.
    assert 'PlacementDist.uniform(' not in src


# ═════════════════════════════════════════════════════════════════════════════════════════
# The placement fingerprint
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_placement_fingerprint_names_the_placement_and_not_the_fill_order():
    from Optimization.simdriver.strategy_runner import placement_fingerprint as fp
    a = {1: [(1, 1, 1, 5), (2, 3, 1, 7)], 2: [(1, 2, 2, 1)]}
    b = {2: [(1, 2, 2, 1)], 1: [(2, 3, 1, 7), (1, 1, 1, 5)]}      # same units, other order
    assert fp(a) == fp(b) and len(fp(a)) == 16
    assert fp(a) != fp({1: [(1, 1, 1, 5), (2, 3, 1, 8)], 2: [(1, 2, 2, 1)]})   # a quantity
    assert fp(a) != fp({1: [(1, 1, 1, 5), (2, 3, 2, 7)], 2: [(1, 2, 2, 1)]})   # a row
    assert fp({}) == fp({})


def test_the_arm_stamp_carries_the_fingerprint_beside_its_expected_day():
    from Optimization.simdriver import strategy_runner
    src = inspect.getsource(strategy_runner._arm_expected_pick)
    assert "out['placement_fingerprint'] = placement_fingerprint(bin_map)" in src
    assert "out['bins_filled']" in src
