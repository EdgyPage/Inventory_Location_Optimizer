"""test_affinity_load_equivalence.py

Frozen-oracle gate for the `AffinityStore._load_matrix` streaming rewrite (perf campaign
Phase 3a).  `_oracle_load_matrix` below is the ORIGINAL implementation preserved verbatim
(fetchall + zip(*rows) + np.asarray — the 1.4 GB-transient path the rewrite replaces).
Production must produce a CSR that is BIT-identical to the oracle's — same dtypes, same
indptr/indices/data bytes — plus an identical `_sku_to_idx`, on:

  * a shuffled-insert-order table (row order must be irrelevant: CSR canonicalizes),
  * skus that appear only as sku_j (the unique/searchsorted union must keep them),
  * duplicate lift VALUES across distinct pairs (no value-keyed dedup may sneak in),
  * an empty table (the n==0 early return: `_sku_to_idx == {}`, `_matrix is None`).

The mutation check at the bottom proves the comparison can fail: a float64 matrix (the
dtype a careless rewrite would inherit from SQLite's REAL) is rejected.

    cd Tests && python -m pytest unit/test_affinity_load_equivalence.py
"""
from __future__ import annotations

import random

import numpy as np
from scipy.sparse import csr_matrix

from Warehouse.catalog.Affinity_Store import AffinityStore


# ── the frozen oracle: _load_matrix as of commit f59f38e, verbatim ────────────────────
def _oracle_load_matrix(conn):
    """Original fetchall-based load.  Returns (sku_to_idx, matrix)."""
    rows = conn.execute(
        'SELECT sku_i, sku_j, lift FROM affinity'
    ).fetchall()

    if not rows:
        return {}, None

    sku_i_list, sku_j_list, lift_list = zip(*rows)
    sku_i = np.asarray(sku_i_list, dtype=np.int32)
    sku_j = np.asarray(sku_j_list, dtype=np.int32)
    lift  = np.asarray(lift_list,  dtype=np.float32)

    all_skus = np.unique(np.concatenate([sku_i, sku_j]))
    sku_to_idx = {int(s): i for i, s in enumerate(all_skus)}

    row_idxs = np.searchsorted(all_skus, sku_i).astype(np.int32)
    col_idxs = np.searchsorted(all_skus, sku_j).astype(np.int32)
    matrix = csr_matrix(
        (lift, (row_idxs, col_idxs)),
        shape=(len(all_skus), len(all_skus)),
        dtype=np.float32,
    )
    return sku_to_idx, matrix


# ── fixtures ───────────────────────────────────────────────────────────────────────────
def _store_with_rows(rows):
    """In-memory AffinityStore holding exactly `rows`, inserted in the given order."""
    st = AffinityStore(':memory:')
    st._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)', rows)
    st._conn.commit()
    st._load_matrix()
    return st


def _adversarial_rows():
    """~2k pairs: shuffled insert order, j-only skus, duplicated lift values."""
    rng = random.Random(20260819)
    rows = []
    # dense-ish block over skus 100..149 (both directions, canonical duplicates of values)
    skus = list(range(100, 150))
    for a in skus:
        for b in rng.sample(skus, 8):
            if a != b:
                v = rng.choice([1.5, 2.0, 2.0, 3.25, 0.75])   # deliberate value collisions
                rows.append((a, b, v))
    # skus that appear ONLY as sku_j (never as sku_i) — the union must still index them
    for j_only in (9001, 9002, 9003):
        rows.append((rng.choice(skus), j_only, 1.0 + rng.random()))
    # dedup on PK the same way INSERT OR REPLACE would, then shuffle the insert order
    rows = list({(a, b): (a, b, v) for a, b, v in rows}.values())
    rng.shuffle(rows)
    return rows


def _assert_bit_identical(store, oracle_idx, oracle_mat):
    assert store._sku_to_idx == oracle_idx
    m, o = store._matrix, oracle_mat
    assert m.shape == o.shape
    assert m.dtype == o.dtype == np.float32
    assert m.indptr.dtype == o.indptr.dtype
    assert m.indices.dtype == o.indices.dtype
    assert m.indptr.tobytes() == o.indptr.tobytes()
    assert m.indices.tobytes() == o.indices.tobytes()
    assert m.data.tobytes() == o.data.tobytes()


# ── the gate ───────────────────────────────────────────────────────────────────────────
def test_load_matrix_matches_frozen_oracle():
    st = _store_with_rows(_adversarial_rows())
    oracle_idx, oracle_mat = _oracle_load_matrix(st._conn)
    assert oracle_mat is not None and oracle_mat.nnz > 100     # non-vacuous fixture
    # j-only skus really are j-only AND indexed (the case a naive rewrite drops)
    i_skus = {r[0] for r in st._conn.execute('SELECT DISTINCT sku_i FROM affinity')}
    assert 9001 not in i_skus and 9001 in oracle_idx
    _assert_bit_identical(st, oracle_idx, oracle_mat)


def test_load_matrix_row_order_irrelevant():
    rows = _adversarial_rows()
    st_fwd = _store_with_rows(rows)
    st_rev = _store_with_rows(list(reversed(rows)))
    _assert_bit_identical(st_rev, st_fwd._sku_to_idx, st_fwd._matrix)


def test_load_matrix_empty_table():
    st = AffinityStore(':memory:')          # __init__ already ran _load_matrix on 0 rows
    assert st._sku_to_idx == {}
    assert st._matrix is None


def test_comparison_rejects_float64_impostor():
    """Prove the bit-identity assertion can fail: the dtype a lazy rewrite would produce."""
    st = _store_with_rows(_adversarial_rows())
    oracle_idx, oracle_mat = _oracle_load_matrix(st._conn)
    impostor = oracle_mat.astype(np.float64)

    class _Fake:
        _sku_to_idx = oracle_idx
        _matrix = impostor

    try:
        _assert_bit_identical(_Fake, oracle_idx, oracle_mat)
    except AssertionError:
        return
    raise AssertionError('bit-identity check accepted a float64 matrix')
