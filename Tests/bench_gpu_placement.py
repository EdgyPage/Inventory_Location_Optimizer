"""bench_gpu_placement.py - CPU vs GPU for the placement COST MATRIX, the parallel part of the
reorder drain (`_stock`), which dominates wall late-run (~110-130s/10 batches).

For a wave of U queued units sharing a BinKey over C candidate bins, the static per-(unit,bin)
marginal cost is
    cost[u,b] = M[b]·(intercept + v[u]) + D[b]
              = v[u]·M[b]  +  (M[b]·intercept + D[b])
(v = per-unit handle_var; D,M = per-bin travel + golden-zone height - geometry, static).  The
greedy placement (argmin per unit, then consume the bin + update aisle/affinity state) stays
sequential on CPU; we benchmark the matrix build + per-unit argmin (the vectorizable part) and
assert the chosen bins match.

Fixed seeds.  Run: python Tests/bench_gpu_placement.py
"""
from __future__ import annotations

import numpy as np

import bench_gpu_common as B
from bench_gpu_common import cp, torch, median_ms, record, print_table, equiv

_SEED = 1337
_INTERCEPT = 15.0
# (U queued units, C candidate bins).  Real warehouse ~1.66M bins; a tier's free-bin pool
# (the candidate set C) reaches tens-to-hundreds of thousands at large scale - GPU advantage,
# if any, shows where the U x C cost matrix is big enough to amortize launch/transfer.
_SIZES = [(500, 5000), (2000, 20000), (5000, 50000), (2000, 200000)]  # largest U*C ~3.2GB


def _data(u, c):
    rng = np.random.default_rng(_SEED)
    v = rng.uniform(0.5, 6.0, u)                          # per-unit handle_var
    D = rng.uniform(0.0, 400.0, c)                        # per-bin travel cost (s)
    M = rng.choice([1.0, 1.2, 1.4], c).astype(np.float64)  # height multiplier
    return v, D, M


def _argmin_np(v, D, M):
    base = M * _INTERCEPT + D                             # (C,)
    cost = v[:, None] * M[None, :] + base[None, :]        # (U, C)
    return cost.argmin(axis=1)


def run():
    rows = []
    for u, c in _SIZES:
        v, D, M = _data(u, c)
        ref = _argmin_np(v, D, M)
        cpu = median_ms(lambda: _argmin_np(v, D, M), reps=10)

        cupy_ms = eq_cupy = None
        if cp is not None:
            gv, gD, gM = cp.asarray(v), cp.asarray(D), cp.asarray(M)

            def f_cp():
                base = gM * _INTERCEPT + gD
                return (gv[:, None] * gM[None, :] + base[None, :]).argmin(axis=1)
            out, st = B.bench(f_cp, sync=B.sync_cupy, reps=10)
            cupy_ms, eq_cupy = st['median_ms'], equiv(ref, out)

        torch_ms = eq_torch = None
        if torch is not None:
            tv = torch.as_tensor(v, device='cuda')
            tD = torch.as_tensor(D, device='cuda')
            tM = torch.as_tensor(M, device='cuda')

            def f_t():
                base = tM * _INTERCEPT + tD
                return (tv[:, None] * tM[None, :] + base[None, :]).argmin(dim=1)
            out, st = B.bench(f_t, sync=B.sync_torch, reps=10)
            torch_ms, eq_torch = st['median_ms'], equiv(ref, out)

        eqs = [e for e in (eq_cupy, eq_torch) if e is not None]
        record(rows, 'placement:cost-matrix+argmin', f'{u}x{c}', cpu, cupy_ms, torch_ms,
               (all(eqs) if eqs else None), 'greedy select + affinity stay sequential (CPU)')
    return rows


if __name__ == '__main__':
    print('Placement cost-matrix benchmark -', B.describe_backends())
    print_table(run(), title='placement drain cost matrix (reord hotspot, parallel part)')
