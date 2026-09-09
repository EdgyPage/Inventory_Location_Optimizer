"""test_first_time_guarantee.py — demand is declared, the picking crew and the line floor
are derived from the joint first-time confidence (ADR-0004).

"Fit the store's window to its own steady state" (.scratch/department-calibration, 2026-09-08)
found that NOTHING IS LOST under the era: a cut or unfilled pick is re-offered next day, so the
crew picks all of demand, and the derivation that priced SERVED units (demand × fill) had
under-staffed both channels by exactly the fill rate.  The declared input flipped -- demand per
channel in the sampler's own unit, the crew solved -- and one scalar, the first-time confidence
`c`, replaced `rho_pick`: the floor is solved so the first-pass fill clears `sqrt(c)`, the crew
is the smallest integer whose expected cut share of units is under `1 - sqrt(c)`.

What this file pins, each against a hand computation or a seeded Monte-Carlo:

  * the crew side's closed forms (`staffing.partial_expectation`, `cut_share`, `solve_pickers`,
    `line_moments`, `units_cv`) -- and the SABOTAGE: a crew sized on served units breaks the
    cut-share bound at the demanded load, on the reference pair's own numbers;
  * the closed form's cut share against a Monte-Carlo of the declared day law;
  * the shelf side (`coverage.solve_floor_lines`): monotone, at-lower-bound, unreachable;
  * the regime's inputs (`staffing_spec` / `staffing_provenance`): the picker keys are None and
    `derived` under the era, the era-only keys None flag-off -- and the record round-trips
    through BOTH restore sites;
  * the CLI refusals in both directions (`_check_era_flags`), the per-arm count under the era;
  * the ONE crew reader (`staffing.channel_crew`) over every record shape, and the readers
    that now go through it;
  * the coverage loop under the era on a real tiny pair: one round at the declaration, the
    floor solved and stamped, the crew solved -- and flag-off byte-identical to before.

Run:  python -m pytest Tests/unit/test_first_time_guarantee.py -q
"""
from __future__ import annotations

import argparse
import inspect
import logging
import math
import random

import numpy as np
import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import (
    CONFIG, STAFFING_KEYS, ERA_ONLY_KEYS, FLAG_OFF_ONLY_KEYS, _SCALAR_DEFAULTS,
    staffing_spec, staffing_provenance, channel_demand, era_on,
)
from Optimization.simconfig import coverage as cov
from Optimization.simconfig import staffing as st
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Order import Order

_LOG = logging.getLogger('test_first_time_guarantee')
S = 28800.0
C = 0.95
SIDE = math.sqrt(C)                       # each side's share of the joint confidence
BOUND = 1.0 - SIDE                        # the crew's expected cut share, at most


# ── fixtures ────────────────────────────────────────────────────────────────────────────

def _order(sku, *, freq, qty, lead=0.0):
    """One catalogue SKU, built the production way; its level is a run's declaration."""
    return Order.build(sku, 'conveyable', 'seasonal', 10, 10, 10, 10, freq, qty,
                       lead_time_mean=lead, supply_cv=0.0)


@pytest.fixture()
def section():
    Order.next_sku = 1
    return [_order(1, freq=0.5, qty=4.0), _order(2, freq=0.25, qty=2.0),
            _order(3, freq=0.25, qty=8.0)]


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    keys = (*STAFFING_KEYS, 'shift_drain_or_cap')
    before = {k: CONFIG['global'].get(k) for k in keys}
    yield CONFIG['global']
    CONFIG['global'].update(before)


def _mean(lam):
    """The mean of `max(1, Poisson(lam))` BY HAND."""
    return lam + math.exp(-lam)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The crew side: closed forms
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_split_is_the_square_root_and_refuses_the_poles():
    assert math.isclose(st.first_time_split(C), SIDE)
    assert math.isclose(st.first_time_split(C) ** 2, C)
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match='first_time_confidence'):
            st.first_time_split(bad)


def test_partial_expectation_is_the_normal_overflow_by_hand():
    """`E[(W - cap)^+] = sd·[φ(z) - z(1 - Φ(z))]`: at z = 0 it is sd/sqrt(2π); at sd = 0 it
    is the plain excess; far above the mean it vanishes; far below it is mean - cap."""
    assert math.isclose(st.partial_expectation(100.0, 10.0, 100.0), 10.0 / math.sqrt(2 * math.pi))
    assert st.partial_expectation(100.0, 0.0, 80.0) == 20.0
    assert st.partial_expectation(100.0, 0.0, 120.0) == 0.0
    assert st.partial_expectation(100.0, 10.0, 200.0) < 1e-9
    assert math.isclose(st.partial_expectation(100.0, 10.0, 0.0), 100.0, rel_tol=1e-9)
    # z = 1: φ(1) - (1 - Φ(1)) = 0.24197 - 0.15866 = 0.08332
    assert math.isclose(st.partial_expectation(100.0, 10.0, 110.0), 10.0 * 0.0833155, rel_tol=1e-4)


def test_solve_pickers_is_the_smallest_crew_inside_the_bound_and_walks_up_from_the_mean_day():
    load, cv = 100_000.0, 0.3
    r = st.solve_pickers(load, cv, S, BOUND)
    K = r['pickers']
    assert st.cut_share(load, cv * load, K, S) <= BOUND
    assert st.cut_share(load, cv * load, K - 1, S) > BOUND, 'not the smallest'
    assert K >= math.ceil((1.0 - BOUND) * load / S), 'below any crew that can meet the bound'
    assert math.isclose(r['expected_utilization'], load / (K * S))
    assert math.isclose(r['sd_s'], cv * load) and r['cut_share_max'] == BOUND
    # THE SMALLEST, not the crew that fits the mean day: at a small spread a crew UNDER
    # the mean day already meets the bound (the reviewer's case: 10.1 days of load at
    # cv 0.02 solves to 10, not 11) -- and the plain excess is what a zero spread leaves.
    small = st.solve_pickers(10.1 * S, 0.02, S, BOUND)
    assert small['pickers'] == 10 and small['cut_share'] <= BOUND
    z = st.solve_pickers(load, 0.0, S, BOUND)
    assert z['pickers'] == math.ceil((1.0 - BOUND) * load / S) and z['cut_share'] <= BOUND
    assert st.cut_share(load, 0.0, z['pickers'] - 1, S) > BOUND
    # no load fields nobody; a bad bound is refused; an unreachable one says so
    assert st.solve_pickers(0.0, cv, S, BOUND)['pickers'] == 0
    with pytest.raises(ValueError, match='cut_share_max'):
        st.solve_pickers(load, cv, S, 1.0)
    # (a bound of zero IS reached once the cap sits ~8 sd above the mean and the Normal
    # tail underflows, so the guard must bite before that: k_max well inside the spread)
    with pytest.raises(ValueError, match='not reachable'):
        st.solve_pickers(load, cv, S, 0.0, k_max=5)


def test_the_reference_pair_solves_to_the_crew_the_ticket_predicted():
    """The store leaf of the 2026-09-08 era check, on the record's own numbers: 6,125.3
    demanded units/day at 108.371 s/unit (663,800 s), unit cv 0.3343.  "Fit the store's
    window": crew ~32 at ~0.72 with an expected cut share under 0.0253."""
    load = 6125.3 * 108.371
    r = st.solve_pickers(load, 0.3343, S, BOUND)
    assert r['pickers'] == 32
    assert 0.71 < r['expected_utilization'] < 0.73
    assert r['cut_share'] <= BOUND < st.cut_share(load, 0.3343 * load, 31, S)


def test_sabotage_pricing_on_served_units_breaks_the_cut_share_bound():
    """THE DEFECT the flip closes.  The previous derivation priced the crew on SERVED units
    (demand × the 0.922 fill rate).  Nothing is lost under the era, so the day's work is the
    DEMANDED load -- and the served-unit crew, sized inside the bound on the wrong load,
    stands outside it on the right one.  On the reference pair: 29 pickers instead of 32,
    and a cut share of 0.042 against the 0.0253 promised."""
    load_demanded = 6125.3 * 108.371
    fill = 0.9220
    cv = 0.3343
    served_crew = st.solve_pickers(load_demanded * fill, cv, S, BOUND)
    assert served_crew['cut_share'] <= BOUND, 'inside the bound on the load it was priced on'
    real = st.cut_share(load_demanded, cv * load_demanded, served_crew['pickers'], S)
    assert real > BOUND, 'the served-unit crew must FAIL the bound at the demanded load'
    assert served_crew['pickers'] < st.solve_pickers(load_demanded, cv, S, BOUND)['pickers']
    # ...and the derivation itself prices the DEMANDED units: nowhere does a fill rate
    # reduce a picking load (memory `nothing-is-lost-under-the-era`).
    src = inspect.getsource(st.derive)
    assert 'pick_load_s = units_day * s_pick' in src
    assert 'served_day' not in src and 'fill' not in src.split('def derive')[-1].split("'put'")[0]


def test_line_moments_and_the_units_cv_by_hand(section):
    """`E[q]` and `E[q²]` per SKU off the stamped law, weighted by line share; then
    `Var[U] = n·Var[q] + (cv·n)²·E[q]²` -- decision 8 of "Fit the store's window"."""
    m = st.line_moments(section)
    pis = (0.5, 0.25, 0.25)
    lams = (4.0, 2.0, 8.0)
    m1 = sum(p * _mean(l) for p, l in zip(pis, lams))
    assert math.isclose(m['m1'], m1, rel_tol=1e-9)
    # E[q²] of max(1, X): E[X²] + P(X = 0)·(1 - 0) = λ + λ² + e^-λ
    m2 = sum(p * (l + l * l + math.exp(-l)) for p, l in zip(pis, lams))
    assert math.isclose(m['m2'], m2, rel_tol=1e-6)
    assert math.isclose(m['var'], m2 - m1 * m1, rel_tol=1e-6)
    n, cvl = 200.0, 0.25
    var_u = n * m['var'] + (cvl * n) ** 2 * m1 * m1
    assert math.isclose(st.units_cv(n, cvl, m), math.sqrt(var_u) / (n * m1), rel_tol=1e-9)
    # the Gaussian line count carries nearly all of it; a zero-spread day keeps the rest
    assert st.units_cv(n, cvl, m) > cvl > st.units_cv(n, 0.0, m) > 0.0
    assert st.units_cv(0.0, cvl, m) == 0.0
    assert st.line_moments([]) == {'m1': 0.0, 'm2': 0.0, 'var': 0.0, 'n_skus': 0}


def test_the_closed_form_cut_share_matches_a_monte_carlo_of_the_declared_law(section):
    """The declared law: `N ~ Normal(n, cv·n)` lines (floored at one, as the sampler floors),
    each on a SKU by line share, each demanding its stamped law's draw; the day's work is
    the units at `s_pick`.  The closed form is the Normal partial expectation at the day's
    unit cv; the Monte-Carlo is the compound sum itself.  Seeded; 20,000 days; the day is
    sized so the cap sits inside the spread (z ≈ 0.4), where the overflow is neither zero
    nor the whole load."""
    n, cvl, s_pick, K, day = 200.0, 0.25, 12.0, 3, 4000.0
    m = st.line_moments(section)
    load = n * m['m1'] * s_pick
    closed = st.cut_share(load, st.units_cv(n, cvl, m) * load, K, day)
    assert 0.02 < closed < 0.2, 'the fixture must sit inside the spread to test anything'
    rng = random.Random(7)
    nrng = np.random.default_rng(7)
    pis = np.array([0.5, 0.25, 0.25])
    W = np.empty(20_000)
    for i in range(W.size):
        N = max(1, int(round(nrng.normal(n, cvl * n))))
        picks = nrng.choice(3, size=N, p=pis)
        W[i] = sum(section[j].demand.line.sample(rng) for j in picks) * s_pick
    mc = float(np.maximum(W - K * day, 0.0).mean() / W.mean())
    assert math.isclose(closed, mc, rel_tol=0.05), (closed, mc)
    assert math.isclose(W.mean(), load, rel_tol=0.02)


# ═════════════════════════════════════════════════════════════════════════════════════════
# The shelf side: the solved floor
# ═════════════════════════════════════════════════════════════════════════════════════════

def _fill_at(section, n, f):
    cov.rescale_section(section, n, coverage_days=10.0, safety_days=2.0, floor_lines=f)
    return cov.fill_rate(section, n)['fill_rate']


def test_the_floor_solves_to_the_smallest_line_count_that_clears_the_fill(section):
    # A hundredth of a line a day over three SKUs puts every SKU ON its floor at ten days
    # of coverage -- the reference catalogue's regime (588 lines over 240k SKUs), where
    # the fill is a pure function of the floor.
    n = 0.01
    r = cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0, fill_min=SIDE)
    f = r['floor_lines']
    assert r['fill_rate'] >= SIDE and not r['at_lower_bound']
    assert math.isclose(r['fill_rate'], _fill_at(section, n, f), rel_tol=1e-12)
    # smallest: one tolerance below, the fill is under the target
    assert _fill_at(section, n, f - 2 * cov.FLOOR_SOLVE_TOL) < SIDE
    assert f >= cov.DEFAULT_FLOOR_LINES
    # the orders are left declared at the solved floor
    _ = r
    cov.rescale_section(section, n, coverage_days=10.0, safety_days=2.0, floor_lines=f)
    levels = [(c.equilibrium_qty, c.reorder_point) for c in section]
    cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0, fill_min=SIDE)
    assert [(c.equilibrium_qty, c.reorder_point) for c in section] == levels
    # monotone: a higher confidence needs a higher floor
    hi = cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0, fill_min=0.999)
    assert hi['floor_lines'] > f and hi['fill_rate'] >= 0.999


def test_the_floor_stays_at_one_line_when_one_line_already_clears_it_and_refuses_the_unreachable(section):
    n = 0.01                                     # every SKU on the floor (see above)
    assert _fill_at(section, n, 1.0) < SIDE, 'the fixture must NOT clear the side at one line'
    lo = cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0, fill_min=0.5)
    assert lo['at_lower_bound'] and lo['floor_lines'] == cov.DEFAULT_FLOOR_LINES
    assert lo['bracket'] == (1.0, 1.0)
    # a thousand lines a day gives every SKU ten days of stock, far above its floor: the
    # one-line floor clears any side and nothing is solved
    assert cov.solve_floor_lines(section, 1000.0, coverage_days=10.0, safety_days=2.0,
                                 fill_min=SIDE)['at_lower_bound']
    with pytest.raises(ValueError, match='cannot reach'):
        cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0,
                              fill_min=SIDE, hi_max=1.2)
    with pytest.raises(ValueError, match='fill_min'):
        cov.solve_floor_lines(section, n, coverage_days=10.0, safety_days=2.0, fill_min=1.0)
    # no demand: nothing to solve, the one-line floor stands
    z = cov.solve_floor_lines(section, 0.0, coverage_days=10.0, safety_days=2.0, fill_min=SIDE)
    assert z['at_lower_bound'] and z['floor_lines'] == cov.DEFAULT_FLOOR_LINES


def test_a_section_above_its_floor_solves_the_same_root_through_coverage_days(section):
    """A tiny line count puts every SKU ABOVE its floor (coverage × d_s > L_s never binds the
    other way here); the fill then depends on `coverage_days` through the levels, and the
    solve reads it through the same formula -- a longer coverage needs no floor at all."""
    n = 0.05
    short = cov.solve_floor_lines(section, n, coverage_days=1.0, safety_days=0.0, fill_min=SIDE)
    long = cov.solve_floor_lines(section, n, coverage_days=5000.0, safety_days=0.0, fill_min=SIDE)
    assert long['at_lower_bound'], 'with thousands of days of stock the one-line floor clears'
    assert short['floor_lines'] >= long['floor_lines']


# ═════════════════════════════════════════════════════════════════════════════════════════
# The regime's inputs, the record, and both restore sites
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_regime_decides_which_keys_are_inputs(restore):
    assert set(ERA_ONLY_KEYS) | set(FLAG_OFF_ONLY_KEYS) <= set(STAFFING_KEYS)
    assert not set(ERA_ONLY_KEYS) & set(_SCALAR_DEFAULTS)
    restore.update(shift_drain_or_cap=False, store_pickers=7, ff_pickers=5, rho_pick=0.7)
    off = staffing_spec()
    assert (off['store_pickers'], off['ff_pickers'], off['rho_pick']) == (7, 5, 0.7)
    assert all(off[k] is None for k in ERA_ONLY_KEYS)
    assert channel_demand('store') is None
    restore['shift_drain_or_cap'] = True
    on = staffing_spec()
    assert all(on[k] is None for k in FLAG_OFF_ONLY_KEYS), 'the crew is derived, rho unread'
    assert (on['store_demand'], on['ff_demand']) == (_s.STORE_DEMAND, _s.FF_DEMAND)
    assert on['first_time_confidence'] == _s.FIRST_TIME_CONFIDENCE == C
    assert channel_demand('store') == _s.STORE_DEMAND
    restore.update(store_demand=0.01, first_time_confidence=0.9)
    on = staffing_spec()
    assert (on['store_demand'], on['first_time_confidence']) == (0.01, 0.9)
    assert set(on) == set(STAFFING_KEYS) == set(off)
    restore['store_demand'] = 1.5
    with pytest.raises(ValueError, match='store_demand'):
        channel_demand('store')


def test_the_reference_defaults_reproduce_the_previous_fixed_point():
    """The declared demand's defaults were chosen so the reference pair's warehouse, levels
    and script family did not move when the input flipped: 588.65 store / 2,903.2
    fulfillment lines a day over 239,938 / 160,062 SKUs."""
    assert math.isclose(_s.STORE_DEMAND * 239_938, 588.65, rel_tol=1e-4)
    assert math.isclose(_s.FF_DEMAND * 160_062, 2903.2, rel_tol=1e-4)
    assert _s.FLOOR_LINES is None and _s.RHO_PICK == 0.85


def test_provenance_follows_the_regime(restore):
    restore.update(shift_drain_or_cap=False, floor_lines=None)
    off = staffing_provenance({'store_pickers', 'rho_pick'})
    assert off['store_pickers'] == 'declared' and off['ff_pickers'] == 'assumed'
    assert off['rho_pick'] == 'declared'
    assert not set(ERA_ONLY_KEYS) & set(off), 'a key the regime does not read has no provenance'
    assert off['floor_lines'] == 'assumed'                       # the one-line default
    restore['shift_drain_or_cap'] = True
    on = staffing_provenance({'store_demand'})
    assert on['store_pickers'] == on['ff_pickers'] == 'derived'
    assert 'rho_pick' not in on
    assert on['store_demand'] == 'declared' and on['ff_demand'] == 'assumed'
    assert on['first_time_confidence'] == 'assumed'
    assert on['floor_lines'] == 'derived'                        # solved
    restore['floor_lines'] = 1.5
    assert staffing_provenance({'floor_lines'})['floor_lines'] == 'declared'


def test_the_record_round_trips_through_both_restore_sites(tmp_path, restore):
    """Seam 4, both halves: the era's inputs written to the run spec come back through
    `_apply_run_spec` (resume) and `_apply_run_shape` (analysis) so that `staffing_spec()`
    reads the same record -- picker keys None, demand and confidence as declared."""
    from Optimization.run_simulation import _apply_run_spec
    from Optimization import run_analysis
    from Optimization.runschema.sim_manifest import _write_run_spec
    restore.update(shift_drain_or_cap=True, store_demand=0.003, ff_demand=0.02,
                   first_time_confidence=0.9, floor_lines=None)
    inputs = staffing_spec()
    spec = {'n_batches': 3, 'shift_drain_or_cap': True,
            'staffing': {'inputs': inputs, 'provenance': staffing_provenance({'store_demand'})}}
    # resume: the saved inputs overlay the args
    args = argparse.Namespace(**{k: None for k in STAFFING_KEYS}, shift_drain_or_cap=True)
    _apply_run_spec(args, spec, explicit=set())
    assert (args.store_demand, args.ff_demand, args.first_time_confidence) == (0.003, 0.02, 0.9)
    assert args.store_pickers is None and args.rho_pick is None
    # analysis: the saved inputs restore into CONFIG, and the accessor reads them back
    restore.update(store_demand=0.5, ff_demand=0.5, first_time_confidence=0.5)   # perturb
    _write_run_spec(str(tmp_path), spec)
    run_analysis._apply_run_shape(str(tmp_path), _LOG)
    assert era_on() is True
    assert staffing_spec() == inputs
    assert run_analysis._RUN_STAFFING['provenance']['store_pickers'] == 'derived'


# ═════════════════════════════════════════════════════════════════════════════════════════
# The CLI: refusals in both directions, and the per-arm count
# ═════════════════════════════════════════════════════════════════════════════════════════

def _ns(**over) -> argparse.Namespace:
    base = dict(shift_drain_or_cap=False, releases_per_day=None, roll_over_unpicked=False,
                cut_at_day_end=False, put_queue_split=False, put_swap_coef=0.0)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.parametrize('flag', FLAG_OFF_ONLY_KEYS)
def test_a_typed_crew_or_picking_target_under_the_era_is_an_error(flag):
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match=flag.replace('_', '-')):
        _check_era_flags(_ns(shift_drain_or_cap=True), explicit={'shift_drain_or_cap', flag})


@pytest.mark.parametrize('flag', ERA_ONLY_KEYS)
def test_a_typed_demand_or_confidence_without_the_era_is_an_error(flag):
    from Optimization.run_simulation import _check_era_flags
    with pytest.raises(SystemExit, match=flag.replace('_', '-')):
        _check_era_flags(_ns(), explicit={flag})


def test_each_regime_accepts_its_own_flags():
    from Optimization.run_simulation import _check_era_flags
    assert _check_era_flags(_ns(), explicit={'store_pickers', 'rho_pick'}) == []
    notes = _check_era_flags(_ns(shift_drain_or_cap=True),
                             explicit={'shift_drain_or_cap', 'store_demand',
                                       'first_time_confidence', 'floor_lines'})
    assert len(notes) == 3                                       # the era's completions only


@pytest.mark.parametrize('flag', ['--store-demand', '--ff-demand', '--first-time-confidence'])
def test_each_new_knob_has_a_flag(flag):
    from Optimization import run_simulation
    assert f"'{flag}'" in inspect.getsource(run_simulation), flag


def test_a_per_arm_count_is_refused_under_the_era_even_when_it_restates_the_placeholder(
        monkeypatch, restore):
    from Optimization.simdriver.workunits import _channel_runs_for
    from Optimization.config.sim_config import STORE_CONFIGS

    class _NoOrders:
        orders: list = []

    restore.update(shift_drain_or_cap=True, store_pickers=25)
    monkeypatch.setitem(CONFIG['channels']['store'], 'configs',
                        [{**STORE_CONFIGS[0], 'num_pickers': 25}])
    with pytest.raises(ValueError, match='DERIVED from the declared demand'):
        _channel_runs_for(_NoOrders())
    restore['shift_drain_or_cap'] = False
    _mixed, runs = _channel_runs_for(_NoOrders())                # flag-off: restating is legal
    assert runs[0][0].picker.num_pickers == 25


# ═════════════════════════════════════════════════════════════════════════════════════════
# The one crew reader
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_channel_crew_prefers_the_derived_block_over_every_record_shape():
    spec_shape = {'inputs': {'store_pickers': None, 'ff_pickers': None},
                  'derived': {'pairA': {'channels': {'store': {'pickers': 32},
                                                     'fulfillment': {'pickers': 14}}}}}
    assert st.channel_crew(spec_shape, channel='store', pair='pairA') == 32
    assert st.channel_crew(spec_shape, channel='fulfillment', pair='pairA') == 14
    assert st.channel_crew(spec_shape, channel=None, pair='pairA') == 32
    payload = {'inputs': {'store_pickers': None}, 'derived': spec_shape['derived']['pairA']}
    assert st.channel_crew(payload, channel='store') == 32
    # an older era record restates the declared crew in `derived`; both agree
    old = {'inputs': {'store_pickers': 25}, 'derived': {'p': {'channels': {'store': {'pickers': 25}}}}}
    assert st.channel_crew(old, channel='store', pair='p') == 25
    # flag-off: the declared key, from the record or the bare inputs dict
    assert st.channel_crew({'inputs': {'store_pickers': 7, 'ff_pickers': 5}}, channel='fulfillment') == 5
    assert st.channel_crew({'store_pickers': 7, 'ff_pickers': 5}, channel='store') == 7
    with pytest.raises(KeyError, match='no picking crew'):
        st.channel_crew(spec_shape, channel='store', pair='pairB')
    with pytest.raises(KeyError, match='fulfillment'):
        st.channel_crew({'inputs': {'store_pickers': 7}}, channel='fulfillment')


def test_the_worker_checks_its_crew_against_the_derived_block():
    from Optimization.simdriver.strategy_runner import _check_declared_crew
    p = {'channel_name': 'store', 'put_crew': {'size': 4}, 'recv_crew': {'size': 2},
         'staffing': {'inputs': {'store_pickers': None, 'ff_pickers': None},
                      'derived': {'channels': {'store': {'pickers': 32}},
                                  'put': {'crew': 4}, 'receiving': {'crew': 2}}}}
    _check_declared_crew(p, 32)
    with pytest.raises(ValueError, match='derives store_pickers=32'):
        _check_declared_crew(p, 25)


def test_the_expectations_read_the_derived_crew():
    from Optimization.simconfig import equilibrium as eq
    rec = {'inputs': {'store_pickers': None, 'band_tol': 0.1},
           'derived': {'p': {'day_seconds': S,
                             'channels': {'store': {'pickers': 32,
                                                    'expected_utilization': {'pick': 0.72},
                                                    's_pick': {'value': 108.0}}},
                             'put': {}, 'receiving': {}}},
           'calibration': {'p': {}}}
    e = eq.expectations_for(rec, pair='p', channel='store')
    assert e['departments']['pick'] == {'crew': 32, 'expected': 0.72, 's_pick': 108.0}
    assert eq.picker_key('fulfillment') == 'ff_pickers'


def test_the_derived_crew_reaches_the_channel_and_the_payload():
    from Optimization.simdriver import workunits
    src = inspect.getsource(workunits._derive_staffing_for_pair)
    assert "K = int(a['pickers'])" in src
    assert 'picker = _dc_replace(ch.picker, num_pickers=K,' in src
    assert "cost=_dc_replace(ch.picker.cost, num_pickers=K))" in src
    assert 'new_ch = _dc_replace(ch, picker=picker,' in src
    assert "'guarantee': a.get('guarantee')" in src


# ═════════════════════════════════════════════════════════════════════════════════════════
# The loop under the era, on a real tiny pair -- and flag-off byte-identical
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_era_declares_the_day_solves_the_floor_and_the_crew_in_one_round(tmp_path, restore):
    from test_coverage_rescale import _tiny_pair
    from Optimization.simdriver import sim_assets
    from Warehouse.generation.generate_inventory import load_inventory_from_db
    restore.update(shift_drain_or_cap=True, coverage_days=10.0, safety_days=2.0,
                   floor_lines=None, store_demand=0.05)
    inv_db, aff_db = _tiny_pair(tmp_path)
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    rec = shared['coverage']
    Order.next_sku = 1
    n_skus = len(load_inventory_from_db(inv_db).orders)
    # ONE round: the seed is the declaration and stage A answers with it.
    assert rec['seed'] == {'method': 'declared', 'lines_per_day': {'store': 0.05 * n_skus}}
    assert len(rec['rounds']) == 2 and rec['converged'] is True
    assert math.isclose(rec['lines_per_day']['store'], 0.05 * n_skus)
    assert rec['residual'] == {'store': 0.0}
    # the floor: solved, stamped, the fill clears the side
    floor = rec['floor']['store']
    assert floor['provenance'] == 'derived'
    assert floor['solved']['fill_min'] == SIDE and floor['solved']['fill_rate'] >= SIDE
    assert rec['final']['store']['floor_lines'] == floor['floor_lines']
    assert rec['final']['store']['fill']['fill_rate'] >= SIDE
    # the crew: solved from the guarantee, at the declared demanded units, stamped
    a = shared['era_stage_a']['channels']['store']
    assert a['pickers_provenance'] == 'derived' and a['pickers'] >= 1
    g = a['guarantee']
    assert g['first_time_confidence'] == C and g['side'] == SIDE
    assert g['crew']['pickers'] == a['pickers'] and g['crew']['cut_share'] <= BOUND
    d = a['demand']
    assert d['declared'] == 0.05 and d['n_skus'] == len(a['orders'])
    assert math.isclose(d['units_per_day'], a['daily_demand_units'])
    assert math.isclose(d['units_per_day'], d['lines_per_day'] * a['analytic']['units_per_line'])
    assert d['units_per_day'] >= d['served_units_per_day']
    assert math.isclose(g['crew']['load_s'], d['units_per_day'] * a['s_pick']['value'])
    assert math.isclose(a['batch']['mean_fraction'], 0.05, rel_tol=1e-9)
    assert math.isclose(a['batch']['std_fraction'], 0.05 * (_s.STORE_BATCH_STD / _s.STORE_BATCH_MEAN))
    # a typed floor BELOW the solved one refuses; AT it is accepted and stamped declared
    restore['floor_lines'] = max(cov.DEFAULT_FLOOR_LINES, floor['floor_lines'] - 0.05)
    if restore['floor_lines'] < floor['floor_lines']:
        with pytest.raises(ValueError, match='below the'):
            sim_assets.build_shared_assets(
                inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh2' / 'warehouse.db'))
    restore['floor_lines'] = floor['floor_lines'] + 0.5
    again = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh3' / 'warehouse.db'))
    f2 = again['coverage']['floor']['store']
    assert f2['provenance'] == 'declared' and f2['floor_lines'] == floor['floor_lines'] + 0.5
    assert f2['solved']['floor_lines'] == floor['solved']['floor_lines']


def test_flag_off_is_byte_identical_to_the_declared_crew_regime(tmp_path, restore):
    """The store-only path with the era off: the crew is declared, `rho_pick` sizes the
    fixed point, the floor is one line, nothing is solved and no guarantee is stamped --
    the record's shape moved (a `floor` block, `floor_lines` None as the input) but every
    number is what the previous regime declared."""
    from test_coverage_rescale import _tiny_pair
    from Optimization.simdriver import sim_assets
    restore.update(shift_drain_or_cap=False, store_pickers=3, rho_pick=0.85, floor_lines=None)
    inv_db, aff_db = _tiny_pair(tmp_path)
    shared = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh' / 'warehouse.db'))
    rec = shared['coverage']
    assert rec['seed']['method'] == 'analytic_pick'
    assert rec['floor_lines'] is None
    assert rec['floor']['store'] == {'floor_lines': cov.DEFAULT_FLOOR_LINES,
                                     'provenance': 'assumed', 'solved': None}
    assert rec['final']['store']['floor_lines'] == cov.DEFAULT_FLOOR_LINES
    assert ec.floors_at(rec) == {'store': cov.DEFAULT_FLOOR_LINES}
    a = shared['era_stage_a']['channels']['store']
    assert a['pickers'] == 3 and a['pickers_provenance'] == 'declared'
    assert a['guarantee'] is None and a['demand'] is None
    # the fixed point fills the declared capacity: K × S × rho
    assert math.isclose(a['expected']['total_s'], 3 * S * 0.85, rel_tol=1e-4) \
        or a['expected'].get('saturated')
    # ...and a typed floor is simply the floor
    restore['floor_lines'] = 1.5
    again = sim_assets.build_shared_assets(
        inv_db, aff_db, _LOG, warehouse_db_path=str(tmp_path / 'wh2' / 'warehouse.db'))
    assert again['coverage']['floor']['store'] == {'floor_lines': 1.5, 'provenance': 'declared',
                                                    'solved': None}


def test_floors_at_reads_per_channel_and_falls_back_to_the_scalar_then_the_default():
    new = {'floor_lines': None, 'final': {'store': {'floor_lines': 1.27},
                                          'fulfillment': {'floor_lines': 1.31}}}
    f = ec.floors_at(new)
    assert (f['store'], f['fulfillment']) == (1.27, 1.31)
    assert f['returns'] == cov.DEFAULT_FLOOR_LINES                # an unknown channel
    old = {'floor_lines': 1.0, 'final': {'store': {'floor_lines': 1.0}}}
    assert ec.floors_at(old)['fulfillment'] == 1.0
    pre = {'floor_lines': 1.5, 'rounds': [{'rescaled_at': {'store': 10.0}}]}
    assert ec.floors_at(pre)['store'] == 1.5
