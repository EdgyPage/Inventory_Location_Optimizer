from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING, Collection

import numpy as np
from scipy.sparse import csr_matrix

from Inventory_Builder import AffMatrix

if TYPE_CHECKING:
    from Inventory_Builder import Inventory


class AffinityStore:
    """SQLite-backed affinity matrix with in-memory CSR acceleration.

    At construction the full affinity table is loaded once into a scipy CSR
    sparse matrix so that delta_lift and sum_lift execute as pure numpy/scipy
    operations with no SQL round-trips.  The SQLite connection stays open only
    for writes (load_for_skus, index_inventory).

    Typical workflow
    ----------------
    store = AffinityStore('affinity.db')          # loads matrix once (~1-2 s)
    delta = store.delta_lift(sku, aisle_members)  # CSR row slice, O(partners)
    total = store.sum_lift(task_skus)             # scipy submatrix sum, O(k²)
    """

    def __init__(self, db_path: str = ':memory:', seed: int | None = None) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=-262144')    # 256 MB page cache
        self._conn.execute('PRAGMA temp_store=MEMORY')
        self._conn.execute('PRAGMA mmap_size=4294967296')  # 4 GB memory-mapped I/O
        self._rng = random.Random(seed)
        self._init_schema()
        self._load_matrix()

    def _init_schema(self) -> None:
        self._conn.executescript('''
            CREATE TABLE IF NOT EXISTS sku_group (
                sku        INTEGER PRIMARY KEY,
                lift_group INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS affinity (
                sku_i INTEGER NOT NULL,
                sku_j INTEGER NOT NULL,
                lift  REAL    NOT NULL,
                PRIMARY KEY (sku_i, sku_j)
            );
            CREATE INDEX IF NOT EXISTS idx_affinity_sku_i ON affinity(sku_i);
        ''')
        self._conn.commit()

    def _load_matrix(self) -> None:
        """Read the full affinity table into a CSR sparse matrix.

        Uses searchsorted for vectorised SKU→index mapping instead of a Python
        loop, so even a 10M-row table loads in a few seconds.  The matrix uses
        float32 to halve memory vs the float64 stored in SQLite (~60 MB for
        7.5M pairs).

        Note: load_for_skus writes new rows to SQLite but does NOT update this
        matrix.  That path is only used for online/legacy generation and is not
        called by the comparison scripts.
        """
        rows = self._conn.execute(
            'SELECT sku_i, sku_j, lift FROM affinity'
        ).fetchall()

        if not rows:
            self._sku_to_idx: dict[int, int] = {}
            self._matrix: csr_matrix | None = None
            return

        sku_i_list, sku_j_list, lift_list = zip(*rows)
        sku_i = np.asarray(sku_i_list, dtype=np.int32)
        sku_j = np.asarray(sku_j_list, dtype=np.int32)
        lift  = np.asarray(lift_list,  dtype=np.float32)

        all_skus = np.unique(np.concatenate([sku_i, sku_j]))
        self._sku_to_idx = {int(s): i for i, s in enumerate(all_skus)}

        row_idxs = np.searchsorted(all_skus, sku_i).astype(np.int32)
        col_idxs = np.searchsorted(all_skus, sku_j).astype(np.int32)
        self._matrix = csr_matrix(
            (lift, (row_idxs, col_idxs)),
            shape=(len(all_skus), len(all_skus)),
            dtype=np.float32,
        )
        mb = (self._matrix.data.nbytes + self._matrix.indices.nbytes +
              self._matrix.indptr.nbytes) / 1_048_576

    def index_inventory(self, inventory: Inventory) -> None:
        """Store sku → lift_group for every carton. Safe to call multiple times."""
        rows = [(c.sku, c.lift_group) for c in inventory.cartons]
        self._conn.executemany('INSERT OR IGNORE INTO sku_group VALUES (?,?)', rows)
        self._conn.commit()

    def load_for_skus(
        self,
        skus: Collection[int],
        min_lift: float = 1.5,
        max_lift: float = 5.0,
    ) -> AffMatrix:
        """Return a local AffMatrix dict for all within-group pairs in *skus*.

        Already-stored pairs are read from the DB.  Any missing within-group pair
        is generated (random.uniform), persisted, then included.  Cross-group pairs
        are absent; callers treat missing keys as 0.0.

        Note: new rows written here are NOT reflected in the in-memory CSR matrix.
        """
        if not skus:
            return {}

        sku_list = list(skus)
        ph = ','.join('?' * len(sku_list))

        group_rows = self._conn.execute(
            f'SELECT sku, lift_group FROM sku_group WHERE sku IN ({ph})',
            sku_list,
        ).fetchall()
        group_of: dict[int, int] = {sku: g for sku, g in group_rows}

        by_group: dict[int, list[int]] = defaultdict(list)
        for sku, g in group_of.items():
            by_group[g].append(sku)

        stored = self._conn.execute(
            f'SELECT sku_i, sku_j, lift FROM affinity WHERE sku_i IN ({ph})',
            sku_list,
        ).fetchall()
        result: AffMatrix = {(i, j): lift for i, j, lift in stored}

        new_rows: list[tuple[int, int, float]] = []
        for group_skus in by_group.values():
            for idx, sku_i in enumerate(group_skus):
                for sku_j in group_skus[idx + 1:]:
                    if (sku_i, sku_j) not in result:
                        lift_val = self._rng.uniform(min_lift, max_lift)
                        result[(sku_i, sku_j)] = lift_val
                        result[(sku_j, sku_i)] = lift_val
                        new_rows.extend([
                            (sku_i, sku_j, lift_val),
                            (sku_j, sku_i, lift_val),
                        ])

        if new_rows:
            self._conn.executemany(
                'INSERT OR IGNORE INTO affinity VALUES (?,?,?)', new_rows
            )
            self._conn.commit()

        return result

    def partners(self, sku: int) -> dict[int, float]:
        rows = self._conn.execute(
            'SELECT sku_j, lift FROM affinity WHERE sku_i = ?', (sku,)
        ).fetchall()
        return dict(rows)

    def delta_lift(self, sku: int, aisle_members: list[int]) -> float:
        """Sum of lift between sku and every member of aisle_members.

        Walks the CSR row for sku directly via indptr/indices/data — no SQL,
        no matrix construction.  Bounded by the number of affinity partners of
        sku (≤ group size, typically ~100), regardless of aisle_members length.
        """
        if not aisle_members or self._matrix is None or sku not in self._sku_to_idx:
            return 0.0
        i     = self._sku_to_idx[sku]
        start = int(self._matrix.indptr[i])
        end   = int(self._matrix.indptr[i + 1])
        if start == end:
            return 0.0
        col_indices = self._matrix.indices[start:end]
        data        = self._matrix.data[start:end]
        member_set  = {self._sku_to_idx[s] for s in aisle_members if s in self._sku_to_idx}
        if not member_set:
            return 0.0
        return float(sum(d for ci, d in zip(col_indices, data) if ci in member_set))

    def sum_lift(self, skus: list[int]) -> float:
        """Total pairwise lift for all ordered pairs within skus.

        Extracts a (k × k) submatrix from the CSR matrix and sums it in C.
        Both (i,j) and (j,i) are stored, so the result counts each undirected
        pair twice — consistent with the ordered-pair convention used throughout.
        """
        if len(skus) < 2 or self._matrix is None:
            return 0.0
        idxs = [self._sku_to_idx[s] for s in skus if s in self._sku_to_idx]
        if len(idxs) < 2:
            return 0.0
        idxs_arr = np.array(idxs, dtype=np.int32)
        return float(self._matrix[idxs_arr][:, idxs_arr].sum())

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AffinityStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
