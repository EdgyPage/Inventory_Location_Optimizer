"""gpu_broker.py — one process, one CUDA context: the shared GPU server for placement argmin.

CPU workers (gpu_client.GpuClient) send (request_id, response_queue, v, D, M, intercept, tile,
est_bytes) over a Manager request queue.  The broker admits each request against a measured VRAM
budget (so concurrency trades off against per-job size), runs the tiled cost-matrix argmin on the
GPU across a small thread+stream pool, and returns (request_id, idx | None).  EVERY GPU op is
wrapped so a bad/oversized request degrades to None (the client then computes on CPU) — the broker
process never dies, so an unattended run can't be taken down by GPU trouble.

torch is the broker backend (own process, single context — no cupy/torch nvrtc ordering concern).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

_HEADROOM_BYTES = 512 * 1024 * 1024     # leave room for display + context + fragmentation
SHUTDOWN = None                          # sentinel pushed to the request queue to stop


def gpu_argmin_torch(torch, v, D, M, intercept, tile, stream=None):
    """Tiled cost-matrix argmin on the GPU (mirror of gpu_client._argmin_tiled).  Peak working
    set ~ U*tile, so VRAM is bounded by the tile, not C.  Returns int64 idx[U] on the host."""
    import contextlib
    ctx = torch.cuda.stream(stream) if stream is not None else contextlib.nullcontext()
    with ctx:
        tv = torch.as_tensor(v, device='cuda', dtype=torch.float64)
        tD = torch.as_tensor(D, device='cuda', dtype=torch.float64)
        tM = torch.as_tensor(M, device='cuda', dtype=torch.float64)
        base = tM * intercept + tD
        u, c = tv.shape[0], tD.shape[0]
        best_val = torch.full((u,), float('inf'), device='cuda', dtype=torch.float64)
        best_idx = torch.zeros((u,), device='cuda', dtype=torch.long)
        rows = torch.arange(u, device='cuda')
        s = 0
        while s < c:
            e = min(s + tile, c)
            chunk = tv.view(u, 1) * tM[s:e].view(1, e - s) + base[s:e].view(1, e - s)
            cidx = chunk.argmin(dim=1)
            cval = chunk[rows, cidx]
            upd = cval < best_val
            best_idx = torch.where(upd, cidx + s, best_idx)
            best_val = torch.where(upd, cval, best_val)
            s = e
        return best_idx.detach().cpu().numpy().astype(np.int64)


def compute_budget(torch, vram_frac: float) -> int:
    free, _total = torch.cuda.mem_get_info()
    return max(0, int(free * vram_frac) - _HEADROOM_BYTES)


def broker_main(request_q, status, vram_frac=0.6, max_inflight=8, log_q=None):
    """Broker entry point (run in its own process).  `status` is a Manager dict; we set
    status['ready']/'budget'/'error' so the parent can confirm startup."""
    try:
        import torch
        if not torch.cuda.is_available():
            status['ready'] = False
            status['error'] = 'cuda not available in broker'
            return
        torch.cuda.init()
        budget = compute_budget(torch, vram_frac)
        status['budget'] = budget
        status['ready'] = True
    except Exception as exc:                                      # noqa: BLE001
        status['ready'] = False
        status['error'] = repr(exc)
        return

    # byte-budget admission shared across the worker threads (concurrency = f(job size))
    lock = threading.Condition()
    reserved = [0]
    streams = [torch.cuda.Stream() for _ in range(max_inflight)]
    served = [0]
    fell_back = [0]

    def _reserve(est, deadline=2.0):
        import time as _t
        end = _t.perf_counter() + deadline
        with lock:
            while reserved[0] + est > budget:
                if est > budget:
                    return False
                if not lock.wait(timeout=max(0.0, end - _t.perf_counter())):
                    return False
            reserved[0] += est
            return True

    def _release(est):
        with lock:
            reserved[0] -= est
            lock.notify_all()

    def _handle(rid, resp_q, v, D, M, intercept, tile, est, sidx):
        idx = None
        if _reserve(est):
            try:
                idx = gpu_argmin_torch(torch, v, D, M, intercept, tile, streams[sidx])
                served[0] += 1
            except Exception:                                    # OOM / runtime → fall back
                idx = None
                fell_back[0] += 1
            finally:
                _release(est)
        else:
            fell_back[0] += 1
        try:
            resp_q.put((rid, idx))
        except Exception:
            pass

    pool = ThreadPoolExecutor(max_workers=max_inflight)
    rr = [0]
    while True:
        item = request_q.get()
        if item is SHUTDOWN:
            break
        rid, resp_q, v, D, M, intercept, tile, est = item
        sidx = rr[0] % max_inflight
        rr[0] += 1
        pool.submit(_handle, rid, resp_q, v, D, M, intercept, tile, est, sidx)

    pool.shutdown(wait=True)
    try:
        status['served'] = served[0]
        status['fell_back'] = fell_back[0]
        status['peak_bytes'] = int(torch.cuda.max_memory_allocated())
    except Exception:
        pass


# ── lifecycle helpers (used by run_simulation and the benchmark) ─────────────────
def start_broker(manager, vram_frac=0.6, max_inflight=8):
    """Start the broker process.  Returns (proc, request_q, status) or (None, None, status)
    with status['error'] set if the GPU is unavailable.  Caller passes request_q to workers."""
    import multiprocessing as mp
    request_q = manager.Queue()
    status = manager.dict()
    proc = mp.get_context('spawn').Process(
        target=broker_main, args=(request_q, status, vram_frac, max_inflight), daemon=True)
    proc.start()
    # wait briefly for readiness
    import time
    for _ in range(200):
        if 'ready' in status:
            break
        time.sleep(0.05)
    if not status.get('ready'):
        return None, None, status
    return proc, request_q, status


def stop_broker(proc, request_q):
    try:
        if request_q is not None:
            request_q.put(SHUTDOWN)
        if proc is not None:
            proc.join(timeout=10)
    except Exception:
        pass
