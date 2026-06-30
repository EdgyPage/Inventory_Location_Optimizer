"""bench_gpu_picksim.py - CPU vs GPU for the pick-sim per-pick compute (fast_pick), the
user's candidate #2.  NOTE: this section is only ~5% of wall (see bench_sections), so even a
big speedup here moves the whole run little - benchmarked for completeness.

Flatten a batch's P picks into arrays and compute, vectorized:
  var  = pw*ln(w) + pv*ln(v)                       (handle term; default log fns)
  hmult = step over DEFAULT_HEIGHT_BRACKETS (y_phys)
  pick_time = hmult*(intercept + var*qty) + cart_swap_coef*cart_swapped
  leg  = dx*x_pace + dy*y_pace                      (travel between consecutive bins)
  event time = cumsum(leg + pick_time)             (prefix-sum; segmented per picker in prod)
We bench the per-pick MAP (equivalence-checked) + the cumsum scan.

Fixed seeds.  Run: python Tests/bench_gpu_picksim.py
"""
from __future__ import annotations

import numpy as np

import bench_gpu_common as B
from bench_gpu_common import cp, torch, median_ms, record, print_table, equiv
from cost_model import DEFAULT_HEIGHT_BRACKETS, sec_per_inch

_SEED = 1337
_INTERCEPT, _PW, _PV, _CART = 15.0, 0.58, 0.7, 300.0
_XP, _YP = sec_per_inch(3.0), sec_per_inch(2.0)
_THR = np.array([t for t, _ in DEFAULT_HEIGHT_BRACKETS[:-1]], np.float64)   # [96, 240]
_MUL = np.array([m for _, m in DEFAULT_HEIGHT_BRACKETS], np.float64)        # [1.0,1.2,1.4]
# picks per batch - scales with warehouse/batch size; large tier shows the GPU crossover.
_SIZES = [20_000, 100_000, 1_000_000]


def _data(p):
    rng = np.random.default_rng(_SEED)
    return dict(w=rng.integers(1, 200, p).astype(np.float64),
                v=rng.integers(1, 110_000, p).astype(np.float64),
                q=rng.integers(1, 20, p).astype(np.float64),
                y=rng.uniform(0, 360, p),
                dx=rng.uniform(0, 200, p), dy=rng.uniform(0, 48, p),
                cart=rng.integers(0, 2, p).astype(np.float64))


def _map_np(d):
    var = _PW * np.log(np.maximum(d['w'], 1.0)) + _PV * np.log(np.maximum(d['v'], 1.0))
    hmult = _MUL[np.searchsorted(_THR, d['y'], side='right')]
    pick = hmult * (_INTERCEPT + var * d['q']) + _CART * d['cart']
    return pick + d['dx'] * _XP + d['dy'] * _YP


def run():
    rows = []
    for p in _SIZES:
        d = _data(p)
        ref = _map_np(d)
        cpu = median_ms(lambda: np.cumsum(_map_np(d)), reps=10)

        cupy_ms = eq_cupy = None
        if cp is not None:
            g = {k: cp.asarray(val) for k, val in d.items()}
            thr, mul = cp.asarray(_THR), cp.asarray(_MUL)

            def f_cp():
                var = _PW * cp.log(cp.maximum(g['w'], 1.0)) + _PV * cp.log(cp.maximum(g['v'], 1.0))
                hmult = mul[cp.searchsorted(thr, g['y'], side='right')]
                pick = hmult * (_INTERCEPT + var * g['q']) + _CART * g['cart']
                return cp.cumsum(pick + g['dx'] * _XP + g['dy'] * _YP)
            out, st = B.bench(f_cp, sync=B.sync_cupy, reps=10)
            cupy_ms, eq_cupy = st['median_ms'], equiv(np.cumsum(ref), out)

        torch_ms = eq_torch = None
        if torch is not None:
            g = {k: torch.as_tensor(val, device='cuda') for k, val in d.items()}
            thr = torch.as_tensor(_THR, device='cuda'); mul = torch.as_tensor(_MUL, device='cuda')

            def f_t():
                var = _PW * torch.log(torch.clamp(g['w'], min=1.0)) + _PV * torch.log(torch.clamp(g['v'], min=1.0))
                hmult = mul[torch.bucketize(g['y'], thr, right=True)]
                pick = hmult * (_INTERCEPT + var * g['q']) + _CART * g['cart']
                return torch.cumsum(pick + g['dx'] * _XP + g['dy'] * _YP, 0)
            out, st = B.bench(f_t, sync=B.sync_torch, reps=10)
            torch_ms, eq_torch = st['median_ms'], equiv(np.cumsum(ref), out)

        eqs = [e for e in (eq_cupy, eq_torch) if e is not None]
        record(rows, 'picksim:per-pick map+cumsum', p, cpu, cupy_ms, torch_ms,
               (all(eqs) if eqs else None), 'section is ~5% of wall (low Amdahl ceiling)')
    return rows


if __name__ == '__main__':
    print('Pick-sim benchmark -', B.describe_backends())
    print_table(run(), title='fast_pick per-pick compute (low-priority, ~5% of wall)')
