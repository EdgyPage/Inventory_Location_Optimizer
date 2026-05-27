from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING, Collection

from Inventory_Builder import AffMatrix

if TYPE_CHECKING:
    from Inventory_Builder import Inventory


class AffinityStore:
    """SQLite-backed affinity matrix with lazy on-demand pair generation.

    Avoids the O(N²/G) memory cost of pre-computing all pairwise lifts up front.
    Lift values are generated the first time a pair is queried and persisted so
    subsequent batches find them already cached.

    Typical workflow
    ----------------
    store = AffinityStore('affinity_cache.db', seed=0)
    store.index_inventory(inventory)   # O(N) — stores sku → lift_group

    # Inside the simulation loop, after Batch creation:
    aff = store.load_for_skus(list(batch.items.keys()))
    # aff is a plain dict; pass to sum_affinity(), true_load(), etc.

    The caller should pass the *selected* batch SKUs (~k items), not the full
    candidate pool, to keep each invocation O(k²/G) rather than O(N²/G).
    """

    def __init__(self, db_path: str = ':memory:', seed: int | None = None) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._rng = random.Random(seed)
        self._init_schema()

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
        """
        if not skus:
            return {}

        sku_list = list(skus)
        ph = ','.join('?' * len(sku_list))

        # Resolve group membership for the requested SKUs
        group_rows = self._conn.execute(
            f'SELECT sku, lift_group FROM sku_group WHERE sku IN ({ph})',
            sku_list,
        ).fetchall()
        group_of: dict[int, int] = {sku: g for sku, g in group_rows}

        by_group: dict[int, list[int]] = defaultdict(list)
        for sku, g in group_of.items():
            by_group[g].append(sku)

        # Fetch already-stored pairs — storing both directions means one query suffices
        stored = self._conn.execute(
            f'SELECT sku_i, sku_j, lift FROM affinity WHERE sku_i IN ({ph})',
            sku_list,
        ).fetchall()
        result: AffMatrix = {(i, j): lift for i, j, lift in stored}

        # Generate and cache any missing within-group pairs
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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AffinityStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
