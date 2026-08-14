"""cache_schema.py — DDL for the derived viewer sidecar (`_viz/**/<arm>.viz.db`).

Everything here is DERIVED. Deleting a sidecar costs time, never data; the viewer runs without
one, just slowly. It exists because four things are too slow to compute per request on a
production arm (384 aisles, 396,500 bins, 130,885 SKUs, 100 batches, ~1 GB sim DB):

| Table                | Replaces                                                           |
|----------------------|--------------------------------------------------------------------|
| `aisle_batch_rollup` | a ~0.7 s whole-warehouse state rebuild per pane per batch step, run |
|                      | only to count occupied bins per aisle                               |
| `sku_rank`           | a measured 24.4 s `GROUP BY sku` full scan of `picks`               |
| `sku_series`         | the same scan again, per batch                                      |
| `bin_span`           | a measured ~5-9 s scan for one bin's history — `bin_keyframe`'s PK  |
|                      | starts `(run_id, batch_id)`, so no per-aisle index is usable        |
| `final_home`         | the per-SKU destination map the convergence colouring is built on   |

**`bin_span` is built from the KEYFRAMES, never from `bin_inventory`.** That is what makes it
exact: the delta stream records picks and never restocks, so replaying it loses 59% of a real
warehouse within five batches (`RECONSTRUCTION.md` §1). The spans therefore live on the keyframe
grid — `kf_from`/`kf_to` are batch ids of keyframes, not arbitrary batches.

This module is DDL and nothing else, so `context/verify_context.py` can point a `schema_file` at
it and check every declared table actually exists.
"""
from __future__ import annotations

import os
import sqlite3

#: Bumped when a table's meaning changes in a way a stale sidecar would silently get wrong.
#: `precompute` rebuilds any cache whose stored version differs.
CACHE_VERSION = 1


# key/value provenance: which sources this was built from, and how, so staleness is detectable
# without re-reading a gigabyte.  Holds cache_version, sim_schema_id (the PIN — every archived
# run predates the stamp, so deriving once and recording it here is what stops the reader
# re-deriving on every request), run_id, keyframe_interval, top_n, final_home_batch, built_utc,
# built_secs, and st_size / st_mtime_ns for each of the three source DBs.
_CREATE_CACHE_META = """
    CREATE TABLE IF NOT EXISTS cache_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
"""

# One row per (bin, contiguous run of keyframes holding the same SKU).  A per-bin-per-batch table
# would be 396,500 x 100 = 39.65M rows and larger than the sim DB it caches; spans key on SKU
# CHANGE only, because qty churns every batch and would defeat the compression.  Exact qty at an
# arbitrary (batch, t) still comes from keyframe + picks, so nothing is lost.
_CREATE_BIN_SPAN = """
    CREATE TABLE IF NOT EXISTS bin_span (
        run_id       INTEGER NOT NULL,
        aisle_id     INTEGER NOT NULL,
        bayX         INTEGER NOT NULL,
        bayY         INTEGER NOT NULL,
        kf_from      INTEGER NOT NULL,   -- first keyframe batch holding this sku
        kf_to        INTEGER NOT NULL,   -- last keyframe batch holding it (inclusive)
        sku          INTEGER,            -- NULL = the bin was empty across this span
        qty_at_from  INTEGER NOT NULL,   -- qty at kf_from; exact qty elsewhere = kf + picks
        PRIMARY KEY (run_id, aisle_id, bayX, bayY, kf_from)
    ) WITHOUT ROWID
"""

# Whole-warehouse-at-a-keyframe reads scan by batch, which the PK (aisle-first) cannot serve.
# Covering, so the scan never touches the table itself.
_CREATE_BIN_SPAN_IDX = """
    CREATE INDEX IF NOT EXISTS ix_span_batch
        ON bin_span (run_id, kf_from, kf_to, aisle_id, bayX, bayY, sku, qty_at_from)
"""

# EVERY sku, not just the top N — so the reader can answer "sku 91234 is rank 3,201, outside the
# cached series window" instead of returning an empty series that reads as "never picked".
_CREATE_SKU_RANK = """
    CREATE TABLE IF NOT EXISTS sku_rank (
        run_id      INTEGER NOT NULL,
        sku         INTEGER NOT NULL,
        rank        INTEGER NOT NULL,   -- 1 = most units picked
        picks       INTEGER NOT NULL,
        units       INTEGER NOT NULL,
        first_batch INTEGER NOT NULL,
        last_batch  INTEGER NOT NULL,
        PRIMARY KEY (run_id, sku)
    )
"""
_CREATE_SKU_RANK_IDX = """
    CREATE INDEX IF NOT EXISTS ix_sku_rank ON sku_rank (run_id, rank)
"""

# Top-N only: all 130,885 SKUs x 100 batches would be ~13M rows (~600 MB) and blow the budget for
# a series nothing can plot.  N is recorded in cache_meta so the reader knows the window.
_CREATE_SKU_SERIES = """
    CREATE TABLE IF NOT EXISTS sku_series (
        run_id   INTEGER NOT NULL,
        sku      INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        picks    INTEGER NOT NULL,
        units    INTEGER NOT NULL,
        PRIMARY KEY (run_id, sku, batch_id)
    ) WITHOUT ROWID
"""

# The colour authority.  `home_aisles` is a comma-separated aisle list, not a single bin, because
# 64.3% of SKUs hold several bins at the last keyframe and only 9.7% of those keep every replica
# in one aisle (median span 2, max 8).  Scoring "is this item home?" against one bin would paint
# the majority of correctly-placed replicas as misplaced.  aisle_id/bayX/bayY is the PRIMARY home
# (largest qty, ties by lowest coordinate) and drives hue + lightness; home_aisles drives chroma.
_CREATE_FINAL_HOME = """
    CREATE TABLE IF NOT EXISTS final_home (
        run_id      INTEGER NOT NULL,
        sku         INTEGER NOT NULL,
        aisle_id    INTEGER NOT NULL,   -- primary home
        bayX        INTEGER NOT NULL,
        bayY        INTEGER NOT NULL,
        qty         INTEGER NOT NULL,
        n_homes     INTEGER NOT NULL,   -- bins held at the final keyframe; 1 = unambiguous
        home_aisles TEXT    NOT NULL,   -- comma-separated distinct aisle ids
        batch_id    INTEGER NOT NULL,   -- WHICH keyframe "final" means; never left implicit
        PRIMARY KEY (run_id, sku)
    ) WITHOUT ROWID
"""

# 384 aisles x 100 batches = 38,400 rows.  `capacity` is denormalised because warehouse.db is
# shared by all 34 arms and may be renamed or archived away from them.
_CREATE_AISLE_ROLLUP = """
    CREATE TABLE IF NOT EXISTS aisle_batch_rollup (
        run_id       INTEGER NOT NULL,
        batch_id     INTEGER NOT NULL,
        aisle_id     INTEGER NOT NULL,
        occupied     INTEGER NOT NULL,
        capacity     INTEGER NOT NULL,
        qty          INTEGER NOT NULL,
        n_skus       INTEGER NOT NULL,
        picks        INTEGER NOT NULL,
        units_picked INTEGER NOT NULL,
        visits       INTEGER NOT NULL,   -- distinct tasks entering this aisle
        task_secs    REAL    NOT NULL,
        home_match   INTEGER NOT NULL,   -- occupied bins whose sku counts this aisle as a home
        PRIMARY KEY (run_id, batch_id, aisle_id)
    ) WITHOUT ROWID
"""
_CREATE_AISLE_ROLLUP_IDX = """
    CREATE INDEX IF NOT EXISTS ix_rollup_aisle
        ON aisle_batch_rollup (run_id, aisle_id, batch_id)
"""

_ALL = (_CREATE_CACHE_META,
        _CREATE_BIN_SPAN, _CREATE_BIN_SPAN_IDX,
        _CREATE_SKU_RANK, _CREATE_SKU_RANK_IDX,
        _CREATE_SKU_SERIES,
        _CREATE_FINAL_HOME,
        _CREATE_AISLE_ROLLUP, _CREATE_AISLE_ROLLUP_IDX)

TABLES = ('cache_meta', 'bin_span', 'sku_rank', 'sku_series', 'final_home',
          'aisle_batch_rollup')


def source_stamps(sim_db: str, keyframe_db: str, warehouse_db: str) -> dict:
    """Size + mtime of every source, so a rebuilt sim DB invalidates the cache.

    `st_mtime_ns` rather than `st_mtime`: the float form cannot be compared with `==` (repo rule)
    and the integer form is exact. Size AND mtime, because some volumes carry coarse timestamps
    and a rewrite inside the granularity window would otherwise be invisible.

    An absent source records `<absent>` rather than being omitted — otherwise DELETING the
    keyframe DB after a build would leave the cache reporting `fresh`.
    """
    out = {}
    for label, path in (('sim', sim_db), ('kf', keyframe_db), ('wh', warehouse_db)):
        if path and os.path.exists(path):
            st = os.stat(path)
            out[f'{label}_db_size'] = str(st.st_size)
            out[f'{label}_db_mtime_ns'] = str(st.st_mtime_ns)
        else:
            out[f'{label}_db_size'] = '<absent>'
            out[f'{label}_db_mtime_ns'] = '<absent>'
    return out


def cache_freshness(viz_cache: str, sim_db: str, keyframe_db: str, warehouse_db: str) -> str:
    """'fresh' | 'stale' | 'absent' | 'partial'.

    Lives here rather than in `precompute` so the READ path can call it without importing the
    builder (and without `readers -> precompute -> db_reader` becoming an import cycle).
    """
    if not viz_cache or not os.path.exists(viz_cache):
        return 'absent'
    try:
        con = sqlite3.connect(f'file:{viz_cache.replace(os.sep, "/")}?mode=ro', uri=True)
        try:
            meta = {k: v for k, v in con.execute('SELECT key, value FROM cache_meta')}
        finally:
            con.close()
    except sqlite3.Error:                      # truncated / not a database / no such table
        return 'partial'
    if not meta.get('built_utc'):
        return 'partial'                       # a build that died before finishing
    if meta.get('cache_version') != str(CACHE_VERSION):
        return 'stale'
    want = source_stamps(sim_db, keyframe_db, warehouse_db)
    return 'fresh' if all(meta.get(k) == v for k, v in want.items()) else 'stale'


def init_cache_db(path: str) -> sqlite3.Connection:
    """Create the sidecar and return an OPEN connection tuned for bulk writing.

    The pragmas are safe precisely because this file is derived: a crash mid-build leaves a
    cache with no `built_utc`, which `precompute` treats as absent and rebuilds.
    """
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.execute('PRAGMA temp_store=MEMORY')
    con.execute('PRAGMA cache_size=-262144')          # 256 MB page cache
    for stmt in _ALL:
        con.execute(stmt)
    return con
