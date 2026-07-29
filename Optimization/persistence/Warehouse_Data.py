"""
Warehouse_Data.py — SQLite persistence for warehouse sizing stats and aisle distributions.

Written once per inventory pair (shared across A/B/C strategies) to
pair_dir/warehouse.db.  Separate from the per-strategy sim_*.db files.
"""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone


def compute_warehouse_fingerprint(aisle_rows: list[dict], inventory_label: str = '') -> str:
    """Stable hash of a warehouse's geometry + inventory profile.

    Rename-proof identity: stored in warehouse_stats and in every sim run row so a run
    can be matched to its warehouse.db regardless of folder/file names.  Keyed on the
    per-aisle layout (id, handling, category, unit_type, size, bays) which uniquely
    fixes the geometry the picker_events/aisle_ids were recorded against."""
    parts = [str(inventory_label)]
    for r in sorted(aisle_rows, key=lambda r: r['aisle_id']):
        parts.append('{aisle_id}:{handling_type}:{category}:{unit_type}:{storage_size}:'
                     '{bay_x}x{bay_y}'.format(**r))
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:16]


# ── schemas ────────────────────────────────────────────────────────────────────

_CREATE_WAREHOUSE_STATS = """
    CREATE TABLE IF NOT EXISTS warehouse_stats (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        inventory_db        TEXT    NOT NULL,
        timestamp           TEXT    NOT NULL,
        n_skus              INTEGER NOT NULL,
        n_pallet_units      INTEGER NOT NULL,
        n_singleton_units   INTEGER NOT NULL,
        total_aisles        INTEGER NOT NULL,
        total_bins          INTEGER NOT NULL,
        expected_fill       REAL    NOT NULL,
        target_fill         REAL    NOT NULL,
        max_aisles_cap      INTEGER,
        max_bins_cap        INTEGER,
        avg_equilibrium_qty REAL    NOT NULL DEFAULT 0.0,
        avg_reorder_point   REAL    NOT NULL DEFAULT 0.0,
        warehouse_fingerprint TEXT
    )
"""

_CREATE_AISLE_TYPE_STATS = """
    CREATE TABLE IF NOT EXISTS aisle_type_stats (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        warehouse_id       INTEGER NOT NULL REFERENCES warehouse_stats(id),
        handling_type      TEXT    NOT NULL,
        category           TEXT    NOT NULL,
        unit_type          TEXT    NOT NULL,
        replica_count      INTEGER NOT NULL,
        eff_bins_per_aisle INTEGER NOT NULL,
        total_bins         INTEGER NOT NULL,
        size_small_pct     REAL    NOT NULL DEFAULT 0.0,
        size_medium_pct    REAL    NOT NULL DEFAULT 0.0,
        size_large_pct     REAL    NOT NULL DEFAULT 0.0,
        size_xlarge_pct    REAL    NOT NULL DEFAULT 0.0
    )
"""

_CREATE_AISLE_IDX = """
    CREATE INDEX IF NOT EXISTS ix_at_warehouse_id
    ON aisle_type_stats (warehouse_id)
"""

# Per-aisle physical layout — one row per aisle in the built warehouse.  Gives a
# visualizer the full geometry (incl. empty bins, generated from bay_x × bay_y)
# without a row per bin, and is the source of aisle_id → unit_type/handling for
# DB-only analysis.
_CREATE_AISLE_LAYOUT = """
    CREATE TABLE IF NOT EXISTS aisle_layout (
        aisle_id      INTEGER PRIMARY KEY,
        handling_type TEXT    NOT NULL,
        category      TEXT    NOT NULL,
        unit_type     TEXT    NOT NULL,
        storage_size  TEXT    NOT NULL,
        bay_x         INTEGER NOT NULL,
        bay_y         INTEGER NOT NULL
    )
"""


# ── helpers ────────────────────────────────────────────────────────────────────

def _open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    return con


# ── public API ─────────────────────────────────────────────────────────────────

def init_warehouse_db(path: str) -> None:
    """Create warehouse_stats, aisle_type_stats, and aisle_layout tables."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = _open_db(path)
    try:
        con.execute(_CREATE_WAREHOUSE_STATS)
        con.execute(_CREATE_AISLE_TYPE_STATS)
        con.execute(_CREATE_AISLE_IDX)
        con.execute(_CREATE_AISLE_LAYOUT)
        con.commit()
    finally:
        con.close()


def save_aisle_layout(path: str, rows: list[dict]) -> None:
    """Replace aisle_layout with one row per aisle of the built warehouse.

    rows: dicts with keys aisle_id, handling_type, category, unit_type,
    storage_size, bay_x, bay_y.  Rewrite-fresh so it always reflects the latest
    plan/warehouse for this pair.
    """
    con = _open_db(path)
    try:
        con.execute(_CREATE_AISLE_LAYOUT)
        con.execute('DELETE FROM aisle_layout')
        con.executemany(
            'INSERT OR REPLACE INTO aisle_layout '
            '(aisle_id, handling_type, category, unit_type, storage_size, bay_x, bay_y) '
            'VALUES (?,?,?,?,?,?,?)',
            [(r['aisle_id'], r['handling_type'], r['category'], r['unit_type'],
              r['storage_size'], r['bay_x'], r['bay_y']) for r in rows],
        )
        con.commit()
    finally:
        con.close()


def save_warehouse_stats(
    path          : str,
    inventory_db  : str,
    n_skus        : int,
    n_pallet      : int,
    n_singleton   : int,
    total_aisles  : int,
    total_bins    : int,
    expected_fill : float,
    target_fill   : float,
    max_aisles    : int | None,
    max_bins      : int | None,
    avg_eq_qty    : float,
    avg_rp        : float,
    aisle_rows    : list[dict],
    warehouse_fingerprint : str | None = None,
) -> int:
    """Insert one warehouse_stats row + one aisle_type_stats row per aisle type.

    aisle_rows: list of dicts with keys
        handling_type, category, unit_type, replica_count, eff_bins_per_aisle,
        total_bins, size_small_pct, size_medium_pct, size_large_pct, size_xlarge_pct

    Returns the warehouse_stats.id of the inserted row.
    """
    ts = datetime.now(timezone.utc).isoformat()
    con = _open_db(path)
    try:
        cur = con.execute(
            'INSERT INTO warehouse_stats '
            '(inventory_db, timestamp, n_skus, n_pallet_units, n_singleton_units, '
            'total_aisles, total_bins, expected_fill, target_fill, '
            'max_aisles_cap, max_bins_cap, avg_equilibrium_qty, avg_reorder_point, '
            'warehouse_fingerprint) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (inventory_db, ts, n_skus, n_pallet, n_singleton,
             total_aisles, total_bins, expected_fill, target_fill,
             max_aisles, max_bins, avg_eq_qty, avg_rp, warehouse_fingerprint),
        )
        warehouse_id = cur.lastrowid

        con.executemany(
            'INSERT INTO aisle_type_stats '
            '(warehouse_id, handling_type, category, unit_type, '
            'replica_count, eff_bins_per_aisle, total_bins, '
            'size_small_pct, size_medium_pct, size_large_pct, size_xlarge_pct) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            [
                (warehouse_id,
                 r['handling_type'], r['category'], r['unit_type'],
                 r['replica_count'], r['eff_bins_per_aisle'], r['total_bins'],
                 r['size_small_pct'], r['size_medium_pct'],
                 r['size_large_pct'], r['size_xlarge_pct'])
                for r in aisle_rows
            ],
        )
        con.commit()
        return warehouse_id  # type: ignore[return-value]
    finally:
        con.close()
