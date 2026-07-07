"""gpu_auction.py — parallel (Jacobi) auction solver for the placement linear-assignment problem.

PROTOTYPE (no production wiring).  Models one placement wave as a linear assignment of U units to
B>=U bins minimising total cost, and solves it with Bertsekas' auction algorithm in parallel form:
all unassigned units bid at once for their best bin; a contested bin keeps the highest bidder and
RAISES ITS PRICE; displaced units re-bid against the new prices.  Prices only ever rise and the total
rise is bounded, so it terminates — resolving bin clashes WITHOUT the eviction cascade a naive
parallel swap would create.  The bid step is a row-wise top-2 over the U x B cost matrix, which is the
GPU-parallel reduction we want.

What's here:
  * build_static_cost(...)  — the exact bilinear placement cost `f_s*(M_b*(intercept+v_s) + D_b)`
    (same form as inventory_optimal.py's scipy LAP); a pure U x B matrix.
  * auction_assign(...)     — numpy + torch backends, tiled top-2 (peak memory bounded by tile, not
    B), eps-scaling, deterministic index tie-breaks.  Returns assign[U] (bin per unit) + prices.
  * greedy_assign(...)      — sequential argmin-and-consume baseline (the structure the production
    greedy uses) for a quality reference.
  * auction_place_wave(...) — folds the path-dependent AFFINITY reward in via a few-round fixed point
    (solve -> update aisle memberships -> re-solve), the linear relaxation of the quadratic objective.

The auction MINIMISES total cost optimally (within U*eps); it is a *replacement* for the sequential
greedy (re-baselined), not a bit-for-bit mimic.
"""
from __future__ import annotations

import numpy as np

_TILE_BINS = 32_768          # column-tile for the per-row top-2 (bounds peak memory by tile, not B)


# ── cost + references ────────────────────────────────────────────────────────────
def build_static_cost(f, v, D, M, intercept):
    """Static per-(unit,bin) placement cost matrix, shape (U, B):
        cost[u,b] = f[u] * ( M[b]*(intercept + v[u]) + D[b] )
    f,v are per-unit (U,); D,M are per-bin (B,).  Mirrors Assignment_Functions._ranked_minlabor_impl
    / inventory_optimal._optimal_work_assign (minus the path-dependent affinity terms)."""
    f = np.ascontiguousarray(f, np.float64); v = np.ascontiguousarray(v, np.float64)
    D = np.ascontiguousarray(D, np.float64); M = np.ascontiguousarray(M, np.float64)
    return f[:, None] * (M[None, :] * (intercept + v[:, None]) + D[None, :])


def assignment_cost(cost, assign):
    """Total cost of an assignment (assign[u] = bin index, or -1 for unassigned)."""
    a = np.asarray(assign)
    rows = np.flatnonzero(a >= 0)
    return float(cost[rows, a[rows]].sum())


def greedy_assign(cost, order=None):
    """Sequential argmin-and-consume baseline (the production greedy's structure on a static cost):
    process units in `order` (default: row index), each takes its lowest-cost still-free bin."""
    U, B = cost.shape
    order = range(U) if order is None else order
    free = np.ones(B, dtype=bool)
    assign = np.full(U, -1, dtype=np.int64)
    for u in order:
        row = cost[u].copy()
        row[~free] = np.inf
        b = int(np.argmin(row))
        if np.isfinite(row[b]):
            assign[u] = b
            free[b] = False
    return assign


# ── numpy auction ────────────────────────────────────────────────────────────────
def _top2_numpy(benefit, un, price, tile):
    """Per-row (rows = un) running top-2 of (benefit[un] - price) over B columns, in tiles.
    Returns (best_idx[n], best_val[n], second_val[n])."""
    n = un.size
    B = benefit.shape[1]
    best1 = np.full(n, -np.inf); idx1 = np.zeros(n, np.int64); best2 = np.full(n, -np.inf)
    r = np.arange(n)
    s = 0
    while s < B:
        e = min(s + tile, B)
        v = benefit[un, s:e] - price[None, s:e]          # (n, w)
        ti = np.argmax(v, axis=1)
        t1 = v[r, ti]
        v[r, ti] = -np.inf
        t2 = v.max(axis=1) if (e - s) > 1 else np.full(n, -np.inf)
        take = t1 > best1
        best2 = np.where(take, np.maximum(best1, t2), np.maximum(best2, t1))
        idx1 = np.where(take, ti + s, idx1)
        best1 = np.where(take, t1, best1)
        s = e
    return idx1, best1, best2


def _auction_phase_numpy(benefit, price, assign, owner, eps, tile, max_rounds):
    U, B = benefit.shape
    for _ in range(max_rounds):
        un = np.flatnonzero(assign < 0)
        if un.size == 0:
            return True
        best_idx, best_val, second_val = _top2_numpy(benefit, un, price, tile)
        bids = price[best_idx] + (best_val - second_val) + eps
        maxbid = np.full(B, -np.inf)
        np.maximum.at(maxbid, best_idx, bids)
        ismax = bids >= maxbid[best_idx]                 # deterministic equality (identical compute)
        win = np.full(B, U, dtype=np.int64)              # min unit index among top bidders per bin
        np.minimum.at(win, best_idx[ismax], un[ismax])
        bins_bid = np.flatnonzero(win < U)
        winners = win[bins_bid]
        prev = owner[bins_bid]
        assign[prev[prev >= 0]] = -1                     # displace previous owners (disjoint from winners)
        price[bins_bid] = maxbid[bins_bid]
        owner[bins_bid] = winners
        assign[winners] = bins_bid
    return np.count_nonzero(assign < 0) == 0


def auction_assign_numpy(cost, eps=None, tile=_TILE_BINS, max_rounds=2_000_000):
    """Optimal (within U*eps) assignment minimising total cost via the parallel auction (single eps).

    CORRECT reference, but SLOW on realistic *structured* costs: many bins are near-tied for a unit, so
    the eps-sized price increments take ~1e5 rounds (measured ~193k rounds / 23s at U=200,B=2000).
    eps-scaling would cut rounds but a correct *rectangular* (B>U) eps-scaling is non-trivial (a coarse
    phase inflates used-bin prices, then unused price-0 bins look best and units flee to them).  This is
    the central reason the prototype's verdict is NO-GO vs scipy.linear_sum_assignment (which solves the
    identical problem optimally in ~0.04-0.09s) — see docs/gpu_auction_assessment.md."""
    cost = np.ascontiguousarray(cost, np.float64)
    U, B = cost.shape
    if B < U:
        raise ValueError(f'auction needs B>=U, got U={U} B={B}')
    benefit = -cost
    cmax = float(np.abs(cost).max()) or 1.0
    eps_final = (cmax * 1e-7) if eps is None else float(eps)
    price = np.zeros(B)
    assign = np.full(U, -1, np.int64)
    owner = np.full(B, -1, np.int64)
    _auction_phase_numpy(benefit, price, assign, owner, eps_final, tile, max_rounds)
    return assign, price


# ── torch (GPU) auction ──────────────────────────────────────────────────────────
def _top2_torch(torch, benefit, un, price, tile):
    n = int(un.shape[0]); B = benefit.shape[1]
    neg = torch.tensor(float('-inf'), device=benefit.device, dtype=benefit.dtype)
    best1 = torch.full((n,), float('-inf'), device=benefit.device, dtype=benefit.dtype)
    best2 = best1.clone()
    idx1 = torch.zeros(n, dtype=torch.long, device=benefit.device)
    r = torch.arange(n, device=benefit.device)
    rows = benefit.index_select(0, un)                   # (n, B) view-ish; sliced per tile below
    s = 0
    while s < B:
        e = min(s + tile, B)
        v = rows[:, s:e] - price[s:e].unsqueeze(0)       # (n, w)
        t1, ti = v.max(dim=1)
        if (e - s) > 1:
            v[r, ti] = neg
            t2, _ = v.max(dim=1)
        else:
            t2 = torch.full((n,), float('-inf'), device=benefit.device, dtype=benefit.dtype)
        take = t1 > best1
        best2 = torch.where(take, torch.maximum(best1, t2), torch.maximum(best2, t1))
        idx1 = torch.where(take, ti + s, idx1)
        best1 = torch.where(take, t1, best1)
        s = e
    return idx1, best1, best2


def _auction_phase_torch(torch, benefit, price, assign, owner, eps, tile, max_rounds):
    U, B = benefit.shape
    for _ in range(max_rounds):
        un = torch.nonzero(assign < 0, as_tuple=False).flatten()
        if un.numel() == 0:
            return True
        best_idx, best_val, second_val = _top2_torch(torch, benefit, un, price, tile)
        bids = price.index_select(0, best_idx) + (best_val - second_val) + eps
        maxbid = torch.full((B,), float('-inf'), device=benefit.device, dtype=benefit.dtype)
        maxbid.scatter_reduce_(0, best_idx, bids, reduce='amax', include_self=True)
        ismax = bids >= maxbid.index_select(0, best_idx)
        win = torch.full((B,), U, dtype=torch.long, device=benefit.device)
        win.scatter_reduce_(0, best_idx[ismax], un[ismax], reduce='amin', include_self=True)
        bins_bid = torch.nonzero(win < U, as_tuple=False).flatten()
        winners = win.index_select(0, bins_bid)
        prev = owner.index_select(0, bins_bid)
        pv = prev[prev >= 0]
        if pv.numel():
            assign[pv] = -1
        price[bins_bid] = maxbid.index_select(0, bins_bid)
        owner[bins_bid] = winners
        assign[winners] = bins_bid
    return bool((assign >= 0).all())


def auction_assign_torch(cost, eps=None, tile=_TILE_BINS, max_rounds=1_000_000, device='cuda'):
    import torch
    c = torch.as_tensor(np.ascontiguousarray(cost, np.float64), device=device, dtype=torch.float64)
    U, B = c.shape
    if B < U:
        raise ValueError(f'auction needs B>=U, got U={U} B={B}')
    benefit = -c
    cmax = float(c.abs().max().item()) or 1.0
    eps_final = (cmax * 1e-7) if eps is None else float(eps)
    price = torch.zeros(B, device=c.device, dtype=c.dtype)
    assign = torch.full((U,), -1, dtype=torch.long, device=c.device)
    owner = torch.full((B,), -1, dtype=torch.long, device=c.device)
    _auction_phase_torch(torch, benefit, price, assign, owner, eps_final, tile, max_rounds)
    return assign.detach().cpu().numpy().astype(np.int64), price.detach().cpu().numpy()


def auction_assign(cost, backend='auto', **kw):
    """Dispatch: 'numpy', 'torch', or 'auto' (torch+CUDA if available, else numpy)."""
    if backend == 'numpy':
        return auction_assign_numpy(cost, **kw)
    if backend == 'torch':
        return auction_assign_torch(cost, **kw)
    try:
        import torch
        if torch.cuda.is_available():
            return auction_assign_torch(cost, **kw)
    except Exception:
        pass
    return auction_assign_numpy(cost, **kw)


# ── affinity fixed-point wrapper (the quadratic part, linearised + iterated) ───────
def wave_objective(static_cost, L, lam, assign, aisle_of_bin):
    """Full wave objective to MINIMISE: total static cost minus lam * affinity reward earned by
    co-aisle partners.  L[u,p] = (lift(u,p)-1)*f_p (>=0), aisle_of_bin[b] = bin b's aisle id."""
    base = assignment_cost(static_cost, assign)
    a = np.asarray(assign)
    placed = np.flatnonzero(a >= 0)
    aisle_u = np.full(len(a), -1, np.int64)
    aisle_u[placed] = np.asarray(aisle_of_bin)[a[placed]]
    reward = 0.0
    for u in placed:                                     # reward = sum over distinct co-aisle partners
        mates = placed[(aisle_u[placed] == aisle_u[u]) & (placed != u)]
        if mates.size:
            reward += float(L[u, mates].sum())
    return base - lam * reward


def affinity_correction(L, lam, assign, aisle_of_bin, n_aisles):
    """correction[u,b] = -lam * sum_{partners p placed in bin b's aisle} L[u,p].  Built as L @ M
    where M[p,a]=1 if unit p is assigned to aisle a (a dense matmul — GPU-friendly)."""
    U = L.shape[0]
    a = np.asarray(assign)
    M = np.zeros((U, n_aisles))
    placed = np.flatnonzero(a >= 0)
    M[placed, np.asarray(aisle_of_bin)[a[placed]]] = 1.0
    member_score = lam * (L @ M)                          # (U, n_aisles)
    return -member_score[:, np.asarray(aisle_of_bin)]     # (U, B)


def auction_place_wave(static_cost, L, lam, aisle_of_bin, rounds=4, backend='numpy', **kw):
    """Fixed-point: solve static LAP, then re-solve with the affinity correction implied by the
    current assignment, a few rounds.  Returns (assign, telemetry) where telemetry lists per-round
    (changed_units, objective)."""
    n_aisles = int(np.asarray(aisle_of_bin).max()) + 1
    assign, _ = auction_assign(static_cost, backend=backend, **kw)
    tele = [(0, wave_objective(static_cost, L, lam, assign, aisle_of_bin))]
    for _ in range(rounds):
        corr = affinity_correction(L, lam, assign, aisle_of_bin, n_aisles)
        new_assign, _ = auction_assign(static_cost + corr, backend=backend, **kw)
        changed = int(np.count_nonzero(new_assign != assign))
        obj = wave_objective(static_cost, L, lam, new_assign, aisle_of_bin)
        tele.append((changed, obj))
        assign = new_assign
        if changed == 0:
            break
    return assign, tele
