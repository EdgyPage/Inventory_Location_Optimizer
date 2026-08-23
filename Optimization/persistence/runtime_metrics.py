"""runtime_metrics.py — per-arm RUNTIME (wall-clock compute cost) capture, written by the PARENT.

Each worker RETURNS its whole-arm wall time + per-section totals (reorder / batch-build / pre /
sim / extract / inv / DB-save) + warehouse identity; the supervisor (parent process) inserts one
row per arm into ``<run_root>/runtime_metrics.db``.  Writing from the single parent avoids SQLite
write contention under the flat pool, and puts the DB at the highest run dir so it spans every cell.

This is RUNTIME (how long an arm took to COMPUTE) — orthogonal to the sim-modeled batch_stats
durations.  The runtime graphs (Optimization/run_runtime_graphs.py, part of the analysis hub) read
it to rank the slowest arms / assignment-fns / warehouses / cells and show WHERE the time goes, so
recurring hot-paths (e.g. a reorder/reslot-dominated arm — the valid-aisle recompute suspicion) are
visible.
"""
from __future__ import annotations

import os
import sqlite3

from Schema import compat as _compat
from Schema import identity as _identity
from Schema import connect as _connect
from Schema import shape as _shape

RUNTIME_DB = 'runtime_metrics.db'

_DDL = """
CREATE TABLE IF NOT EXISTS runtime (
    cell        TEXT    NOT NULL,
    pair        TEXT    NOT NULL,
    config      TEXT    NOT NULL,
    channel     TEXT    NOT NULL,
    arm         TEXT    NOT NULL,
    initial     TEXT    NOT NULL,
    assignment  TEXT    NOT NULL,
    n_bins      INTEGER NOT NULL DEFAULT 0,
    regime_bins INTEGER NOT NULL DEFAULT 0,
    n_aisles    INTEGER NOT NULL DEFAULT 0,
    batches     INTEGER NOT NULL DEFAULT 0,
    total_s     REAL    NOT NULL DEFAULT 0,
    rate        REAL    NOT NULL DEFAULT 0,
    reord_s     REAL    NOT NULL DEFAULT 0,
    build_s     REAL    NOT NULL DEFAULT 0,
    pre_s       REAL    NOT NULL DEFAULT 0,
    sim_s       REAL    NOT NULL DEFAULT 0,
    extract_s   REAL    NOT NULL DEFAULT 0,
    inv_s       REAL    NOT NULL DEFAULT 0,
    save_s      REAL    NOT NULL DEFAULT 0,
    smpl_s      REAL    NOT NULL DEFAULT 0,
    task_s      REAL    NOT NULL DEFAULT 0,
    kf_s        REAL    NOT NULL DEFAULT 0,
    p1_s        REAL    NOT NULL DEFAULT 0,
    p2_s        REAL    NOT NULL DEFAULT 0,
    gc_pause_s  REAL    NOT NULL DEFAULT 0,
    gc_gen2     INTEGER NOT NULL DEFAULT 0,
    peak_rss_mib REAL,
    live_objects INTEGER,
    -- Measured OUTSIDE the section partition below; see OUTSIDE_TOTAL.  All three are
    -- NULLABLE on purpose: NULL means "not measured", 0 would mean "measured as zero",
    -- and telling those apart is the entire job of `precomp_src`.
    precomp_s   REAL,
    precomp_src TEXT,
    map_lap_pct REAL,
    UNIQUE(cell, pair, config, channel, arm)
)
"""

# The named sections a runtime row breaks down into (column, human label) — the stacked-breakdown
# order.  Kept here so the DB and the graphs agree on the section set.
# ── Schema identity ───────────────────────────────────────────────────────────
# Registered here, in the writer, so `Schema/` stays a stdlib-only leaf importing no writer.
# The declared shape is BUILT from the same DDL `record_arm` executes, never hand-listed.

_ALL_DDL = (_DDL, _identity.meta_ddl('schema_meta'))


def declared_runtime_shape() -> dict:
    con = sqlite3.connect(':memory:')
    try:
        for stmt in _ALL_DDL:
            con.execute(stmt)
        return _shape.canonical_shape(con)
    finally:
        con.close()


#: The shape every archived runtime_metrics.db carried before `schema_meta` was declared — the
#: 2026-07-29 vintage (`comparison_whatif_20260729_115451` and `_125755`; no earlier run wrote
#: this DB at all).  DERIVED from those files, never chosen.  It differs from the declaration by
#: exactly one absent table, the stamp itself: that vintage's twenty `runtime` columns read
#: identically, so `load_rows` returns the same dicts from any vetted shape.  Four vintages
#: now: pre-stamp, the 20-column stamped era, the 29-column observability era (what the
#: published Experiment-8 run carries), and the current 32-column declaration.
PRE_STAMP_RUNTIME_SCHEMA_ID = 'a683d2d07c72'

RUNTIME_DB_FAMILY = _identity.register(_identity.Family(
    name='runtime_metrics_db',
    declared_shape=declared_runtime_shape,
    meta_table='schema_meta',
    known_ids=('397b7e750e1d',  # 29-column observability era: d5bdedc..3b712fb (2026-08-19
                                #   through 2026-08-23) — the shape EXPERIMENT 8's run carries,
                                #   superseded by the setup-phase columns (precomp_s/_src,
                                #   map_lap_pct), which the batch loop's clock never covered
              'c033ff9c85a5',  # 20-column stamped era: c07b975..f59f38e (2026-08-13 runs
                                #   through the 2026-08-19 deep/RSS ladders), superseded by
                                #   the observability columns (smpl/task/kf/p1/p2/gc/rss)
              PRE_STAMP_RUNTIME_SCHEMA_ID,),
))


SECTIONS = [
    ('reord_s',   'reorder'),
    ('build_s',   'batch-build'),
    ('pre_s',     'pre-snapshot'),
    ('sim_s',     'sim'),
    ('extract_s', 'extract'),
    # `inv_s` predates the bin-mutation log, when this section wrote the bin_inventory
    # snapshot.  That table is gone; the section is now the per-batch conservation ledger.
    # The COLUMN keeps its name so archived rows stay readable (and so relabelling does not
    # move the runtime_metrics schema id); only the human label tracks what it measures.
    ('inv_s',     'bin-accounting'),
    ('save_s',    'DB-save'),
    # NOT in this list, deliberately: smpl_s/task_s (sub-splits of build_s), kf_s (a
    # sub-span of pre_s), p1_s/p2_s (a split of sim_s), gc_pause_s (overlaps every
    # section).  This list is a PARTITION for the stacked graph — adding an overlay
    # column here double-counts its seconds.  Query the columns directly instead.
]

#: Spans measured OUTSIDE `total_s` — the batch loop's clock starts after setup, so the
#: map precompute is in neither `total_s` nor any SECTION.  Kept as its own list for the
#: same reason SECTIONS excludes its overlays, and the failure mode is the mirror image:
#: SECTIONS must not gain an overlay (double-counts), and this must never be folded INTO
#: SECTIONS (stacks a span onto a total that does not contain it).  An arm's real cost is
#: the SUM of the two lists, and any chart showing it says so.
OUTSIDE_TOTAL = [
    ('precomp_s', 'map precompute (setup, once per arm)'),
]


def runtime_db_path(run_root: str) -> str:
    return os.path.join(run_root, RUNTIME_DB)


def _parse_arm(arm: str):
    """'uni_rank_labor_norsl' -> ('uni', 'rank_labor'); assignment may contain '_'."""
    body = arm[:-len('_norsl')] if arm.endswith('_norsl') else arm
    initial, _, assignment = body.partition('_')
    return initial, assignment


def record_arm(run_root: str, cell: str, uid, res: dict) -> None:
    """Insert one arm's runtime row (idempotent by (cell,pair,config,channel,arm) — a re-run/resume
    of the arm overwrites its row).  uid = (pair, config, channel, arm); res = the worker return dict.
    Best-effort: callers wrap this so a runtime-DB hiccup never sinks a real run."""
    pair, config, channel, arm = uid
    initial, assignment = _parse_arm(arm)
    total   = float(res.get('elapsed', 0.0) or 0.0)
    batches = int(res.get('done', 0) or 0)
    con = sqlite3.connect(runtime_db_path(run_root))
    try:
        for stmt in _ALL_DDL:
            con.execute(stmt)
        # Stamp + verify the store: worker-safe (warn-once) — record_arm runs per arm, deep
        # inside a sweep; a store gap must nag, not kill the arm.
        _compat.stamp_checked(con, RUNTIME_DB_FAMILY, strict=False)
        con.execute(
            'INSERT OR REPLACE INTO runtime '
            '(cell,pair,config,channel,arm,initial,assignment,n_bins,regime_bins,n_aisles,'
            'batches,total_s,rate,reord_s,build_s,pre_s,sim_s,extract_s,inv_s,save_s,'
            'smpl_s,task_s,kf_s,p1_s,p2_s,gc_pause_s,gc_gen2,peak_rss_mib,live_objects,'
            'precomp_s,precomp_src,map_lap_pct) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cell or '', pair, config, channel, arm, initial, assignment,
             int(res.get('n_bins', 0) or 0), int(res.get('regime_bins', 0) or 0),
             int(res.get('n_aisles', 0) or 0), batches, total,
             (batches / total if total > 0 else 0.0),
             float(res.get('t_reord', 0.0) or 0.0), float(res.get('t_build', 0.0) or 0.0),
             float(res.get('t_pre', 0.0) or 0.0), float(res.get('t_sim', 0.0) or 0.0),
             float(res.get('t_extract', 0.0) or 0.0), float(res.get('t_inv', 0.0) or 0.0),
             float(res.get('t_save', 0.0) or 0.0),
             # finer splits + memory observability (2026-08-19 column add; every read is a
             # .get so a crashed/legacy result dict writes zeros/NULLs, never raises)
             float(res.get('t_sample', 0.0) or 0.0), float(res.get('t_task', 0.0) or 0.0),
             float(res.get('t_kf', 0.0) or 0.0),
             float(res.get('p1_s', 0.0) or 0.0), float(res.get('p2_s', 0.0) or 0.0),
             float(res.get('gc_pause_s', 0.0) or 0.0), int(res.get('gc_gen2', 0) or 0),
             res.get('peak_rss_mib'), res.get('live_objects'),
             # Setup-phase spans (2026-08-23 column add).  NOT coerced to 0.0 like the
             # section columns above: a worker that did not measure the precompute must
             # write NULL, or the analysis cannot tell "no map to build" from "0.0 s".
             res.get('t_precompute'),
             ('inline' if res.get('t_precompute') is not None else None),
             res.get('map_lap_pct')))
        con.commit()                     # connect.close checkpoints, it does not commit
    finally:
        _connect.close(con)


#: The setup-phase columns, in DDL order — the ones a pre-2026-08-23 DB predates.
_SETUP_COLUMNS = (('precomp_s', 'REAL'), ('precomp_src', 'TEXT'), ('map_lap_pct', 'REAL'))


def _migrate_setup_columns(con) -> bool:
    """Add the setup-phase columns to a DB written before they existed; True if it changed.

    `CREATE TABLE IF NOT EXISTS` cannot widen an existing table, so a finished run whose
    arms predate these columns needs an explicit ALTER before a backfill can write to it.
    Every added column is NULLABLE with no default, so existing rows read NULL — "never
    measured", which is exactly what they are.

    Re-stamping afterwards is not optional: the file's recorded schema id describes its
    old shape, and leaving the two disagreeing turns every later read into an unvetted
    warning.  A migrated run legitimately IS the current shape.
    """
    have = {r[1] for r in con.execute('PRAGMA table_info(runtime)')}
    added = [c for c, _t in _SETUP_COLUMNS if c not in have]
    for col, typ in _SETUP_COLUMNS:
        if col not in have:
            con.execute(f'ALTER TABLE runtime ADD COLUMN {col} {typ}')
    if added:
        _compat.stamp_checked(con, RUNTIME_DB_FAMILY, strict=False)
        con.commit()
    return bool(added)


def record_precompute(run_root: str, cell: str, uid, seconds: float,
                      source: str, map_lap_pct=None) -> bool:
    """Fill an existing arm's setup-phase measurement; True when a row was updated.

    The inline path (`record_arm`) writes these at simulation time.  This is the other
    door: a finished run whose arms predate the columns can be measured afterwards by
    rebuilding the arm's warehouse and timing the precompute alone, without re-simulating
    hundreds of GB.  `source` separates the two so nothing downstream has to guess —
    'inline' seconds are contended against the sweep's whole worker pool, a 'backfill'
    measurement is not, and the two are not comparable as ratios.

    Updates only; a missing row means the arm never ran, and inventing one here would put
    a measurement in the table with no simulation behind it.
    """
    if source not in ('inline', 'backfill'):
        raise ValueError(f"source must be 'inline' or 'backfill', got {source!r}")
    pair, config, channel, arm = uid
    con = sqlite3.connect(runtime_db_path(run_root))
    try:
        _migrate_setup_columns(con)
        cur = con.execute(
            'UPDATE runtime SET precomp_s=?, precomp_src=?, map_lap_pct=? '
            'WHERE cell=? AND pair=? AND config=? AND channel=? AND arm=?',
            (float(seconds), source, map_lap_pct, cell or '', pair, config, channel, arm))
        con.commit()
        return cur.rowcount > 0
    finally:
        _connect.close(con)


def load_rows(run_root: str) -> list[dict]:
    """All runtime rows as dicts (empty if the DB is absent) — for the runtime graphs.

    WARNS rather than raising on an unvetted shape, unlike the sim-DB check in
    `Performance_Evaluations/core/context.py`.  The difference is what the number is FOR: these
    rows are wall-clock diagnostics about how long an arm took to compute, never a published
    result, and this is a `SELECT *` into dicts that the graphs index defensively.  A runtime DB
    from a shape we have not seen is worth a named line in the log; it is not worth sinking the
    analysis of a sweep that has already finished computing.
    """
    path = runtime_db_path(run_root)
    if not os.path.exists(path):
        return []
    _identity.check_or_warn(path, 'runtime_metrics_db', verify=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute('SELECT * FROM runtime')]
    finally:
        con.close()
