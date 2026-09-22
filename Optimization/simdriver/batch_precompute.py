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

# No sys.path bootstrap: imports are package-absolute and spawn's prepare()
# propagates the parent's sys.path (seeded by the entry script) to chunk workers.
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.picking.Workload_Builder import Batch
from Warehouse.generation.generate_inventory import load_inventory_from_db

# Below this many batches (or with workers<=1) precompute runs serially — a transient process pool's
# spawn/import overhead isn't worth amortizing for a tiny sequence.
_MIN_PARALLEL = 16


def _load_worker_inventory(inv_db: str, max_skus, sku_allowlist, channel_regime=None):
    """Load inventory EXACTLY as strategy_runner._run_strategy_worker does (same db, limit,
    allowlist, THEN regime filter) so the candidate list — and therefore every sampled batch —
    is identical to the worker's.  ``channel_regime`` (store/fulfillment) keeps a mixed-catalog
    channel's stream regime-pure; None ⇒ the whole (allowlisted) inventory (store-only)."""
    inv = load_inventory_from_db(inv_db, limit=max_skus)
    if sku_allowlist is not None:
        inv.orders = [c for c in inv.orders if c.sku in sku_allowlist]
    if channel_regime is not None:
        from Warehouse.kernel.regime import regime_of
        inv.orders = [c for c in inv.orders if regime_of(c) == channel_regime]
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
    # Sampler VERSION: hashed only when not 'v1', so every fingerprint ever computed for
    # a v1 config stays byte-identical, while v2 batches can never be served from (or
    # poison) a v1 cache file.  Guarded getattr: pickled/legacy configs predate the field.
    _sampler = getattr(batch_cfg, 'sampler', 'v1')
    if _sampler != 'v1':
        h.update(f'sampler={_sampler}'.encode('ascii'))
    # The line LAW (`Demand.line`): hashed only for SKUs carrying a family other than the
    # founding `poisson_max1` -- whose one parameter IS `qty`, already hashed -- so every
    # fingerprint ever computed stays byte-identical while a catalogue stamped with another
    # law can never be served from (or poison) a Poisson cache.
    # (`getattr`: a demand stub with no law -- the v2 sampler tests' `_Demand` -- is the
    # founding family, exactly as `Demand.from_rates` reconstructs an absent law.)
    laws = [(int(c.sku), *c.demand.line.to_row()) for c in orders
            if getattr(c.demand, 'line', None) is not None
            and c.demand.line.family != 'poisson_max1']
    if laws:
        h.update(repr(laws).encode('utf-8'))
    return h.hexdigest()


def _sample_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg, seed_batches, lo, hi,
                  channel_regime=None):
    """Module-level (spawn-picklable) chunk worker: load inv+aff once, sample batches [lo, hi)."""
    inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist, channel_regime)
    aff = AffinityStore(aff_db) if aff_db else None
    return [Batch(batch_cfg, inv, affinity=aff, rng=random.Random(seed_batches + i))
            for i in range(lo, hi)]


def precompute_batches(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                       seed_batches: int, n_batches: int, workers: int = 1,
                       channel_regime=None) -> list:
    """Return the full list of `Batch` objects for i in [0, n_batches).

    Serial when workers<=1 or n_batches<_MIN_PARALLEL; otherwise splits the range across a transient
    spawn pool (each chunk loads inv+aff once, then samples its slice).  Result order is always 0..n-1.
    """
    if workers <= 1 or n_batches < _MIN_PARALLEL:
        return _sample_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                             seed_batches, 0, n_batches, channel_regime)

    import concurrent.futures as cf
    import multiprocessing as mp
    nchunks = min(workers, n_batches)
    bounds = [(round(j * n_batches / nchunks), round((j + 1) * n_batches / nchunks))
              for j in range(nchunks)]
    out: list = [None] * nchunks
    ctx = mp.get_context('spawn')
    with cf.ProcessPoolExecutor(max_workers=nchunks, mp_context=ctx) as ex:
        futs = {ex.submit(_sample_range, inv_db, max_skus, sku_allowlist, aff_db,
                          batch_cfg, seed_batches, lo, hi, channel_regime): j
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


def read_batches_blob(path: str) -> tuple[str, list] | None:
    """`(fingerprint, batches)` as stored, or None for a file that cannot be read.

    The reader for a consumer that does NOT know the fingerprint in advance -- a ranking tool
    walking a finished run's pair directory, where a leaf written before 2026-09-22 recorded
    no `batches_fingerprint` in its `sim_meta.json` and the two channels' lists sit side by
    side.  Such a caller matches the list to the channel by its SKUs; a worker never does,
    because `load_batches` below already knows what it expects.

    ImportError/AttributeError guard: a cache written before a module rename/move (e.g. the
    flat->package conversion) embeds the OLD class paths -- unpicklable now.  Treat it exactly
    like a corrupt file."""
    try:
        with open(path, 'rb') as f:
            blob = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, ValueError, ImportError, AttributeError):
        return None
    if not isinstance(blob, dict) or not isinstance(blob.get('batches'), list):
        return None
    return str(blob.get('fingerprint')), blob['batches']


def load_batches(path: str, expected_fingerprint: str) -> list | None:
    """Return the stored batch list IFF the stored fingerprint matches expected; else None so the
    caller falls back to inline sampling (never the wrong family's list)."""
    got = read_batches_blob(path)
    if got is None or got[0] != expected_fingerprint:
        return None
    return got[1]


def planned_lines(batches: list, n_batches: int | None = None) -> dict:
    """{sku: planned lines} over the first `n_batches` batches -- the SCRIPT's weight.

    ONE construction, because two things read it and must agree to the line: the worker
    hands it to `Inventory_Manager.enable_pick_owed` as the weight `pick_owed_s` prices the
    placement against (`strategy_runner._build_arm`), and `run_unload_ranking` rebuilds its
    TOTAL to price the lines that score declined to price (`unservable_weight`) at the leaf's
    mean line.  A second spelling would not fail; it would adjust the score by a weight the
    score was never measured on, silently.

    A batch's `items` is keyed by SKU, one key per line, so a SKU's weight is the number of
    batches that ask for it -- not the units, which is what makes the score a count of
    walks rather than of pieces.
    """
    lines: dict = {}
    for b in (batches if n_batches is None else batches[:n_batches]):
        for sku in b.items:
            lines[sku] = lines.get(sku, 0.0) + 1.0
    return lines


def _sibling_batches(out_dir: str, name: str) -> str | None:
    """The same family's batch file under a SIBLING CELL's pair dir, copied into `out_dir`;
    the copied path, or None when no sibling holds one.

    `out_dir` is `<run_root>/<cell>/<pair>`.  On a frozen matrix every cell samples the
    identical script -- same inventory, same seed, same batch content whenever the cells
    share a geometry (the whole `inbound_unload` matrix does) -- and until 2026-09-19 every
    cell recomputed it (~2 min a cell at campaign scale) because the cache path is per cell.
    Now the parent sets a cell up while the pool is running the previous cells' units, so
    that recompute also stole the pool's cores.  A sibling's file is the same bytes: the
    name IS the fingerprint, and the worker re-verifies the full fingerprint on load.

    Reserved (`_`-prefixed) siblings are skipped -- the frozen-inventory subtree holds no
    batches -- and a copy that fails for any reason is a miss, never an error.  Copied rather
    than referenced so the run tree's contract is unchanged: the artifact still exists per
    cell."""
    pair = os.path.basename(os.path.normpath(out_dir))
    cell_dir = os.path.dirname(os.path.normpath(out_dir))
    run_root = os.path.dirname(cell_dir)
    if not pair or not os.path.basename(cell_dir) or not os.path.isdir(run_root):
        return None
    try:
        siblings = sorted(os.listdir(run_root))
    except OSError:
        return None
    for d in siblings:
        if d.startswith('_') or d == os.path.basename(cell_dir):
            continue
        src = os.path.join(run_root, d, pair, name)
        if os.path.isfile(src):
            dst = os.path.join(out_dir, name)
            tmp = f'{dst}.tmp.{os.getpid()}'
            try:
                import shutil
                os.makedirs(out_dir, exist_ok=True)
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
                return dst
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue
    return None


def ensure_batches(out_dir: str, inv_db: str, max_skus, sku_allowlist, aff_db, batch_cfg,
                   seed_batches: int, n_batches: int, workers: int = 1, log=None,
                   channel_regime=None):
    """Compute-or-reuse this family's batch file under out_dir.  Returns (path, fingerprint).

    The file is named by the fingerprint so different families never collide and identical families
    (e.g. configs that don't change inventory) share one file.  Existence ⇒ reuse (write is atomic);
    the worker re-verifies the full fingerprint on load.  A sibling cell's copy of the same file
    is reused before anything is computed (`_sibling_batches`).

    ``channel_regime`` (store/fulfillment) makes the precompute regime-pure so a mixed-catalog
    channel shares ONE list across all its configs (store and fulfillment necessarily get distinct
    files: different regime SKUs, seed, and batch fraction all feed the fingerprint).
    """
    inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist, channel_regime)
    aff = AffinityStore(aff_db) if aff_db else None
    fp  = batch_fingerprint(inv, batch_cfg, seed_batches, n_batches, aff)
    path = os.path.join(out_dir, f'_batches_{fp[:16]}.pkl')
    if os.path.exists(path):
        if log is not None:
            log.info(f'  Batches: reuse {os.path.basename(path)} ({n_batches} batches, fp {fp[:8]})')
        return path, fp
    copied = _sibling_batches(out_dir, os.path.basename(path))
    if copied is not None:
        if log is not None:
            log.info(f'  Batches: copied {os.path.basename(path)} from a sibling cell '
                     f'({n_batches} batches, fp {fp[:8]})')
        return copied, fp
    if log is not None:
        log.info(f'  Batches: precompute {n_batches} (workers={workers}) -> '
                 f'{os.path.basename(path)} (fp {fp[:8]})')
    batches = precompute_batches(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                                 seed_batches, n_batches, workers=workers,
                                 channel_regime=channel_regime)
    write_batches(path, fp, batches)
    return path, fp
