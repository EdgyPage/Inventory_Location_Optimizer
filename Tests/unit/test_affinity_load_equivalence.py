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

import os
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


def test_sidecar_roundtrip_is_bit_identical(tmp_path):
    """The affinity_arrays.npz cache: a reopen served from the sidecar must produce the
    SAME store, bit for bit, as the SQL load that wrote it — and a stale sidecar (the DB
    file changed underneath) must be ignored and rewritten, not served."""
    db = str(tmp_path / 'affinity.db')
    st1 = AffinityStore(db)
    st1._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)',
                          _adversarial_rows())
    st1._conn.commit()
    st1._load_matrix()                                  # SQL load; writes the sidecar
    sc = st1._sidecar_path()
    assert sc is not None and os.path.exists(sc), 'the SQL load must leave a sidecar'

    st2 = AffinityStore(db)                             # fresh open: sidecar hit
    _assert_bit_identical(st2, st1._sku_to_idx, st1._matrix)

    # stale detection: grow the DB file -> stamp mismatch -> SQL reload + fresh sidecar
    st1._conn.execute('INSERT OR REPLACE INTO affinity VALUES (77771, 77772, 4.5)')
    st1._conn.commit()
    st3 = AffinityStore(db)
    assert 77771 in st3._sku_to_idx, 'a stale sidecar was served over the changed DB'
    st4 = AffinityStore(db)                             # rewritten sidecar serves the new shape
    _assert_bit_identical(st4, st3._sku_to_idx, st3._matrix)

    # corruption: an unreadable sidecar degrades to the SQL path, never raises
    with open(sc, 'wb') as fh:
        fh.write(b'garbage')
    st5 = AffinityStore(db)
    _assert_bit_identical(st5, st3._sku_to_idx, st3._matrix)


def test_sidecar_never_crosses_between_dbs_in_one_directory(tmp_path):
    """Two same-shaped stores in one directory must never serve each other's arrays.
    The first sidecar cut used a FIXED file name and a (size, change-counter) stamp —
    two seed-variant DBs collided on both and one silently served the other's lifts."""
    rng = random.Random(5)
    rows_a = [(a, b, 1.0 + rng.random()) for a in range(1, 30) for b in range(31, 40)]
    rows_b = [(a, b, 1.0 + rng.random()) for a in range(1, 30) for b in range(31, 40)]
    for name, rows in (('aff1.db', rows_a), ('aff2.db', rows_b)):
        st = AffinityStore(str(tmp_path / name))
        st._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)', rows)
        st._conn.commit()
        st._load_matrix()                                    # writes that DB's own sidecar
    re1 = AffinityStore(str(tmp_path / 'aff1.db'))
    re2 = AffinityStore(str(tmp_path / 'aff2.db'))
    assert re1._sidecar_path() != re2._sidecar_path(), 'sidecar name must carry the db name'
    assert re1._matrix.data.tobytes() != re2._matrix.data.tobytes(), (
        'two different affinity DBs came back with identical lift arrays — a sidecar '
        'served across files')


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
