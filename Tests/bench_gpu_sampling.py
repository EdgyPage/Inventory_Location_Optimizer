"""bench_gpu_sampling.py - CPU vs GPU for the batch sampler (_lift_weighted_sample), the
single biggest sim hotspot (~40-66s/10 batches early-run).

The production draw is sequential & without-replacement with DYNAMIC per-step weights
(partner-lift updates), so it needs a per-draw host sync.  We measure:
  A. full CPU `_lift_weighted_sample` (the thing a port would replace).
  B. the per-draw INNER op (multiply+cumsum+searchsorted over n) CPU vs CuPy vs Torch -
     isolates whether the heavy op is faster on GPU vs launch/sync overhead.
  C. a FAITHFUL CuPy per-step port (same host uniforms + partner map) - realistic naive port,
     equivalence-checked by selected-set overlap (~1.0 expected).
  D. one-shot static-weight Gumbel-top-k (CuPy + Torch) - the speed CEILING, but NOT equivalent
     (drops the dynamic partner lift); reported with its distributional overlap vs CPU.

Fixed seeds throughout.  Run: python Tests/bench_gpu_sampling.py
"""
from __future__ import annotations

import os
import random

import numpy as np

import bench_gpu_common as B
from bench_gpu_common import cp, torch, bench, record, print_table, set_overlap

from Workload_Builder import _lift_weighted_sample, _get_partner_map
from Affinity_Store import AffinityStore
from generation.generate_inventory import load_inventory_from_db

_SEED = 1337
_MEAN_FRAC = 0.20
_MAX_SKUS = int(os.environ.get('BENCH_MAX_SKUS', '0')) or None   # 0/unset -> full inventory


def _load():
    """Real inventory + affinity for realistic weights/partner map; synthetic fallback."""
    import run_simulation as rs
    pairs = rs.find_latest_db_pairs(rs._DEFAULT_PROFILES_DIR)
    if pairs:
        _label, inv_db, aff_db = pairs[0]
        inv = load_inventory_from_db(inv_db, limit=_MAX_SKUS)
        aff = AffinityStore(aff_db)
        return inv.orders, aff
    raise SystemExit('no inventory/affinity profile found under PROFILE_INPUT_DIR')


def run(reps_full=2, reps_inner=20):
    rows: list[dict] = []
    orders, aff = _load()
    n = len(orders)
    k = max(1, min(n, round(_MEAN_FRAC * n)))
    partner_map = _get_partner_map(aff)
    base_w = np.fromiter((c.demand.relative_frequency for c in orders), np.float64, n)
    print(f'  inventory N={n:,}  draws k={k:,}  affinity partners~'
          f'{sum(len(v) for v in partner_map.values()) // max(1, len(partner_map))}/sku')

    # ── A. full CPU sampler (baseline) ──────────────────────────────────────────
    cpu_full = B.median_ms(lambda: _lift_weighted_sample(orders, k, aff, random.Random(_SEED)),
                           warmup=1, reps=reps_full)
    ref_sel = [c.sku for c in _lift_weighted_sample(orders, k, aff, random.Random(_SEED))]

    # ── B. per-draw inner op over n: w=base*lift; cumsum; searchsorted(u) ────────
    lift_np = np.ones(n, np.float64)
    u = base_w.sum() * 0.5

    def inner_np():
        w = base_w * lift_np
        c = np.cumsum(w)
        return int(np.searchsorted(c, u))
    inner_cpu = B.median_ms(inner_np, reps=reps_inner)

    inner_cupy = inner_torch = None
    if cp is not None:
        g_base, g_lift = cp.asarray(base_w), cp.asarray(lift_np)

        def inner_cp():
            w = g_base * g_lift
            c = cp.cumsum(w)
            return int(cp.searchsorted(c, u))
        inner_cupy = B.median_ms(inner_cp, sync=B.sync_cupy, reps=reps_inner)
    if torch is not None:
        t_base = torch.as_tensor(base_w, device='cuda')
        t_lift = torch.ones(n, dtype=torch.float64, device='cuda')

        def inner_t():
            w = t_base * t_lift
            c = torch.cumsum(w, 0)
            return int(torch.searchsorted(c, torch.tensor(u, dtype=torch.float64, device='cuda')))
        inner_torch = B.median_ms(inner_t, sync=B.sync_torch, reps=reps_inner)
    record(rows, 'sampling:inner_op (1 draw)', n, inner_cpu, inner_cupy, inner_torch, True,
           'per-draw heavy op; x'+str(k)+' draws/batch')

    # ── C. faithful CuPy per-step port (same uniforms + partner map) ────────────
    cupy_full = None
    if cp is not None:
        sku_to_idx = {c.sku: i for i, c in enumerate(orders)}
        # precompute partner (idx, lift) arrays per sku for batched GPU scatter
        pidx = {s: (np.array([sku_to_idx[p] for p, _ in lst if p in sku_to_idx], np.int64),
                    np.array([lv for p, lv in lst if p in sku_to_idx], np.float64))
                for s, lst in partner_map.items()}

        def cupy_sample():
            g_base = cp.asarray(base_w)
            lift = cp.ones(n, dtype=cp.float64)
            active = cp.ones(n, dtype=cp.bool_)
            r = random.Random(_SEED)
            sel = []
            for _ in range(k):
                w = cp.where(active, g_base * lift, 0.0)
                total = float(w.sum())                      # sync
                if total <= 0.0:
                    break
                cumw = cp.cumsum(w)
                idx = int(cp.searchsorted(cumw, r.uniform(0.0, total)))   # sync
                if idx >= n:
                    idx = n - 1
                sel.append(orders[idx].sku)
                active[idx] = False
                pa = pidx.get(orders[idx].sku)
                if pa is not None and pa[0].size:
                    lift[cp.asarray(pa[0])] *= cp.asarray(pa[1])
            return sel
        sel_gpu, st = bench(cupy_sample, sync=B.sync_cupy, warmup=1, reps=reps_full)
        cupy_full = st['median_ms']
        ov = set_overlap(ref_sel, sel_gpu)
        record(rows, 'sampling:full faithful port', f'{n}x{k}', cpu_full, cupy_full, None,
               ov > 0.98, f'selected-set overlap={ov:.3f} (per-draw host sync)')
    else:
        record(rows, 'sampling:full faithful port', f'{n}x{k}', cpu_full, None, None, None,
               'cupy unavailable')

    # ── D. one-shot static-weight Gumbel-top-k (ceiling; NOT equivalent) ────────
    def gumbel_np():
        rng = np.random.default_rng(_SEED)
        g = -np.log(-np.log(rng.random(n)))
        keys = np.log(np.maximum(base_w, 1e-300)) + g
        return np.argpartition(keys, n - k)[n - k:]
    gk_cpu = B.median_ms(gumbel_np, reps=reps_inner)
    gk_cupy = gk_torch = None
    sel_static = [orders[i].sku for i in gumbel_np()]
    if cp is not None:
        g_base = cp.asarray(base_w)

        def gumbel_cp():
            rs_ = cp.random.RandomState(_SEED)
            g = -cp.log(-cp.log(rs_.random_sample(n)))
            keys = cp.log(cp.maximum(g_base, 1e-300)) + g
            return cp.argpartition(keys, n - k)[n - k:]
        gk_cupy = B.median_ms(gumbel_cp, sync=B.sync_cupy, reps=reps_inner)
    if torch is not None:
        t_base = torch.as_tensor(base_w, device='cuda')

        def gumbel_t():
            gen = torch.Generator(device='cuda').manual_seed(_SEED)
            g = -torch.log(-torch.log(torch.rand(n, dtype=torch.float64, device='cuda', generator=gen)))
            keys = torch.log(torch.clamp(t_base, min=1e-300)) + g
            return torch.topk(keys, k).indices
        gk_torch = B.median_ms(gumbel_t, sync=B.sync_torch, reps=reps_inner)
    ov_static = set_overlap(ref_sel, sel_static)
    record(rows, 'sampling:gumbel-topk (static)', f'{n}x{k}', gk_cpu, gk_cupy, gk_torch, None,
           f'NOT equiv: drops dynamic lift; overlap vs CPU={ov_static:.3f}')
    return rows


if __name__ == '__main__':
    print('Sampling benchmark -', B.describe_backends())
    print_table(run(), title='_lift_weighted_sample (batch build hotspot)')
