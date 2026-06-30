"""batch_precompute.py — compute each warehouse family's batch sequence ONCE and share it.

Batch sampling (`Workload_Builder.Batch`) is a PURE function of (batch_cfg, inventory, affinity,
seed_batches + i): it weights SKUs by static demand frequency x the immutable affinity-lift CSR and
draws from a dedicated per-batch RNG `random.Random(seed_batches + i)`.  Today every strategy arm of a
warehouse family re-samples the identical sequence inside its own worker process — the heavy
O(k*|inventory|) sampling (the bulk of the per-batch "build" section) is paid ~32x for bit-identical
results.  This module computes the sequence ONCE (optionally in parallel chunks) so every arm of the
family reads it instead, while the genuinely sequential `Task.from_batch` stays per-arm in the loop.

Correctness rests on two things:
  * `load_inventory_from_db` selects `ORDER BY sku`, so the candidate order — which
    `_lift_weighted_sample` indexes positionally — is deterministic; loading the SAME db with the SAME
    limit/allowlist the worker uses reproduces the worker's candidates exactly.
  * a content FINGERPRINT over exactly the fields a batch consumes (the ordered
    (sku, relative_frequency, quantity_rate) triples + batch_cfg + seeds + the affinity CSR digest)
    keys the on-disk list.  Families of different sizes get different fingerprints (so they never share
    a list), and a worker accepts a file only when its own recomputed fingerprint matches — never
    another family's.  On any mismatch/miss the caller falls back to inline sampling (still correct).
"""
from __future__ import annotations

import hashlib
import os
import pickle
import random
import struct
import sys

import numpy as np

# Path setup mirrors strategy_runner so Warehouse imports resolve in spawned chunk workers.
_HERE      = os.path.dirname(os.path.abspath(__file__))
_WAREHOUSE = os.path.normpath(os.path.join(_HERE, '..', 'Warehouse'))
for _p in (_WAREHOUSE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from Affinity_Store import AffinityStore                       # noqa: E402
from Workload_Builder import Batch                             # noqa: E402
from generation.generate_inventory import load_inventory_from_db  # noqa: E402

# Below this many batches (or with workers<=1) precompute runs serially — a transient process pool's
# spawn/import overhead isn't worth amortizing for a tiny sequence.
_MIN_PARALLEL = 16


def _load_worker_inventory(inv_db: str, max_skus, sku_allowlist):
    """Load inventory EXACTLY as strategy_runner._run_strategy_worker does (same db, limit, allowlist)
    so the candidate list — and therefore every sampled batch — is identical to the worker's."""
    inv = load_inventory_from_db(inv_db, limit=max_skus)
    if sku_allowlist is not None:
        inv.orders = [c for c in inv.orders if c.sku in sku_allowlist]
    return inv


def _affinity_digest(affinity) -> str:
    """Hash the immutable affinity-lift CSR (the values batch selection multiplies in).  Distinguishes
    different affinity DBs; identical for the same DB loaded twice (parent precompute vs worker)."""
    m = getattr(affinity, '_matrix', None)
    if m is None:
        return 'noaff'
    h = hashlib.blake2b(digest_size=16)
    for arr in (m.indptr, m.indices, m.data):
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def batch_fingerprint(inventory, batch_cfg, seed_batches: int, n_batches: int, affinity) -> str:
    """Stable hash over EXACTLY the inputs that determine the batch sequence (order included)."""
    orders = inventory.orders
    n = len(orders)
    skus = np.fromiter((int(c.sku) for c in orders), dtype=np.int64, count=n)
    freq = np.fromiter((float(c.demand.relative_frequency) for c in orders), dtype=np.float64, count=n)
    qty  = np.fromiter((float(c.demand.quantity_rate) for c in orders), dtype=np.float64, count=n)
    h = hashlib.blake2b(digest_size=20)
    h.update(skus.tobytes()); h.update(freq.tobytes()); h.update(qty.tobytes())
    h.update(struct.pack('<idd', int(batch_cfg.inventory_size),
                         float(batch_cfg.mean_fraction), float(batch_cfg.std_fraction)))
    h.update(struct.pack('<qq', int(seed_batches), int(n_batches)))
    h.update(_affinity_digest(affinity).encode('ascii'))
    return h.hexdigest()


def _sample_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg, seed_batches, lo, hi):
    """Module-level (spawn-picklable) chunk worker: load inv+aff once, sample batches [lo, hi)."""
    inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist)
    aff = AffinityStore(aff_db) if aff_db else None
    return [Batch(batch_cfg, inv, affinity=aff, rng=random.Random(seed_batches + i))
            for i in range(lo, hi)]


def precompute_batches(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                       seed_batches: int, n_batches: int, workers: int = 1) -> list:
    """Return the full list of `Batch` objects for i in [0, n_batches).

    Serial when workers<=1 or n_batches<_MIN_PARALLEL; otherwise splits the range across a transient
    spawn pool (each chunk loads inv+aff once, then samples its slice).  Result order is always 0..n-1.
    """
    if workers <= 1 or n_batches < _MIN_PARALLEL:
        return _sample_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                             seed_batches, 0, n_batches)

    import concurrent.futures as cf
    import multiprocessing as mp
    nchunks = min(workers, n_batches)
    bounds = [(round(j * n_batches / nchunks), round((j + 1) * n_batches / nchunks))
              for j in range(nchunks)]
    out: list = [None] * nchunks
    ctx = mp.get_context('spawn')
    with cf.ProcessPoolExecutor(max_workers=nchunks, mp_context=ctx) as ex:
        futs = {ex.submit(_sample_range, inv_db, max_skus, sku_allowlist, aff_db,
                          batch_cfg, seed_batches, lo, hi): j
                for j, (lo, hi) in enumerate(bounds)}
        for f in cf.as_completed(futs):
            out[futs[f]] = f.result()
    batches: list = []
    for chunk in out:
        batches.extend(chunk)
    return batches


def write_batches(path: str, fingerprint: str, batches: list) -> None:
    """Persist {fingerprint, batches} atomically (tmp + os.replace) so a half-written file is never
    observed as complete."""
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'wb') as f:
        pickle.dump({'fingerprint': fingerprint, 'n': len(batches), 'batches': batches},
                    f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def load_batches(path: str, expected_fingerprint: str) -> list | None:
    """Return the stored batch list IFF the stored fingerprint matches expected; else None so the
    caller falls back to inline sampling (never the wrong family's list)."""
    try:
        with open(path, 'rb') as f:
            blob = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, ValueError):
        return None
    if not isinstance(blob, dict) or blob.get('fingerprint') != expected_fingerprint:
        return None
    return blob.get('batches')


def ensure_batches(out_dir: str, inv_db: str, max_skus, sku_allowlist, aff_db, batch_cfg,
                   seed_batches: int, n_batches: int, workers: int = 1, log=None):
    """Compute-or-reuse this family's batch file under out_dir.  Returns (path, fingerprint).

    The file is named by the fingerprint so different families never collide and identical families
    (e.g. configs that don't change inventory) share one file.  Existence ⇒ reuse (write is atomic);
    the worker re-verifies the full fingerprint on load.
    """
    inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist)
    aff = AffinityStore(aff_db) if aff_db else None
    fp  = batch_fingerprint(inv, batch_cfg, seed_batches, n_batches, aff)
    path = os.path.join(out_dir, f'_batches_{fp[:16]}.pkl')
    if os.path.exists(path):
        if log is not None:
            log.info(f'  Batches: reuse {os.path.basename(path)} ({n_batches} batches, fp {fp[:8]})')
        return path, fp
    if log is not None:
        log.info(f'  Batches: precompute {n_batches} (workers={workers}) -> '
                 f'{os.path.basename(path)} (fp {fp[:8]})')
    batches = precompute_batches(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                                 seed_batches, n_batches, workers=workers)
    write_batches(path, fp, batches)
    return path, fp
