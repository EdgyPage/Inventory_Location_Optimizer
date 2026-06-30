"""test_gpu_governor.py — the GPU broker/governor's correctness + crash-safety contract.

CPU-safe: the tiled-argmin equivalence and the fallback contract run without a GPU; the GPU/broker
roundtrip skips when CUDA is absent.  Run: python -m pytest Tests/test_gpu_governor.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

import gpu_client as C


def _cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _rand(u, c, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.uniform(0.5, 6.0, u), rng.uniform(0.0, 400.0, c),
            rng.choice([1.0, 1.2, 1.4], c).astype(np.float64))


def _brute(v, D, M, intercept):
    cost = v[:, None] * M[None, :] + (M * intercept + D)[None, :]
    return cost.argmin(axis=1)


def test_estimate_bytes_tile_bounded_and_monotonic():
    # peak bounded by tile: doubling C beyond the tile barely grows the estimate
    e1 = C.estimate_bytes(2000, 50_000, tile=10_000)
    e2 = C.estimate_bytes(2000, 200_000, tile=10_000)
    assert e2 < 1.5 * e1                      # grows only via the small C-sized vectors
    # monotonic in U
    assert C.estimate_bytes(4000, 50_000, 10_000) > C.estimate_bytes(2000, 50_000, 10_000)


def test_cpu_argmin_matches_bruteforce_with_tiling():
    v, D, M = _rand(300, 5000, seed=1)
    for tile in (5000, 1000, 137):            # tile == C, < C, and an awkward size
        idx = C.cpu_argmin(v, D, M, 15.0, tile=tile)
        assert np.array_equal(idx, _brute(v, D, M, 15.0))


def test_placement_argmin_no_client_falls_back_to_cpu():
    C.set_client(None)
    v, D, M = _rand(100, 2000, seed=2)
    idx = C.placement_argmin(v, D, M, 15.0)
    assert np.array_equal(idx, _brute(v, D, M, 15.0))


def test_client_over_budget_returns_none():
    # budget smaller than the job estimate => placement_argmin returns None (caller does CPU),
    # without ever touching the queues (so None queues are fine here).
    client = C.GpuClient(request_q=None, response_q=None, budget_bytes=1)
    assert client.placement_argmin(*_rand(2000, 200_000, seed=3), intercept=15.0) is None


@pytest.mark.skipif(not _cuda_available(), reason='no CUDA torch')
def test_gpu_argmin_matches_cpu():
    import gpu_broker as Bk
    import torch
    v, D, M = _rand(500, 30_000, seed=4)
    idx = Bk.gpu_argmin_torch(torch, v, D, M, 15.0, tile=8192)
    assert np.array_equal(idx, _brute(v, D, M, 15.0))


@pytest.mark.skipif(not _cuda_available(), reason='no CUDA torch')
def test_broker_roundtrip_and_overbudget_fallback():
    import multiprocessing as mp
    import gpu_broker as Bk
    mgr = mp.Manager()
    proc, req_q, status = Bk.start_broker(mgr, vram_frac=0.4, max_inflight=4)
    assert status.get('ready'), status.get('error')
    try:
        resp = mgr.Queue()
        v, D, M = _rand(400, 20_000, seed=5)
        est = C.estimate_bytes(len(v), len(D))
        req_q.put((1, resp, v, D, M, 15.0, C.DEFAULT_TILE_BINS, est))
        rid, idx = resp.get(timeout=30)
        assert rid == 1 and np.array_equal(idx, _brute(v, D, M, 15.0))
        # deliberate over-budget request -> broker responds None (fallback), does NOT crash
        req_q.put((2, resp, v, D, M, 15.0, C.DEFAULT_TILE_BINS, status['budget'] + 10**12))
        rid2, idx2 = resp.get(timeout=30)
        assert rid2 == 2 and idx2 is None
    finally:
        Bk.stop_broker(proc, req_q)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
