"""test_batch_precompute.py — the HARD GATE for the batch-precompute dedup.

Steps 3-4 (wiring precompute into run_simulation / strategy_runner) must not begin until this is green.
It proves:
  * precomputed batches are bit-identical (num_skus + items) to the inline
    `Batch(batch_cfg, inv, aff, random.Random(seed_batches+i))` the worker computes today — across
    inventory sizes, with and without an affinity DB, and with a SKU allowlist;
  * the parallel chunked driver equals the serial result, and precompute is reproducible run-to-run;
  * a content fingerprint routes each warehouse family to ONLY its own list (different sizes ⇒ different
    fingerprints; loading with the wrong fingerprint returns None ⇒ the worker falls back, never uses
    another family's batches).

Run: python -m pytest Tests/test_batch_precompute.py
"""
from __future__ import annotations

import os
import random
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

import batch_precompute as BP                      # noqa: E402  (adds Warehouse to sys.path on import)
from Workload_Builder import Batch, BatchConfig    # noqa: E402
from Affinity_Store import AffinityStore           # noqa: E402
from generation.generate_inventory import (        # noqa: E402
    save_inventory_to_db, Inventory)
from Order import Order                            # noqa: E402

SEED_B = 1337
N = 24                                             # batches per sequence in the equivalence checks
_CATS = [('manual', 'standard'), ('manual', 'fragile'), ('automated', 'standard')]


# ── fixtures: small inventory + affinity DBs written exactly like the real pipeline ──────────────
def _make_inventory_db(path: str, n: int, seed: int = 0) -> str:
    rng = random.Random(seed)
    orders = []
    for s in range(1, n + 1):
        h, c = _CATS[s % len(_CATS)]
        orders.append(Order.build(
            sku=s, handling=h, category=c,
            length=rng.randint(5, 40), width=rng.randint(5, 40),
            height=rng.randint(5, 40), weight=rng.randint(1, 50),
            frequency=rng.uniform(0.01, 1.0), qty_rate=rng.randint(1, 10),
            equilibrium_qty=rng.randint(2, 20), reorder_point=1,
            lead_time_mean=0.0, supply_cv=0.0, stock_plan=None))
    save_inventory_to_db(Inventory(orders), path, {'test': True})
    return path


def _make_affinity_db(path: str, n: int, seed: int = 0) -> str:
    store = AffinityStore(path)
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        for j in range(i + 1, min(i + 4, n + 1)):          # a few symmetric lift partners per SKU
            lv = rng.uniform(1.1, 3.0)
            rows += [(i, j, lv), (j, i, lv)]
    store._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)', rows)
    store._conn.commit()
    store._conn.close()
    return path


def _cfg(inv_db: str, allow=None, max_skus=None) -> BatchConfig:
    inv = BP._load_worker_inventory(inv_db, max_skus, allow)
    return BatchConfig(inventory_size=len(inv.orders), mean_fraction=0.2, std_fraction=0.05)


def _naive(inv_db, max_skus, allow, aff_db, cfg, n) -> list:
    """The batch sequence exactly as strategy_runner builds it inline today."""
    inv = BP._load_worker_inventory(inv_db, max_skus, allow)
    aff = AffinityStore(aff_db) if aff_db else None
    return [Batch(cfg, inv, affinity=aff, rng=random.Random(SEED_B + i)) for i in range(n)]


def _assert_same(a_list, b_list):
    assert len(a_list) == len(b_list)
    for k, (a, b) in enumerate(zip(a_list, b_list)):
        assert a.num_skus == b.num_skus, f'batch {k}: num_skus {a.num_skus} != {b.num_skus}'
        assert a.items == b.items, f'batch {k}: items differ'


# ── A. identical-to-naive across sizes, with/without affinity ─────────────────────────────────────
@pytest.mark.parametrize('use_aff', [False, True])
@pytest.mark.parametrize('n_skus', [60, 200])
def test_precompute_matches_naive(tmp_path, n_skus, use_aff):
    inv_db = _make_inventory_db(str(tmp_path / f'inv{n_skus}.db'), n_skus, seed=n_skus)
    aff_db = _make_affinity_db(str(tmp_path / f'aff{n_skus}.db'), n_skus, seed=7) if use_aff else None
    cfg = _cfg(inv_db)
    naive = _naive(inv_db, None, None, aff_db, cfg, N)
    pre = BP.precompute_batches(inv_db, None, None, aff_db, cfg, SEED_B, N, workers=1)
    _assert_same(pre, naive)


# ── A'. allowlist path (worker filters inventory after load) ──────────────────────────────────────
def test_precompute_matches_naive_with_allowlist(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 150, seed=6)
    allow = frozenset(range(1, 80))                        # a subset of SKUs
    cfg = _cfg(inv_db, allow=allow)
    naive = _naive(inv_db, None, allow, None, cfg, N)
    pre = BP.precompute_batches(inv_db, None, allow, None, cfg, SEED_B, N, workers=1)
    _assert_same(pre, naive)


# ── A''. serial == parallel (the chunked driver must not change a single batch) ───────────────────
def test_serial_equals_parallel(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 200, seed=3)
    aff_db = _make_affinity_db(str(tmp_path / 'aff.db'), 200, seed=9)
    cfg = _cfg(inv_db)
    n = max(BP._MIN_PARALLEL, 20)                          # ensure the parallel branch triggers
    ser = BP.precompute_batches(inv_db, None, None, aff_db, cfg, SEED_B, n, workers=1)
    par = BP.precompute_batches(inv_db, None, None, aff_db, cfg, SEED_B, n, workers=4)
    _assert_same(ser, par)


# ── A'''. reproducible run-to-run ─────────────────────────────────────────────────────────────────
def test_reproducible(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 120, seed=5)
    cfg = _cfg(inv_db)
    a = BP.precompute_batches(inv_db, None, None, None, cfg, SEED_B, N, workers=1)
    b = BP.precompute_batches(inv_db, None, None, None, cfg, SEED_B, N, workers=1)
    _assert_same(a, b)


# ── B. per-family routing: each warehouse family loads only its own list ──────────────────────────
def test_fingerprint_distinguishes_families_and_blocks_cross_load(tmp_path):
    invA = _make_inventory_db(str(tmp_path / 'invA.db'), 60, seed=1)
    invB = _make_inventory_db(str(tmp_path / 'invB.db'), 180, seed=2)   # different size => different family
    cfgA, cfgB = _cfg(invA), _cfg(invB)
    fpA = BP.batch_fingerprint(BP._load_worker_inventory(invA, None, None), cfgA, SEED_B, N, None)
    fpB = BP.batch_fingerprint(BP._load_worker_inventory(invB, None, None), cfgB, SEED_B, N, None)
    assert fpA != fpB

    pathA, fp_writtenA = BP.ensure_batches(str(tmp_path), invA, None, None, None, cfgA,
                                           SEED_B, N, workers=1)
    assert fp_writtenA == fpA
    assert BP.load_batches(pathA, fpA) is not None         # correct family loads
    assert BP.load_batches(pathA, fpB) is None             # B must NEVER read A's list
    assert BP.load_batches(str(tmp_path / 'missing.pkl'), fpA) is None   # missing -> fallback


def test_affinity_change_changes_fingerprint(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 100, seed=8)
    cfg = _cfg(inv_db)
    inv = BP._load_worker_inventory(inv_db, None, None)
    aff1 = AffinityStore(_make_affinity_db(str(tmp_path / 'aff1.db'), 100, seed=1))
    aff2 = AffinityStore(_make_affinity_db(str(tmp_path / 'aff2.db'), 100, seed=2))
    fp_none = BP.batch_fingerprint(inv, cfg, SEED_B, N, None)
    fp1 = BP.batch_fingerprint(inv, cfg, SEED_B, N, aff1)
    fp2 = BP.batch_fingerprint(inv, cfg, SEED_B, N, aff2)
    assert len({fp_none, fp1, fp2}) == 3                   # affinity content participates in identity


# ── ensure_batches roundtrip + reuse ──────────────────────────────────────────────────────────────
def test_ensure_batches_roundtrip_and_reuse(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 100, seed=8)
    aff_db = _make_affinity_db(str(tmp_path / 'aff.db'), 100, seed=4)
    cfg = _cfg(inv_db)
    path, fp = BP.ensure_batches(str(tmp_path), inv_db, None, None, aff_db, cfg, SEED_B, N, workers=1)
    loaded = BP.load_batches(path, fp)
    _assert_same(loaded, _naive(inv_db, None, None, aff_db, cfg, N))
    path2, fp2 = BP.ensure_batches(str(tmp_path), inv_db, None, None, aff_db, cfg, SEED_B, N, workers=1)
    assert (path2, fp2) == (path, fp) and os.path.exists(path)   # reuse, no recompute


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
