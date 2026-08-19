from __future__ import annotations

import gc
import os
import random
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING, Collection

import numpy as np
from scipy.sparse import csr_matrix

from Schema import connect as _connect
from Schema import identity as _identity
from Schema import shape as _shape
from Warehouse.catalog.Inventory_Builder import AffMatrix

if TYPE_CHECKING:
    from Warehouse.catalog.Inventory_Builder import Inventory


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
        self._verify_shape(db_path)          # BEFORE the file is opened for writing
        self._conn = sqlite3.connect(db_path)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=-262144')    # 256 MB page cache
        self._conn.execute('PRAGMA temp_store=MEMORY')
        self._conn.execute('PRAGMA mmap_size=4294967296')  # 4 GB memory-mapped I/O
        self._rng = random.Random(seed)
        self._init_schema()
        self._load_matrix()

    #: Mirrors Warehouse/generation/generate_affinity.py `_SCHEMA`.  The generator owns the file;
    #: this exists so an in-memory store (the test/legacy path) still has tables to write into.
    _SCHEMA = '''
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
    '''
    _TABLES = ('sku_group', 'affinity')

    @classmethod
    def _verify_shape(cls, db_path: str) -> None:
        """Refuse a generator-written affinity.db whose tables are not the ones we query.

        HARD FAIL: every caller is a simulation about to place ~400k bins from these lifts, and
        `delta_lift`/`sum_lift` read the CSR matrix built here — a `lift` column that is not the
        `lift` column produces placements that look entirely reasonable and are not.

        Checked with `identity.check_tables`, not `identity.check`, and that is a deliberate
        scope limit: the `affinity_db` FAMILY is registered by
        `Warehouse/generation/generate_affinity.py`, which is a data-gen CLI importing matplotlib
        and pandas.  Importing it here to reach one dataclass would drag both into every
        ProcessPool worker.  Comparing the two tables this class actually reads against the DDL
        it already mirrors gives the same protection for the columns that matter, and makes the
        "both DDLs must stay in step" claim above self-enforcing.  That the mirror really does
        match the generator's declaration is asserted in
        `Tests/architecture/test_schema_identity.py`, where importing both is free.

        Skipped for `:memory:` and for a path this store is about to CREATE: there is no shape to
        disagree with yet, and `_init_schema` authors it below.  Skipped equally when a table is
        genuinely absent, which is the one case `_init_schema` still writes — verifying a shape
        we are in the middle of completing would reject the legacy/in-memory path it exists for.
        """
        if db_path == ':memory:' or not os.path.exists(db_path):
            return
        con = _connect.read_only(db_path)     # never a writer: opening must not change the shape
        try:
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        if not all(t in have for t in cls._TABLES):
            return
        _identity.check_tables(db_path, _shape.shape_of_ddl((cls._SCHEMA,)), cls._TABLES,
                               label=f'affinity_db({", ".join(cls._TABLES)})')

    def _init_schema(self) -> None:
        """Create the tables ONLY when something is actually missing.

        This used to run unconditionally, which meant opening a complete, generator-written
        `affinity.db` — even purely to read it — silently ADDED `sku_group` and changed the
        file's shape.  A database whose structure depends on whether it has been opened yet
        cannot be fingerprinted, and the mutation also drifted it away from what
        `context/artifacts.yml` declares.  The generator now declares `sku_group` too, so the
        common path finds everything present and touches nothing.
        """
        have = {r[0] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if all(t in have for t in self._TABLES):
            return
        self._conn.executescript(self._SCHEMA)
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
        # Stream into COUNT(*)-presized arrays instead of fetchall + zip(*rows): the
        # one-shot form materialized every row tuple and cell object simultaneously
        # (~1.4 GB transient / ~26M GC-tracked objects on a 14M-row production
        # affinity.db — the measured source of both the flat ~3.9 GiB worker peak and
        # the startup-dominated gen-2 pause).  Chunking bounds the transient to one
        # fetchmany window; each chunk's tuples die young, which gen-0 reclaims
        # without full-heap walks.  The per-column conversion inside a chunk is the
        # ORIGINAL path verbatim (zip + np.asarray with pinned dtypes), so the arrays
        # are bit-identical to the old load — enforced by the frozen oracle in
        # Tests/unit/test_affinity_load_equivalence.py.
        n_rows = self._conn.execute('SELECT COUNT(*) FROM affinity').fetchone()[0]

        if not n_rows:
            self._sku_to_idx: dict[int, int] = {}
            self._matrix: csr_matrix | None = None
            return

        sku_i = np.empty(n_rows, dtype=np.int32)
        sku_j = np.empty(n_rows, dtype=np.int32)
        lift  = np.empty(n_rows, dtype=np.float32)
        filled = 0
        cur = self._conn.execute('SELECT sku_i, sku_j, lift FROM affinity')
        # Cycle collector off for the fill: every object allocated here (row tuples of
        # ints/floats, the zip triples) is acyclic, so refcounting alone reclaims each
        # chunk — the collector contributes nothing but pauses (measured on the 14M-row
        # production file: 36k gen-0 + 150 gen-2 collections, ~5.3s, zero RSS effect).
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            while True:
                rows = cur.fetchmany(262_144)
                if not rows:
                    break
                sku_i_list, sku_j_list, lift_list = zip(*rows)
                end = filled + len(rows)
                sku_i[filled:end] = np.asarray(sku_i_list, dtype=np.int32)
                sku_j[filled:end] = np.asarray(sku_j_list, dtype=np.int32)
                lift[filled:end]  = np.asarray(lift_list,  dtype=np.float32)
                filled = end
        finally:
            if was_enabled:
                gc.enable()
        # Two-pass read (COUNT then scan): a mismatch means the table changed between
        # passes.  Hard-fail rather than hand a zero-padded tail to placement — every
        # caller is about to place ~400k bins from these lifts.
        assert filled == n_rows, (
            f'affinity row count moved during load: scanned {filled}, COUNT(*) said {n_rows}')

        all_skus = np.unique(np.concatenate([sku_i, sku_j]))
        self._sku_to_idx = {int(s): i for i, s in enumerate(all_skus)}

        row_idxs = np.searchsorted(all_skus, sku_i).astype(np.int32)
        col_idxs = np.searchsorted(all_skus, sku_j).astype(np.int32)
        self._matrix = csr_matrix(
            (lift, (row_idxs, col_idxs)),
            shape=(len(all_skus), len(all_skus)),
            dtype=np.float32,
        )

    def index_inventory(self, inventory: Inventory) -> None:
        """Store sku → lift_group for every order. Safe to call multiple times."""
        rows = [(c.sku, c.lift_group) for c in inventory.orders]
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
        """Association ABOVE independence between sku and aisle_members: Σ (lift − 1).

        Lift is a multiplier with 1 = independence, so the co-location *value* of a
        partner is (lift − 1); unstored pairs (lift = 1) contribute 0.  Walks the CSR
        row for sku directly — bounded by sku's partner count, regardless of
        aisle_members length.
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
        return float(sum(d - 1.0 for ci, d in zip(col_indices, data) if ci in member_set))

    def delta_lift_idxs(self, sku: int, member_idx_set: set[int]) -> float:
        """Association ABOVE independence: Σ (lift − 1) between sku and a pre-translated
        set of matrix indices.

        Index-set form of delta_lift() (no per-call {_sku_to_idx[s] ...} comprehension).
        Lift = 1 is independence ⇒ each stored partner contributes (lift − 1); unstored
        pairs contribute 0.  The caller keeps member_idx_set current with the aisle's SKU
        composition (add on placement, discard on removal).
        """
        if not member_idx_set or self._matrix is None or sku not in self._sku_to_idx:
            return 0.0
        i     = self._sku_to_idx[sku]
        start = int(self._matrix.indptr[i])
        end   = int(self._matrix.indptr[i + 1])
        if start == end:
            return 0.0
        col_indices = self._matrix.indices[start:end]
        data        = self._matrix.data[start:end]
        return float(sum(d - 1.0 for ci, d in zip(col_indices, data) if ci in member_idx_set))

    def delta_lift_sorted(self, sku: int, sorted_member_arr: 'np.ndarray') -> float:
        """Sum of lift between sku and members described by a sorted numpy array.

        Faster than delta_lift_idxs for large member sets: uses np.searchsorted
        on two pre-sorted arrays (CSR indices are sorted within each row by the
        scipy CSR format; sorted_member_arr is maintained by the caller).
        Zero per-call allocation when sorted_member_arr is cached.
        """
        if len(sorted_member_arr) == 0 or self._matrix is None or sku not in self._sku_to_idx:
            return 0.0
        i     = self._sku_to_idx[sku]
        start = int(self._matrix.indptr[i])
        end   = int(self._matrix.indptr[i + 1])
        if start == end:
            return 0.0
        col_indices = self._matrix.indices[start:end]  # sorted (CSR property)
        data        = self._matrix.data[start:end]
        n        = len(sorted_member_arr)
        pos      = np.searchsorted(sorted_member_arr, col_indices)
        pos_clip = np.minimum(pos, n - 1)
        in_aisle = (pos < n) & (sorted_member_arr[pos_clip] == col_indices)
        # Σ(lift − 1): subtract the count of matched partners (independence = 0).
        return float(data[in_aisle].sum()) - float(in_aisle.sum())

    def sum_lift(self, skus: list[int]) -> float:
        """Total association ABOVE independence within skus: Σ (lift − 1) over all stored
        ordered pairs.

        Extracts a (k × k) submatrix and sums it in C, then subtracts the stored-entry
        count (each stored pair contributes lift − 1; independence pairs contribute 0).
        Both (i,j) and (j,i) are stored, so each undirected pair is counted twice —
        consistent with the ordered-pair convention used throughout.
        """
        if len(skus) < 2 or self._matrix is None:
            return 0.0
        idxs = [self._sku_to_idx[s] for s in skus if s in self._sku_to_idx]
        if len(idxs) < 2:
            return 0.0
        idxs_arr = np.array(idxs, dtype=np.int32)
        sub = self._matrix[idxs_arr][:, idxs_arr]
        return float(sub.sum()) - float(sub.nnz)

    def close(self) -> None:
        """Close, folding the WAL back into affinity.db so no `-wal`/`-shm` is left beside it.

        `load_for_skus` and `index_inventory` both commit, so there is never an open
        transaction here for `connect.close` to lose — it checkpoints, it does not commit.

        Under the worker pool this file is SHARED: one affinity.db per inventory pair, opened
        by every arm running that pair at once.  The checkpoint then finds another connection
        on the file and skips instantly (the busy handler is disabled for it), so this costs
        nothing in the common case and truncates for whichever worker happens to close last.
        """
        _connect.close(self._conn)

    def __enter__(self) -> AffinityStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
