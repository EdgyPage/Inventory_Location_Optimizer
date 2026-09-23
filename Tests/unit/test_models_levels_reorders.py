"""test_models_levels_reorders.py -- the LEVELS and REORDERS closed-form models.

`Optimization/simconfig/models/levels.py` is the declaration chain `coverage.py` stamps on every
SKU; its equations are mirrored against `coverage.py` by `test_closed_form.py`'s gate, and S02
reproduced 800,000 SKUs' Q, r and P exactly.  This file pins what the gate cannot see: the
model's COMPOSITION (the chain evaluated whole, the floor regime flag) and the reorder model's
renewal law, which has no simulator function to mirror -- so it is checked against a replica of
the simulator's position rule (`inventory_reorder._fire_reorders`) run on seeded lines.

Run:  python -m pytest Tests/unit/test_models_levels_reorders.py -q
"""
from __future__ import annotations

import math
import random

import pytest

from Optimization.simconfig.models.levels import LEVELS
from Optimization.simconfig.models.reorders import (REORDERS, line_pmf, noise_pmf,
                                                    renewal_lines, renewal_lines_noisy)


# ── levels ───────────────────────────────────────────────────────────────────────────────────

def _inputs(**over):
    base = {'lam': 4, 'n': 60.0, 'pi': 1e-4, 'f_L': 1.3078, 'supplier': 0.0, 'unit': 1.0,
            'transit': 1.766, 'C': 10.0, 'safety': 2.0}
    base.update(over)
    return base


def test_the_chain_composes_the_records_levels():
    from Optimization.simconfig.coverage import pipeline_qty, stock_levels
    r = LEVELS.evaluate(_inputs())
    Eq = 4 + math.exp(-4)
    assert math.isclose(r['Eq'], Eq)
    assert r['L'] == math.ceil(1.3078 * Eq - 1e-9)
    assert (r['Q'], r['rp']) == stock_levels(r['d'], 1.766, 10.0, 2.0, int(r['L']))
    assert r['P'] == pipeline_qty(r['d'], 1.766)
    assert r['S'] == r['Q'] + r['P']


def test_a_slow_sku_sits_on_its_floor_and_a_fast_one_leaves_it():
    slow = LEVELS.evaluate(_inputs(pi=1e-4))
    assert slow['on_floor'] == 1 and slow['Q'] == slow['L'] and slow['rp'] == slow['Q'] - 1
    fast = LEVELS.evaluate(_inputs(pi=0.05))            # d = 60 * 0.05 * 4.02 = 12 units/day
    assert fast['on_floor'] == 0 and fast['Q'] == round(10.0 * fast['d']) > fast['L']
    assert fast['P'] == round(fast['d'] * 1.766)


# ── reorders ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('lam', [1, 3, 8])
def test_the_line_pmf_is_max_one_poisson(lam):
    p = line_pmf(lam, 60)
    assert p[0] == 0.0
    assert math.isclose(sum(p), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(k * pk for k, pk in enumerate(p)), lam + math.exp(-lam), rel_tol=1e-9)


def test_the_renewal_count_in_closed_cases():
    assert renewal_lines(5, 1) == 1.0, 'P = 0: every line fires'
    lam = 2.0
    p1 = math.exp(-lam) * (1 + lam)                    # P(q = 1)
    assert math.isclose(renewal_lines(lam, 2), 1 + p1, rel_tol=1e-12)


def test_the_noise_law_is_a_centred_rounded_normal():
    pm = noise_pmf(10.0, 0.1)
    assert math.isclose(sum(pm.values()), 1.0, abs_tol=1e-12)
    assert abs(sum(e * p for e, p in pm.items())) < 1e-9
    assert noise_pmf(10.0, 0.0) == {0: 1.0}


def _replica(lam, Q, P, cv, n_lines, rng):
    """The simulator's position rule on a stream of lines: position = on hand + on order;
    a line takes min(q, on hand) -- here the shelf never runs dry, so q -- and fires when the
    position is at or below r = Q - 1, ordering max(1, round(N(ideal, ideal cv)))."""
    rp, S = Q - 1, Q + P
    position, fires = S, 0

    def draw_q():
        # Knuth's Poisson, floored at one -- the line law.
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= L:
                return max(1, k)
            k += 1

    for _ in range(n_lines):
        position -= draw_q()
        if position <= rp:
            ideal = S - position
            qty = max(1, round(rng.gauss(ideal, ideal * cv))) if cv > 0 else ideal
            position += qty
            fires += 1
    return fires / n_lines


@pytest.mark.parametrize('lam, P, cv', [(2, 0, 0.0), (2, 1, 0.0), (1, 2, 0.0), (3, 1, 0.12),
                                        (6, 0, 0.15), (1, 0, 0.1)])
def test_fires_per_line_match_a_replica_of_the_position_rule(lam, P, cv):
    rng = random.Random(4242 + int(lam * 10) + P)
    got = _replica(lam, Q=20, P=P, cv=cv, n_lines=200_000, rng=rng)
    want = 1.0 / renewal_lines_noisy(lam, P, cv)
    assert math.isclose(got, want, rel_tol=0.01), (got, want)


def test_the_model_reads_rates_and_orders_back_every_unit():
    r = REORDERS.evaluate({'lam': 3, 'P': 0, 'p': 0.02, 'cv': 0.0})
    assert r['N'] == 1.0 and math.isclose(r['fires'], 0.02)
    assert math.isclose(r['ordered'], 0.02 * (3 + math.exp(-3)))
    assert math.isclose(r['lot'], 3 + math.exp(-3))
