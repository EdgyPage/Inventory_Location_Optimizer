"""The churn model (`Optimization/simconfig/models/churn.py`): each closed form against a direct
computation it claims to summarise -- a seeded Monte Carlo, a brute-force enumeration, or the
greedy rule it is the fluid limit of.  The campaign verification lives in the study
(`.scratch/aisle-churn/whiteboard/S08-*.md`, `S09-*.md`); these pin the arithmetic."""
from __future__ import annotations

import itertools
import math
import random
import statistics

import pytest

from Optimization.simconfig.models import churn


# ── the fresh-bin law ────────────────────────────────────────────────────────────────────────

def _served_exact(times, ell):
    return sum(1 for i, t in enumerate(times) if any(t - a >= ell for a in times[:i]))


def test_beta_inc_int_matches_the_beta_integral():
    # I_x(a, b) against a midpoint quadrature of the Beta density
    for a, b, x in ((1, 1, 0.3), (2, 5, 0.1), (4, 3, 0.6), (7, 2, 0.9)):
        B = math.gamma(a) * math.gamma(b) / math.gamma(a + b)
        n = 20000
        quad = sum(((k + 0.5) * x / n) ** (a - 1) * (1 - (k + 0.5) * x / n) ** (b - 1)
                   for k in range(n)) * x / n / B
        assert churn.beta_inc_int(x, a, b) == pytest.approx(quad, rel=1e-6)
    assert churn.beta_inc_int(0.0, 2, 3) == 0.0
    assert churn.beta_inc_int(1.0, 2, 3) == 1.0


@pytest.mark.parametrize('lam,H,ell', [(0.05, 40, 2.77), (0.4, 40, 2.77), (2.0, 25, 1.0)])
def test_unconditional_served_matches_a_poisson_process(lam, H, ell):
    rng = random.Random(7)
    reps, tot = 40000, 0
    for _ in range(reps):
        t, times = 0.0, []
        while True:
            t += rng.expovariate(lam)
            if t > H:
                break
            times.append(t)
        tot += _served_exact(times, ell)
    r = churn.FRESH.evaluate({'lam': lam, 'H': H, 'ell': ell})
    se = math.sqrt(lam * H / reps) * 2 + 1e-3
    assert tot / reps == pytest.approx(r['served'], abs=4 * se)
    assert r['phi'] == pytest.approx(r['served'] / (lam * H))


@pytest.mark.parametrize('n,H,ell', [(2, 40, 2.77), (5, 40, 2.77), (12, 10, 3.0)])
def test_count_conditional_served_matches_uniform_points(n, H, ell):
    rng = random.Random(11)
    reps = 40000
    tot = sum(_served_exact(sorted(rng.uniform(0, H) for _ in range(n)), ell)
              for _ in range(reps))
    assert tot / reps == pytest.approx(churn.served_given_count(n, H, ell), abs=0.02)
    assert churn.served_given_count(1, H, ell) == 0.0
    assert churn.served_given_count(n, ell, ell) == 0.0


def test_section_phi_weights_by_lines_and_is_linear_for_sparse_skus():
    # sparse: served ~ x^2 / 2, so phi ~ lam (H - l)^2 / (2 H) -- linear in the density
    lo = churn.section_phi([1e-4], 40, 2.77)
    hi = churn.section_phi([3e-4], 40, 2.77)
    assert hi / lo == pytest.approx(3.0, rel=1e-2)       # 3 - O(x): x = 0.0037 here
    mixed = churn.section_phi([0.01, 1.0], 40, 2.77)
    a = churn.FRESH.evaluate({'lam': 0.01, 'H': 40, 'ell': 2.77})['served']
    b = churn.FRESH.evaluate({'lam': 1.0, 'H': 40, 'ell': 2.77})['served']
    assert mixed == pytest.approx((a + b) / (1.01 * 40))


# ── water-filling is the fluid limit of the LPT balancer ────────────────────────────────────

def test_water_level_conserves_the_added_load():
    loads = [3.0, 1.0, 7.0, 2.0, 2.5]
    for W in (0.5, 2.0, 6.0, 40.0):
        lam, share = churn.water_level(loads, W)
        assert sum(max(0.0, lam - L) for L in loads) == pytest.approx(W)
        assert sum(share) == pytest.approx(1.0)
        assert all(s == 0.0 for s, L in zip(share, loads) if L >= lam)


def test_water_level_is_where_greedy_lpt_lands():
    rng = random.Random(3)
    loads = [rng.uniform(0, 100) for _ in range(30)]
    cur = list(loads)
    got = [0.0] * 30
    for _ in range(20000):                       # small increments, least-loaded first
        i = min(range(30), key=cur.__getitem__)
        cur[i] += 0.05
        got[i] += 0.05
    _lam, share = churn.water_level(loads, 1000.0)
    for g, s in zip(got, share):
        assert g / 1000.0 == pytest.approx(s, abs=0.002)
    assert churn.effective_count([1, 1, 1, 1]) == pytest.approx(4.0)
    assert churn.effective_count([1, 0, 0]) == pytest.approx(1.0)


# ── the frontier law ─────────────────────────────────────────────────────────────────────────

def test_frontier_spends_the_good_bracket_then_holds_the_occupancy_share():
    # 20 free ground bins (M = 1) and plenty of free upper (M = 1.4) at the same travel; every
    # unit prefers ground.  With no frees the ground share is 1 until 20 are placed, then 0.
    free = {1.0: [float(i) for i in range(20)], 1.4: [float(i) for i in range(1000)]}
    occ = {1.0: [float(i) for i in range(100)], 1.4: [float(i) for i in range(300)]}
    out = churn.frontier_shares(free, occ, {d: 10 for d in range(6)}, 0.0, [3000.0], 6, steps=10)
    assert out[0][1.0] == pytest.approx(1.0) and out[1][1.0] == pytest.approx(1.0)
    assert out[3][1.0] == pytest.approx(0.0) and out[5][1.4] == pytest.approx(1.0)
    # with frees = placements, the long-run ground share is the occupancy share sigma_G = 0.25
    out = churn.frontier_shares(free, occ, {d: 10 for d in range(80)}, 10.0, [3000.0], 80,
                                psi=0.3, steps=10)
    tail = statistics.mean(out[d][1.0] for d in range(60, 80))
    assert tail == pytest.approx(0.25, abs=0.03)


def test_frontier_argmin_prices_height_against_travel():
    # a ground front 200 s away loses to an upper bin at 0 s for a light unit (0.4 h < 200)
    # and wins for a heavy one (0.4 h > 200)
    free = {1.0: [200.0] * 5, 1.4: [0.0] * 5}
    occ = {1.0: [200.0], 1.4: [0.0]}
    light = churn.frontier_shares(free, occ, {0: 1}, 0.0, [100.0], 1, steps=1)
    heavy = churn.frontier_shares(free, occ, {0: 1}, 0.0, [1000.0], 1, steps=1)
    assert light[0][1.4] == 1.0 and heavy[0][1.0] == 1.0


def test_steady_state_and_breathing_room():
    r = churn.STEADY.evaluate({'n_b': 250, 'nu_b': 1.0, 'turn': 1000.0, 'G_free': 3000.0,
                               'm': 100.0, 's': 0.55, 'sigma_G': 0.25})
    assert r['s_star'] == pytest.approx(0.25)
    assert r['B'] == pytest.approx(3000.0 / (100.0 * 0.30))


# ── the rearrangement bound ──────────────────────────────────────────────────────────────────

def test_rearrangement_bounds_and_the_random_spread_by_enumeration():
    w = [5.0, 0.0, 2.0, 9.0, 1.0]
    M = [1.0, 1.4, 1.2, 1.2, 1.0]
    costs = [sum(a * M[p] for a, p in zip(w, perm)) for perm in itertools.permutations(range(5))]
    act, cmin, cmax, sd = churn.rearrangement(w, M)
    assert act == pytest.approx(sum(a * b for a, b in zip(w, M)))
    assert cmin == pytest.approx(min(costs)) and cmax == pytest.approx(max(costs))
    assert sd == pytest.approx(statistics.pstdev(costs))
    assert churn.ORDER_SD.expr.evaluate({'w': w, 'M': M}) == pytest.approx(sd)


def test_gap_chain_arithmetic_and_rendering():
    env = {'phi': 0.086, 'U': 258_930, 'hbar': 59.0, 'M_rank': 1.162, 'M_free': 1.263,
           'T': 26_845_232}
    r = churn.GAP_CHAIN.evaluate(env)
    assert r['gap'] == pytest.approx(0.086 * 258_930 * 59.0 * (1.162 - 1.263) / 26_845_232)
    md = churn.GAP_CHAIN.to_markdown(r)
    assert r'\Delta T / T' in md and r'\bar M_{\mathrm{rank}}' in md


def test_put_gain_scales_each_share_by_its_ratio():
    r = churn.PUT_GAIN_MODEL.evaluate({'s_loc': 0.84, 's_trav': 0.16, 'M_rank': 1.154,
                                       'M_free': 1.260, 'T_rank': 9.4, 'T_free': 16.2})
    assert r['put_gain'] == pytest.approx(0.84 * (1.154 / 1.260 - 1) + 0.16 * (9.4 / 16.2 - 1))
    same = churn.PUT_GAIN_MODEL.evaluate({'s_loc': 0.5, 's_trav': 0.5, 'M_rank': 1.2,
                                          'M_free': 1.2, 'T_rank': 3.0, 'T_free': 3.0})
    assert same['put_gain'] == 0.0
