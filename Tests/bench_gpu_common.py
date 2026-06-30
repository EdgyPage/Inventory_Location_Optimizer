"""bench_gpu_common.py - shared harness for the CPU-vs-GPU microbenchmarks.

Backend detection (CuPy / PyTorch), warmup + device-synced timing, conversions, and
equivalence / distributional-agreement helpers.  Import-guarded so a no-CUDA box degrades to
CPU-only (cupy/torch = None) instead of erroring.  Standalone - not collected by pytest; run
any bench_gpu_*.py directly, or Tests/bench_gpu_all.py for the full suite + CSV summary.

Each bench module exposes `run() -> list[dict]` (rows for the aggregate table) and a
`__main__` that prints its own table.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── backend detection ────────────────────────────────────────────────────────
# A GPU kernel can SEGFAULT (uncatchable) when the CUDA runtime/driver mismatch - e.g.
# cupy-cuda12x loading a CUDA-13 cudart against a 12.8 driver (cudaErrorInsufficientDriver).
# So we PROBE each backend's actual compute in a SUBPROCESS first; only import it in-process
# if the probe survives.  This keeps the suite runnable on a half-broken GPU box.
import subprocess

# Probe each backend's ACTUAL compute in a subprocess first (a GPU kernel can segfault
# uncatchably on a broken stack), so the suite never crashes mid-run.  The CuPy probe
# exercises the JIT-compiled ops the benches use (where/searchsorted/argpartition) - a
# build mismatched to the CUDA toolkit passes a*a but NVRTC-fails those.  With an aligned
# CUDA-13 stack (cupy-cuda13x + torch cu13x) import order is irrelevant.
_CUPY_PROBE = ('import cupy as c\n'
               'a=c.random.RandomState(0).random_sample(512)\n'
               'c.where(a>0.5,a,0.0); int(c.searchsorted(c.cumsum(a),0.1))\n'
               'assert c.argpartition(a,506)[506:].size==6\n'
               'c.cuda.runtime.deviceSynchronize()')
_TORCH_PROBE = ('import torch as t; assert t.cuda.is_available(); '
                'x=t.arange(64,device="cuda")*2; assert int(x.sum())==4032; t.cuda.synchronize()')


def _probe(code: str) -> bool:
    try:
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return None


def _import_if(ok, name):
    if not ok:
        return None
    try:
        return __import__(name)
    except Exception:
        return None


cp    = _import_if(_probe(_CUPY_PROBE), 'cupy')
torch = _import_if(_probe(_TORCH_PROBE), 'torch')


def backends() -> dict:
    return {'numpy': True, 'cupy': cp is not None, 'torch': torch is not None}


def describe_backends() -> str:
    parts = ['numpy ' + np.__version__]
    if cp is not None:
        try:
            dev = cp.cuda.runtime.getDeviceProperties(0)['name'].decode()
        except Exception:
            dev = '?'
        parts.append(f'cupy {cp.__version__} ({dev})')
    else:
        import importlib.util
        installed = importlib.util.find_spec('cupy') is not None
        parts.append('cupy: INSTALLED but non-functional (GPU probe crashed - '
                     'driver/runtime mismatch)' if installed else 'cupy: not installed')
    if torch is not None:
        parts.append(f'torch {torch.__version__} ({torch.cuda.get_device_name(0)})')
    else:
        parts.append('torch: unavailable')
    return '  |  '.join(parts)


def sync_cupy():
    if cp is not None:
        cp.cuda.runtime.deviceSynchronize()


def sync_torch():
    if torch is not None:
        torch.cuda.synchronize()


# ── timing: warmup (alloc/JIT) then `reps` synced reps; ms ──────────────────────
def bench(fn, *, sync=None, warmup=3, reps=10):
    """Time fn() (returns its own result).  `sync` (a device-sync callable) is called before
    each stop so GPU async launches are fully counted.  Returns (last_result, stats_ms)."""
    for _ in range(warmup):
        fn()
    if sync:
        sync()
    times, res = [], None
    for _ in range(reps):
        t0 = time.perf_counter()
        res = fn()
        if sync:
            sync()
        times.append((time.perf_counter() - t0) * 1e3)
    return res, {'min_ms': min(times), 'median_ms': statistics.median(times),
                 'mean_ms': statistics.fmean(times)}


def median_ms(fn, **kw):
    return bench(fn, **kw)[1]['median_ms']


# ── conversions / equivalence ───────────────────────────────────────────────────
def to_numpy(x):
    if cp is not None and isinstance(x, cp.ndarray):
        return cp.asnumpy(x)
    if torch is not None and isinstance(x, torch.Tensor):
        return x.detach().cpu().numpy()
    return np.asarray(x)


def equiv(a, b, *, rtol=1e-4, atol=1e-5) -> bool:
    a, b = to_numpy(a), to_numpy(b)
    if a.shape != b.shape:
        return False
    return bool(np.allclose(a, b, rtol=rtol, atol=atol))


def set_overlap(a, b) -> float:
    """Jaccard overlap of two id/sku collections - distributional agreement for sampling."""
    sa, sb = {int(x) for x in a}, {int(x) for x in b}
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 1.0


# ── aggregate table ──────────────────────────────────────────────────────────────
def record(rows, kernel, size, cpu_ms, cupy_ms, torch_ms, equivalent, note=''):
    rows.append({'kernel': kernel, 'size': str(size), 'cpu_ms': cpu_ms,
                 'cupy_ms': cupy_ms, 'torch_ms': torch_ms,
                 'equiv': equivalent, 'note': note})


def _spd(cpu, g):
    return f'{cpu / g:4.1f}x' if (g and g > 0) else '  -  '


def _cell(v):
    return f'{v:8.2f}' if (v is not None) else '     n/a'


def print_table(rows, title=''):
    if title:
        print(f'\n{title}')
    print(f'{"kernel":30} {"size":>14} {"cpu ms":>9} {"cupy ms":>9} {"":>6} '
          f'{"torch ms":>9} {"":>6}  eq')
    for r in rows:
        print(f'{r["kernel"]:30} {r["size"]:>14} {_cell(r["cpu_ms"])} '
              f'{_cell(r["cupy_ms"])} {_spd(r["cpu_ms"], r["cupy_ms"]):>6} '
              f'{_cell(r["torch_ms"])} {_spd(r["cpu_ms"], r["torch_ms"]):>6}  '
              f'{("yes" if r["equiv"] else "NO ") if r["equiv"] is not None else " - "}'
              + (f'   {r["note"]}' if r['note'] else ''))


if __name__ == '__main__':
    print('GPU benchmark backends:')
    print(' ', describe_backends())
    print(' ', backends())
