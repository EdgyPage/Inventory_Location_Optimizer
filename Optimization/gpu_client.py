"""gpu_client.py — worker-side GPU offload shim + the shared tiled cost-matrix kernel.

The placement drain scores, for a wave of U queued units over C candidate bins of one BinKey,
the static marginal cost  cost[u,b] = v[u]*M[b] + (M[b]*intercept + D[b])  and takes argmin over
bins per unit.  This module owns:

  * `estimate_bytes(U, C, tile)` — the allocation calculator (admission + startup ceiling).
  * `cpu_argmin(...)`           — the numpy CPU reference / fallback (tiled; bounded memory).
  * `GpuClient` / `placement_argmin(...)` — offload to the broker; returns None ⇒ caller does CPU.

`placement_argmin` NEVER raises and NEVER blocks indefinitely: a missing/over-budget/timed-out/
errored broker yields None so the worker silently runs the CPU path — an unattended run can't
crash on GPU trouble.  The broker (gpu_broker.py) reuses the same tiling math on the GPU.
"""
from __future__ import annotations

import numpy as np

# Tiling chunk over candidate bins: peak working set is U*tile*itemsize, so peak VRAM is bounded
# by the tile, NOT by C.  This is what lets one bounded budget admit many concurrent waves and
# makes even a 1.66M-bin tier safe.  Overridable per call / by the broker.
DEFAULT_TILE_BINS = 32_768
_ITEMSIZE = 8           # float64
_TEMP_FACTOR = 3.0      # broadcast (U x tile) + argmin temps headroom


def estimate_bytes(u: int, c: int, tile: int = DEFAULT_TILE_BINS) -> int:
    """Worst-case device bytes for a tiled argmin of a U x C cost matrix.
    Peak working set ~ U*min(C,tile) (the live chunk + temps) + the C-sized D/M/base vectors."""
    chunk = u * min(c, tile)
    return int((_TEMP_FACTOR * chunk + 3 * c + 2 * u) * _ITEMSIZE)


def _argmin_tiled(v, D, M, intercept, tile, xp, argmin, full, arange, where):
    """Backend-generic tiled argmin (numpy or torch via the passed callables).
    cost[u,b] = v[u]*M[b] + M[b]*intercept + D[b];  returns int idx[U] of the min bin per unit."""
    u = v.shape[0]
    c = D.shape[0]
    base = M * intercept + D                                   # (C,)
    best_val = full((u,), float('inf'))
    best_idx = full((u,), 0)
    rows = arange(u)
    s = 0
    while s < c:
        e = min(s + tile, c)
        chunk = v.reshape(u, 1) * M[s:e].reshape(1, e - s) + base[s:e].reshape(1, e - s)
        cidx = argmin(chunk, 1)                                # (U,) local idx in [0, e-s)
        cval = chunk[rows, cidx]
        upd = cval < best_val
        best_idx = where(upd, cidx + s, best_idx)
        best_val = where(upd, cval, best_val)
        s = e
    return best_idx


def cpu_argmin(v, D, M, intercept, tile: int = DEFAULT_TILE_BINS):
    """Numpy CPU reference / fallback.  v,D,M are 1-D float64 arrays; returns int64 idx[U]."""
    v = np.ascontiguousarray(v, np.float64)
    D = np.ascontiguousarray(D, np.float64)
    M = np.ascontiguousarray(M, np.float64)
    return _argmin_tiled(
        v, D, M, float(intercept), tile, np,
        argmin=lambda a, ax: a.argmin(axis=ax),
        full=lambda shape, fv: np.full(shape, fv, np.float64 if fv != 0 else np.int64),
        arange=lambda n: np.arange(n),
        where=np.where,
    ).astype(np.int64)


# ── client shim (set once per worker by the pool initializer) ───────────────────
_CLIENT = None    # module-global GpuClient or None (CPU-only)


class GpuClient:
    """Talks to the GPU broker over Manager queues.  Constructed in the pool initializer;
    `placement_argmin` returns None on any trouble so the caller falls back to CPU."""

    def __init__(self, request_q, response_q, budget_bytes: int, timeout: float = 5.0):
        self._req = request_q
        self._resp = response_q
        self.budget = int(budget_bytes)
        self.timeout = timeout
        self._n = 0

    def placement_argmin(self, v, D, M, intercept, tile: int = DEFAULT_TILE_BINS):
        """Offload one wave's argmin to the broker.  Returns int64 idx[U] or None (do CPU)."""
        try:
            est = estimate_bytes(len(v), len(D), tile)
            if est > self.budget:                  # single job can't fit the whole budget → CPU
                return None
            self._n += 1
            rid = self._n
            self._req.put((rid, self._resp,
                           np.ascontiguousarray(v, np.float64),
                           np.ascontiguousarray(D, np.float64),
                           np.ascontiguousarray(M, np.float64),
                           float(intercept), int(tile), est))
            got_rid, idx = self._resp.get(timeout=self.timeout)
            if got_rid != rid or idx is None:
                return None
            return idx
        except Exception:
            return None                            # broker gone / timeout / serialization → CPU


def set_client(client):
    global _CLIENT
    _CLIENT = client


def get_client():
    return _CLIENT


def placement_argmin(v, D, M, intercept, tile: int = DEFAULT_TILE_BINS):
    """Module-level convenience: GPU via the active client, else CPU.  Always returns idx[U]."""
    c = _CLIENT
    if c is not None:
        idx = c.placement_argmin(v, D, M, intercept, tile)
        if idx is not None:
            return idx
    return cpu_argmin(v, D, M, intercept, tile)
