"""The dock and aisle-ceiling gates (`Optimization/simconfig/models/dock.py`): the arithmetic
against direct computation.  The grid verification lives in the study (S04, S12)."""
from __future__ import annotations

import math
import random
import statistics

import pytest

from Optimization.simconfig.models import dock


def test_door_utilisation_matches_the_saturated_grid_point():
    # k30_c95: ~28 trailers/day late in the window, 1.15 h occupancy, 4 doors x 8 h
    r = dock.DOCK.evaluate({'lam_T': 28.0, 'W_T': 1.15 * 3600 * 10 / 1.08, 'team': 10,
                            'overhead': 0.08, 'doors': 4, 'S': 28_800})
    assert r['occ'] == pytest.approx(1.15 * 3600)
    assert r['rho_door'] == pytest.approx(28.0 * 1.15 / 32.0)
    assert r['rho_door'] > 1.0


def test_erlang_c_limits():
    assert dock.erlang_c(1, 0.5) == pytest.approx(0.5)          # M/M/1: P(wait) = rho
    assert dock.erlang_c(4, 4.0) == 1.0
    assert 0.0 < dock.erlang_c(4, 2.0) < dock.erlang_c(4, 3.5) < 1.0


def test_yard_wait_matches_an_mm1_simulation():
    # c = 1, exponential arrivals and service: Allen-Cunneen is exact (Wq = rho / (mu - lam))
    lam, occ, S = 5.0, 3_600.0, 28_800.0          # mu = 8/day, rho = 0.625
    rng = random.Random(5)
    t = free = 0.0
    waits = []
    for _ in range(200_000):
        t += rng.expovariate(lam / S)
        start = max(t, free)
        waits.append(start - t)
        free = start + rng.expovariate(1.0 / occ)
    assert statistics.mean(waits) == pytest.approx(
        dock.yard_wait(lam, occ, 1, S, 1.0, 1.0), rel=0.03)
    assert dock.yard_wait(32.0, 3_600.0, 4, S) == math.inf


def test_aisle_ceiling_and_overflow():
    loads = [2_251.0, 1_000.0, 500.0]
    r = dock.AISLE.evaluate({'S': 28_800.0, 'W_max': max(loads)})
    assert r['k_star'] == pytest.approx(28_800 / 2_251)
    assert dock.overflow(loads, 10, 28_800) == 0.0
    k = 20
    assert dock.overflow(loads, k, 28_800) == pytest.approx(
        (k * 2_251 - 28_800) / (k * sum(loads)))
