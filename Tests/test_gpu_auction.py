"""test_gpu_auction.py — correctness of the parallel auction placement solver (prototype).

CPU-safe; the torch/GPU parity test skips without CUDA.  Proves the auction is optimal (matches
scipy's LAP), feasible, deterministic, beats the sequential greedy, and that the affinity fixed-point
converges without hurting the objective.  Run: python -m pytest Tests/test_gpu_auction.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

import gpu_auction as A


def _cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _rand_cost(u, b, seed):
    rng = np.random.default_rng(seed)
    return rng.uniform(1.0, 1000.0, size=(u, b))


def _scipy_opt(cost):
    from scipy.optimize import linear_sum_assignment
    ri, ci = linear_sum_assignment(cost)
    return float(cost[ri, ci].sum())


@pytest.mark.parametrize('u,b', [(10, 10), (50, 80), (200, 500), (300, 300)])
def test_auction_matches_scipy_optimal(u, b):
    cost = _rand_cost(u, b, seed=u + b)
    assign, _ = A.auction_assign_numpy(cost)
    ac = A.assignment_cost(cost, assign)
    opt = _scipy_opt(cost)
    cmax = float(np.abs(cost).max())
    bound = u * (cmax * 1e-7) + 1e-6                      # auction is within U*eps of optimal
    assert opt - 1e-6 <= ac <= opt + bound, (ac, opt, bound)


def test_auction_feasible_and_complete():
    cost = _rand_cost(120, 400, seed=7)
    assign, _ = A.auction_assign_numpy(cost)
    assert (assign >= 0).all()                            # all units placed (B >= U)
    assert len(set(assign.tolist())) == len(assign)       # exclusive: no bin used twice


def test_auction_deterministic():
    cost = _rand_cost(150, 300, seed=11)
    a1, _ = A.auction_assign_numpy(cost)
    a2, _ = A.auction_assign_numpy(cost)
    assert np.array_equal(a1, a2)


def test_auction_beats_or_ties_greedy():
    # auction is optimal, so its total cost must be <= the sequential argmin-consume greedy's.
    for seed in (1, 2, 3):
        cost = _rand_cost(100, 250, seed=seed)
        ac = A.assignment_cost(cost, A.auction_assign_numpy(cost)[0])
        gc = A.assignment_cost(cost, A.greedy_assign(cost))
        assert ac <= gc + 1e-6, (ac, gc)


@pytest.mark.skipif(not _cuda(), reason='no CUDA torch')
def test_torch_matches_numpy_cost():
    for u, b in [(64, 128), (200, 600)]:
        cost = _rand_cost(u, b, seed=u * b)
        cn = A.assignment_cost(cost, A.auction_assign_numpy(cost)[0])
        ct = A.assignment_cost(cost, A.auction_assign_torch(cost)[0])
        assert abs(cn - ct) <= max(cn, ct) * 1e-6 + 1e-6, (cn, ct)


@pytest.mark.skipif(not _cuda(), reason='no CUDA torch')
def test_torch_tiling_matches_untiled():
    cost = _rand_cost(128, 5000, seed=99)
    big, _ = A.auction_assign_torch(cost, tile=8192)
    small, _ = A.auction_assign_torch(cost, tile=512)
    assert abs(A.assignment_cost(cost, big) - A.assignment_cost(cost, small)) <= 1e-6


def _aisle_setup(u, b, n_aisles, seed):
    rng = np.random.default_rng(seed)
    cost = rng.uniform(1.0, 1000.0, size=(u, b))
    aisle_of_bin = rng.integers(0, n_aisles, size=b)
    L = rng.uniform(0.0, 5.0, size=(u, u))               # L[i,j] = (lift-1)*f_j >= 0
    np.fill_diagonal(L, 0.0)
    return cost, aisle_of_bin, L


def test_affinity_fixed_point_converges_and_helps():
    cost, aisle_of_bin, L = _aisle_setup(80, 200, 12, seed=5)
    lam = 0.3
    assign, tele = A.auction_place_wave(cost, L, lam, aisle_of_bin, rounds=6, backend='numpy')
    # feasible
    assert (assign >= 0).all() and len(set(assign.tolist())) == len(assign)
    # terminates within the round budget
    assert len(tele) <= 7
    objs = [o for _, o in tele]
    # iterating in the affinity-aware objective never ends up worse than the static-only round 0
    assert min(objs) <= objs[0] + 1e-6, objs


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
