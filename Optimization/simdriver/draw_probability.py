"""draw_probability.py — the DRAW PROBABILITY `p_s`: what the declared sampler actually does.

The coverage record prices a SKU's prior lines at its **line share** `pi_s = freq / sum freq`
(`Optimization/simconfig/coverage.py`).  That is the sampler's BASE WEIGHT, not its inclusion
probability.  `Workload_Builder._lift_weighted_sample` draws `k` DISTINCT SKUs per batch WITHOUT
replacement and multiplies every survivor's weight by `prod lift(A, B)` over the partners already
drawn, so the probability a SKU lands in a batch is a different function of the catalogue, not a
scaled one (.scratch/department-calibration, "Close the fulfillment fill-law gap", decision 1).

This module computes that probability -- the **draw probability** `p_s` -- by running the declared
sampler and nothing else.  No warehouse is planned, no inventory is placed, no simulation is
stepped: exactly `Batch(batch_cfg, inventory, affinity, rng=random.Random(seed_batches + i))` for
`i` in `[0, M)`, which is the identical call `batch_precompute._sample_range` makes.  Because the
run's own script uses the SAME base seed, the run's `n_batches` batches are a PREFIX of this sample
(decision 7) -- there is no second demand stream to reconcile.

WHY THIS IS AFFORDABLE, and why it may run BEFORE the coverage fixed point.  Under the era the two
`units_per_line` cancel in `era_coverage.stage_a`, so a channel's `mean_fraction` reduces to the
declared `STORE_DEMAND` / `FF_DEMAND` constant: `k` is a declared fraction times the section size
and does NOT depend on the fixed point's `n`.  `p_s` is therefore a pure function of
`(inventory, affinity, batch_cfg, seed)` -- all declared before the solve runs -- and is drawn ONCE
rather than re-drawn per round.  (`batch_precompute` runs AFTER the solve, from the converged
declaration, which is why its file cannot serve this purpose: it holds `n_batches` batches, and
`n_batches` is a window length, not a precision.)

WHAT COMES BACK, and why it is COUNTS rather than probabilities.  A chunk returns the per-SKU
count `c_s` of batches that included it, positionally over `load_inventory_from_db`'s
`ORDER BY sku` -- the same candidate order `_lift_weighted_sample` indexes, so the vector aligns
with the inventory the caller already holds.  Counts are kept rather than `c_s / M` because the
consumer needs BOTH estimators and only counts can give the unbiased one:

    p_s               ->  c_s / M                                       (the plug-in)
    p_s^j (1-p_s)^i   ->  c_s^(j) · (M - c_s)^(i) / M^(j+i)             (falling factorials)

the second being exact for `c_s ~ Binomial(M, p_s)`.  This matters because the fill law's
line-weighted functional is CONVEX in `p_s`, so a plug-in carries an upward Jensen bias that
shrinks only as `M` grows -- the identical artifact that, at the 20-batch window, was worth two
thirds of the apparent movement in "Close the fulfillment fill-law gap".  `M` is DECLARED by
requiring the two to agree (`prior_line_probability` below, and `--ladder` on the CLI), which makes
the precision a measurement rather than a guess.  The plug-in is what the record carries; the
unbiased form is the diagnostic that sizes `M`.

Chunk counts are stored SEPARATELY and in range order, so any prefix `M_j` is a partial sum: one
draw answers the whole ladder instead of one draw per rung.

The fingerprint is `batch_precompute.batch_fingerprint` unchanged -- the same inputs decide both
sequences, and `M` enters it through the `n_batches` slot.  Files are named `_drawp_*.npz`, so a
draw can never be served from (or poison) a `_batches_*.pkl`.
"""
from __future__ import annotations

import os
import sys

import numpy as np

# No sys.path bootstrap: imports are package-absolute and spawn's prepare() propagates the
# parent's sys.path (seeded by the entry script) to chunk workers.
from Optimization.simdriver.batch_precompute import (
    _load_worker_inventory,
    batch_fingerprint,
)
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.picking.Workload_Builder import Batch

#: Below this many batches (or with workers<=1) the draw runs serially — a transient pool's
#: spawn/import cost isn't worth amortizing, and the per-worker partner-map build is the
#: dominant fixed cost. Mirrors `batch_precompute._MIN_PARALLEL`.
_MIN_PARALLEL: int = 16
#: Default chunk count when the caller doesn't say. More chunks = a finer prefix ladder and
#: better load balance, at one partner-map build per WORKER (not per chunk — a worker process
#: is reused across the chunks it is handed, and the map is cached by `id(affinity)`).
_DEFAULT_CHUNKS: int = 32


#: Per-PROCESS memo of (inventory, affinity, sku->position).  This module hands a worker MANY
#: chunks (the prefix ladder wants more chunks than workers), and all three are expensive and
#: identical across them: on the reference pair the inventory load is ~7.5s, the partner-map
#: build ~10s, and `Workload_Builder._get_partner_map` caches by `id(affinity)` -- so a fresh
#: `AffinityStore` per chunk silently rebuilds 14M partner entries every time.  Keyed by the
#: declaration, so a worker that were ever handed two different sections stays correct.
#: (`batch_precompute._sample_range` needs none of this: it is called exactly once per worker.)
_ASSETS: dict = {}


def _assets(inv_db, max_skus, sku_allowlist, aff_db, channel_regime):
    """Load-or-reuse this process's (inventory, affinity, position map)."""
    allow = None if sku_allowlist is None else frozenset(sku_allowlist)
    key = (inv_db, max_skus, allow, aff_db, channel_regime)
    hit = _ASSETS.get(key)
    if hit is None:
        inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist, channel_regime)
        aff = AffinityStore(aff_db) if aff_db else None
        pos = {int(c.sku): i for i, c in enumerate(inv.orders)}
        hit = _ASSETS[key] = (inv, aff, pos)
    return hit


def _count_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg, seed_batches,
                 lo: int, hi: int, channel_regime=None) -> np.ndarray:
    """Module-level (spawn-picklable) chunk worker: draw batches `[lo, hi)` and return the
    per-SKU inclusion COUNT over the inventory's own order.

    Builds the full `Batch` rather than calling the sampler directly, deliberately: `Batch`
    also draws each selected SKU's line quantity from the same `rng`, and reproducing the
    run's stream exactly means reproducing every draw that stream makes, not only the ones
    this module reads.  The batches themselves are dropped as soon as they are counted, so
    peak memory is the memoized assets plus one batch and the count vector.

    MEMORY, not compute, sets the worker count.  The partner map is ~14M entries of Python
    tuples on the reference catalogue -- well over a gigabyte resident -- so each worker is
    expensive to hold and cheap to keep busy (~0.22 s/batch on the fulfillment section).  A
    handful of workers each drawing thousands of batches is the shape that fits; one worker
    per core is how this thrashes.
    """
    inv, aff, pos = _assets(inv_db, max_skus, sku_allowlist, aff_db, channel_regime)
    counts = np.zeros(len(inv.orders), dtype=np.int64)
    import random                                  # local: the worker's own stream only
    for i in range(lo, hi):
        b = Batch(batch_cfg, inv, affinity=aff, rng=random.Random(seed_batches + i))
        for sku in b.items:
            counts[pos[int(sku)]] += 1
    return counts


def precompute_counts(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                      seed_batches: int, n_draws: int, workers: int = 1,
                      chunks: int = _DEFAULT_CHUNKS, channel_regime=None) -> tuple:
    """Draw `n_draws` batches and return `(bounds, counts)`.

    `bounds` is the list of `(lo, hi)` chunk ranges in order and `counts` the matching
    `(len(bounds), n_skus)` int64 matrix, so `counts[:j].sum(0)` is the count vector at
    prefix `bounds[j-1][1]` -- the ladder, from one draw.
    """
    nchunks = max(1, min(int(chunks), int(n_draws)))
    bounds = [(round(j * n_draws / nchunks), round((j + 1) * n_draws / nchunks))
              for j in range(nchunks)]
    bounds = [(lo, hi) for lo, hi in bounds if hi > lo]

    if workers <= 1 or n_draws < _MIN_PARALLEL:
        rows = [_count_range(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                             seed_batches, lo, hi, channel_regime) for lo, hi in bounds]
        return bounds, np.vstack(rows)

    import concurrent.futures as cf
    import multiprocessing as mp
    out: list = [None] * len(bounds)
    ctx = mp.get_context('spawn')
    with cf.ProcessPoolExecutor(max_workers=min(workers, len(bounds)), mp_context=ctx) as ex:
        futs = {ex.submit(_count_range, inv_db, max_skus, sku_allowlist, aff_db,
                          batch_cfg, seed_batches, lo, hi, channel_regime): j
                for j, (lo, hi) in enumerate(bounds)}
        for f in cf.as_completed(futs):
            out[futs[f]] = f.result()
    return bounds, np.vstack(out)


def write_counts(path: str, fingerprint: str, skus: np.ndarray, bounds: list,
                 counts: np.ndarray, seed_batches: int) -> None:
    """Persist the draw atomically (tmp + os.replace) so a half-written file is never
    observed as complete."""
    tmp = f'{path}.tmp.{os.getpid()}'
    np.savez_compressed(
        tmp,
        fingerprint=np.array(fingerprint),
        sku=np.asarray(skus, dtype=np.int64),
        lo=np.array([lo for lo, _ in bounds], dtype=np.int64),
        hi=np.array([hi for _, hi in bounds], dtype=np.int64),
        counts=np.asarray(counts, dtype=np.int64),
        seed_batches=np.array(int(seed_batches), dtype=np.int64),
    )
    os.replace(f'{tmp}.npz', path)


def load_counts(path: str, expected_fingerprint: str | None = None) -> dict | None:
    """Return `{sku, counts, bounds, M, seed_batches}` IFF the stored fingerprint matches
    (or no expectation is given); else None, so a caller falls back to drawing.

    `counts` is the per-chunk matrix, NOT the total -- summing is the caller's, because the
    prefix ladder is the reason the chunks are kept apart."""
    try:
        with np.load(path, allow_pickle=False) as z:
            fp = str(z['fingerprint'])
            if expected_fingerprint is not None and fp != expected_fingerprint:
                return None
            lo, hi = z['lo'], z['hi']
            return {'fingerprint': fp, 'sku': z['sku'], 'counts': z['counts'],
                    'bounds': list(zip(lo.tolist(), hi.tolist())),
                    'M': int(hi.max()) if len(hi) else 0,
                    'seed_batches': int(z['seed_batches'])}
    except (OSError, ValueError, KeyError):
        return None


def ensure_draw_probability(out_dir: str, inv_db: str, max_skus, sku_allowlist, aff_db,
                            batch_cfg, seed_batches: int, n_draws: int, workers: int = 1,
                            chunks: int = _DEFAULT_CHUNKS, log=None,
                            channel_regime=None) -> tuple:
    """Compute-or-reuse this (pair, channel)'s draw file under `out_dir`.  Returns
    `(path, fingerprint)`.

    Named by the fingerprint, so two channels never collide and an identical declaration is a
    file-exists hit; the write is atomic and the loader re-verifies the fingerprint.  Exactly
    the contract `batch_precompute.ensure_batches` offers, over the same inputs, with `M` in
    the `n_batches` slot.
    """
    inv = _load_worker_inventory(inv_db, max_skus, sku_allowlist, channel_regime)
    aff = AffinityStore(aff_db) if aff_db else None
    fp = batch_fingerprint(inv, batch_cfg, seed_batches, n_draws, aff)
    path = os.path.join(out_dir, f'_drawp_{fp[:16]}.npz')
    if os.path.exists(path):
        if log is not None:
            log.info(f'  Draw probability: reuse {os.path.basename(path)} '
                     f'(M={n_draws:,}, fp {fp[:8]})')
        return path, fp
    if log is not None:
        log.info(f'  Draw probability: draw M={n_draws:,} (workers={workers}) -> '
                 f'{os.path.basename(path)} (fp {fp[:8]})')
    bounds, counts = precompute_counts(inv_db, max_skus, sku_allowlist, aff_db, batch_cfg,
                                       seed_batches, n_draws, workers=workers, chunks=chunks,
                                       channel_regime=channel_regime)
    skus = np.fromiter((int(c.sku) for c in inv.orders), dtype=np.int64, count=len(inv.orders))
    write_counts(path, fp, skus, bounds, counts, seed_batches)
    return path, fp


# ── the two estimators, and the functional that sizes M ──────────────────────────────────

def prior_line_probability(counts: np.ndarray, M: int, K: int) -> tuple:
    """`P(>= 1 prior line for the same SKU within K days | a line)`, line-weighted, both ways.

    The fill law on a floored section reduces to exactly this event, so it is the quantity
    whose precision decides `M`.  A line belongs to SKU `s` with probability proportional to
    `p_s`, and the days are independent (each batch draws from its own
    `random.Random(seed_batches + i)`; no cross-batch state), so

        Q(K) = sum_s p_s · (1 - (1 - p_s)^K) / sum_s p_s

    Returns `(plug_in, unbiased)`.  The plug-in substitutes `c_s / M`; the unbiased form uses
    the binomial falling-factorial identity

        E[ c^(j) · (M - c)^(i) ] = M^(j+i) · p^j · (1 - p)^i

    with `j = 1`, `i = K`, which is exact rather than an alternating expansion and therefore
    stable at every `K` the transit law puts mass on.  Their DIFFERENCE is the plug-in's
    Jensen bias at this `M`; agreement is the criterion that declares `M`.
    """
    c = np.asarray(counts, dtype=np.float64)
    M = float(M)
    K = int(K)

    p_hat = c / M
    denom = p_hat.sum()
    if denom <= 0.0:
        return 0.0, 0.0
    plug_in = float((p_hat * (1.0 - (1.0 - p_hat) ** K)).sum() / denom)

    # c^(1) · (M-c)^(K) / M^(K+1): built as a running product so nothing overflows and a SKU
    # whose failure count is shorter than K contributes an exact zero (it cannot have K
    # non-occurrences to spare), which is the correct value, not a truncation.
    num = c.copy()
    fail = M - c
    for i in range(K):
        num = num * np.clip(fail - i, 0.0, None)
    scale = 1.0
    for i in range(K + 1):
        scale *= (M - i)
    unbiased = float((p_hat - num / scale).sum() / denom)
    return plug_in, unbiased


def ladder(blob: dict, ks=(1, 2, 3, 4)) -> list:
    """The prefix ladder: `(M_j, K, plug_in, unbiased, abs_gap, rel_gap)` for every chunk
    prefix and every `K`.  One draw answers the whole table -- which is the point of keeping
    the chunk counts apart."""
    counts, bounds = blob['counts'], blob['bounds']
    rows = []
    for j in range(1, len(bounds) + 1):
        M_j = bounds[j - 1][1]
        tot = counts[:j].sum(axis=0)
        for K in ks:
            a, b = prior_line_probability(tot, M_j, K)
            gap = abs(a - b)
            rows.append((M_j, K, a, b, gap, (gap / b) if b > 0 else float('inf')))
    return rows


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
# Run as a MODULE (`python -m Optimization.simdriver.draw_probability`), never from a heredoc:
# a spawn pool launched from `python - <<EOF` hangs silently because its workers die
# re-importing `<stdin>` (memory `heredoc-python-breaks-the-spawn-pool`).

def _main(argv: list) -> int:
    import argparse
    import logging
    import time

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--inv-db', required=True)
    ap.add_argument('--aff-db', default=None)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--regime', default=None, help='store | fulfillment; omit for store-only')
    ap.add_argument('--mean-fraction', type=float, required=True)
    ap.add_argument('--std-fraction', type=float, required=True)
    ap.add_argument('--sampler', default='v2')
    ap.add_argument('--seed-batches', type=int, required=True)
    ap.add_argument('--draws', type=int, default=10_000, help='M')
    ap.add_argument('--chunks', type=int, default=_DEFAULT_CHUNKS)
    ap.add_argument('--workers', type=int, default=1)
    ap.add_argument('--max-skus', type=int, default=None)
    ap.add_argument('--ladder', action='store_true', help='print the plug-in/unbiased ladder')
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    log = logging.getLogger('drawp')

    from Optimization.config.channels import BatchConfig
    inv = _load_worker_inventory(a.inv_db, a.max_skus, None, a.regime)
    cfg = BatchConfig(inventory_size=max(1, len(inv.orders)),
                      mean_fraction=a.mean_fraction, std_fraction=a.std_fraction,
                      sampler=a.sampler)
    log.info(f'section {a.regime or "(all)"}: {len(inv.orders):,} SKUs, '
             f'k ~ {cfg.mean_fraction * cfg.inventory_size:,.0f} '
             f'+/- {cfg.std_fraction * cfg.inventory_size:,.0f}, sampler {cfg.sampler}')

    t0 = time.perf_counter()
    path, fp = ensure_draw_probability(a.out_dir, a.inv_db, a.max_skus, None, a.aff_db, cfg,
                                       a.seed_batches, a.draws, workers=a.workers,
                                       chunks=a.chunks, log=log, channel_regime=a.regime)
    dt = time.perf_counter() - t0
    log.info(f'  -> {path}  ({dt:,.0f}s, {dt / max(1, a.draws) * 1000:,.1f} ms/batch)')

    blob = load_counts(path, fp)
    if blob is None:
        log.error('  draw file did not verify against its own fingerprint')
        return 1
    tot = blob['counts'].sum(axis=0)
    M = blob['M']
    p = tot / M
    touched = int((tot > 0).sum())
    # Independence would touch `N · (1 - (1-p)^M)` distinct SKUs over the whole draw; the
    # shortfall is the concentration this module exists to measure.
    log.info(f'  M={M:,}  mean p={p.mean():.6f}  max p={p.max():.4f}  '
             f'touched {touched:,}/{len(p):,}')
    nz = p[p > 0]
    if len(nz):
        rse = float(np.mean(np.sqrt((1.0 - nz) / (M * nz))))
        log.info(f'  mean relative standard error of p_hat over touched SKUs: {rse:.4%}')

    if a.ladder:
        log.info(f'{"M":>9}{"K":>4}{"plug-in":>12}{"unbiased":>12}'
                 f'{"abs gap":>12}{"rel gap":>10}')
        for M_j, K, pi_, ub, gap, rel in ladder(blob):
            log.info(f'{M_j:>9,}{K:>4}{pi_:>12.6f}{ub:>12.6f}{gap:>12.2e}{rel:>10.2%}')
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
