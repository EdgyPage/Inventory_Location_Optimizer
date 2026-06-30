"""bench_gpu_concurrency.py — does ONE shared GPU broker keep up with many CPU workers?

Spawns P client processes that hammer the broker with placement-argmin waves at a representative
late-run size, sweeping the broker's max_inflight, and compares aggregate throughput (waves/s) to
the CPU-only baseline (each client runs the numpy cpu_argmin).  Reports peak VRAM and fallback
counts (must be 0 under budget; a deliberate over-budget probe must fall back, not crash).

Answers the real question: is a 20-worker speedup realized through a single bounded GPU, and what
max_inflight maximizes it.  Run: python Tests/bench_gpu_concurrency.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

import gpu_client as C  # noqa: E402

_SEED = 1337
_U, _CBINS, _ITERS = 1500, 60_000, 40       # late-run-ish wave; iters per client
_P_CLIENTS = 16                             # concurrent CPU workers hammering the GPU
_INFLIGHT_SWEEP = [1, 2, 4, 8]


def _client(args):
    """One client process: run _ITERS placement waves, GPU-via-broker or CPU, return (s, fallbacks)."""
    req_q, resp_q, budget, U, c, iters, seed = args
    rng = np.random.default_rng(seed)
    v = rng.uniform(0.5, 6.0, U)
    D = rng.uniform(0.0, 400.0, c)
    M = rng.choice([1.0, 1.2, 1.4], c).astype(np.float64)
    client = C.GpuClient(req_q, resp_q, budget) if req_q is not None else None
    fb = 0
    t0 = time.perf_counter()
    for _ in range(iters):
        idx = client.placement_argmin(v, D, M, 15.0) if client is not None else None
        if idx is None:
            C.cpu_argmin(v, D, M, 15.0)      # CPU baseline, or GPU-fallback path
            if client is not None:
                fb += 1
    return time.perf_counter() - t0, fb


def _run_pool(mp, make_args, p):
    with mp.Pool(p) as pool:
        return pool.map(_client, [make_args(i) for i in range(p)])


def main():
    import multiprocessing as mp
    import bench_gpu_common as B
    spawn = mp.get_context('spawn')
    print('GPU concurrency benchmark -', B.describe_backends())
    if not B.backends()['torch']:
        print('  no CUDA torch -> skipping (CPU-only box)')
        return
    total = _P_CLIENTS * _ITERS

    # ── CPU baseline (P workers, numpy) ──────────────────────────────────────────
    res = _run_pool(spawn, lambda i: (None, None, 0, _U, _CBINS, _ITERS, _SEED + i), _P_CLIENTS)
    cpu_wall = max(s for s, _ in res)
    cpu_thr = total / cpu_wall
    print(f'\nCPU baseline: {_P_CLIENTS} workers x {_ITERS} waves ({_U}x{_CBINS})  '
          f'wall={cpu_wall:.2f}s  throughput={cpu_thr:.0f} waves/s')

    # ── GPU via broker, sweep max_inflight ──────────────────────────────────────
    import gpu_broker as Bk
    print(f'\n{"inflight":>8} {"wall s":>8} {"waves/s":>9} {"vs CPU":>7} {"fallbacks":>10} {"peakVRAM MB":>12}')
    best = (0.0, None)
    for inflight in _INFLIGHT_SWEEP:
        mgr = spawn.Manager()
        proc, req_q, status = Bk.start_broker(mgr, vram_frac=0.6, max_inflight=inflight)
        if not status.get('ready'):
            print(f'  broker failed: {status.get("error")}')
            continue
        budget = status['budget']
        resp_qs = [mgr.Queue() for _ in range(_P_CLIENTS)]
        res = _run_pool(spawn, lambda i: (req_q, resp_qs[i], budget, _U, _CBINS, _ITERS, _SEED + i),
                        _P_CLIENTS)
        wall = max(s for s, _ in res)
        fb = sum(f for _, f in res)
        Bk.stop_broker(proc, req_q)
        time.sleep(0.5)
        peak = status.get('peak_bytes', 0) / 1e6
        thr = total / wall
        print(f'{inflight:>8} {wall:>8.2f} {thr:>9.0f} {thr / cpu_thr:>6.2f}x {fb:>10} {peak:>12.0f}')
        if thr > best[0]:
            best = (thr, inflight)
        mgr.shutdown()

    if best[1] is not None:
        print(f'\nBest: max_inflight={best[1]}  ->  {best[0] / cpu_thr:.2f}x vs CPU-only '
              f'({_P_CLIENTS} workers).  (fallbacks must be 0; over-budget probe covered in '
              f'test_gpu_governor.)')


if __name__ == '__main__':
    main()
