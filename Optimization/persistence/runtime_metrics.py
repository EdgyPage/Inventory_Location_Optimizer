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
#: exactly one absent table, the stamp itself: `runtime`'s twenty columns are identical, so
#: `load_rows` returns the same dicts from either shape.  The 2026-08-13 runs already match the
#: current declaration, which is what makes this a two-entry list and not a growing one.
PRE_STAMP_RUNTIME_SCHEMA_ID = 'a683d2d07c72'

RUNTIME_DB_FAMILY = _identity.register(_identity.Family(
    name='runtime_metrics_db',
    declared_shape=declared_runtime_shape,
    meta_table='schema_meta',
    known_ids=(PRE_STAMP_RUNTIME_SCHEMA_ID,),
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
        _identity.stamp(con, RUNTIME_DB_FAMILY)
        con.execute(
            'INSERT OR REPLACE INTO runtime '
            '(cell,pair,config,channel,arm,initial,assignment,n_bins,regime_bins,n_aisles,'
            'batches,total_s,rate,reord_s,build_s,pre_s,sim_s,extract_s,inv_s,save_s) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cell or '', pair, config, channel, arm, initial, assignment,
             int(res.get('n_bins', 0) or 0), int(res.get('regime_bins', 0) or 0),
             int(res.get('n_aisles', 0) or 0), batches, total,
             (batches / total if total > 0 else 0.0),
             float(res.get('t_reord', 0.0) or 0.0), float(res.get('t_build', 0.0) or 0.0),
             float(res.get('t_pre', 0.0) or 0.0), float(res.get('t_sim', 0.0) or 0.0),
             float(res.get('t_extract', 0.0) or 0.0), float(res.get('t_inv', 0.0) or 0.0),
             float(res.get('t_save', 0.0) or 0.0)))
        con.commit()                     # connect.close checkpoints, it does not commit
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
