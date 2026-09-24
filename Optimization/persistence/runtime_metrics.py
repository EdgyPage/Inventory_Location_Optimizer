"""runtime_metrics.py — per-arm RUNTIME (wall-clock compute cost) capture, written by the PARENT.

Each worker RETURNS its whole-arm wall time + per-section totals (reorder / batch-build / pre /
sim / extract / inv / DB-save) + warehouse identity; the supervisor (parent process) inserts one
row per arm into ``<run_root>/runtime_metrics.db``.  Writing from the single parent avoids SQLite
write contention under the flat pool, and puts the DB at the highest run dir so it spans every cell.

This is RUNTIME (how long an arm took to COMPUTE) — orthogonal to the sim-modeled batch_stats
durations.  The `cost` chart family (Performance_Evaluations/cost/, a RUN-scope evaluation) reads it
to rank rules by what they cost to run and to show WHERE the time goes, so recurring hot-paths (e.g.
a reorder/reslot-dominated arm — the valid-aisle recompute suspicion) stay visible — and so a WMS
reader can be told what scoring one arriving unit costs.

ON A COUPLED UNIT (two channel leaves through one batch loop, `--couple-channels`) each leaf
writes its own row, and two things about those rows are decided rather than incidental.
`total_s` is the UNIT loop's wall on both rows -- a leaf's clock starts before the loop and
stops after it, so it includes the sibling's work.  `reord_s` on each row carries the SITE
drive (one receive, one put drain, for both leaves) in full, charged by the driver to every
leaf once per batch: the reading a one-leaf site-docked unit has always given.  So a leaf's
`reord_s` includes the site's work, and summing the two leaves' `reord_s` counts the drive
twice.  Until 2026-09-18 the leaves' timer laps were left open across the drive and across
each other instead, which charged the drive to both AND the first leaf's entire step to the
second leaf's `reord_s`; every coupled `reord_s` written before that date is inflated by the
sibling's step (`Tests/e2e/test_coupled_unit_e2e.py`, the lap-privacy tests).
"""
from __future__ import annotations

import os
import sqlite3
from typing import NamedTuple

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
    -- THE INBOUND CARVE (2026-09-24, `Warehouse.kernel.perf_probe`): overlay spans of
    -- reord_s and the inbound counters.  NULLABLE -- NULL is "not measured" (a vintage
    -- before the probe, or a legacy result dict), never a fabricated zero.
    inb_pre_s     REAL,
    inb_freeze_s  REAL,
    inb_pack_s    REAL,
    inb_yplan_s   REAL,
    inb_dplan_s   REAL,
    inb_unload_s  REAL,
    inb_handoff_s REAL,
    put_s         REAL,
    put_open_s    REAL,
    inb_drains    INTEGER,
    yard_T_sum    INTEGER,
    yard_T_max    INTEGER,
    yard_pulls    INTEGER,
    plan_rounds   INTEGER,
    plan_places   INTEGER,
    put_opens     INTEGER,
    put_units     INTEGER,
    -- Setup, measured OUTSIDE total_s (OUTSIDE_TOTAL): worker entry to this leaf's loop
    -- clock, and the sibling leaf's setup that sits inside this leaf's total_s.
    startup_s     REAL,
    sib_setup_s   REAL,
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
    known_ids=('cd91e75b27ba',  # 32-column setup-phase era: 2026-08-23 through 2026-09-24,
                                #   superseded by the inbound carve (inb_*/put* spans, the
                                #   inbound counters, startup_s/sib_setup_s) -- every 400k
                                #   campaign and churn-probe root before it carries this one
              '397b7e750e1d',  # 29-column observability era: d5bdedc..3b712fb (2026-08-19
                                #   through 2026-08-23) — the shape EXPERIMENT 8's run carries,
                                #   superseded by the setup-phase columns (precomp_s/_src,
                                #   map_lap_pct), which the batch loop's clock never covered
              'c033ff9c85a5',  # 20-column stamped era: c07b975..f59f38e (2026-08-13 runs
                                #   through the 2026-08-19 deep/RSS ladders), superseded by
                                #   the observability columns (smpl/task/kf/p1/p2/gc/rss)
              PRE_STAMP_RUNTIME_SCHEMA_ID,),
))


class Span(NamedTuple):
    """One measured span, and ALL FOUR of its spellings in one place (ticket 14).

    The chain used to be: `SectionTimers.SECTIONS` -> `totals()` -> a result-dict key ->
    pickled across the process seam -> a hand-written column in `record_arm`'s INSERT. A
    section added to the first flowed automatically as far as the result dict and then
    **vanished at the INSERT** -- writing nothing and raising nothing. Here a span declares
    its spellings once and `record_arm` builds its statement from them, so the last hop is
    derived rather than remembered.

    `column` IS A HARD CONTRACT. Renaming one shifts the `runtime_metrics` schema id for a
    relabelling and breaks archived rows' comparability with themselves, which is why
    `label` exists: the human name tracks what a span measures, the column never moves.
    `inv_s` is the standing example -- it predates the bin-mutation log, when that section
    wrote the `bin_inventory` snapshot; the table is gone and the section is now the
    per-batch conservation ledger, but the column is still `inv_s`.

    `label` is also the PARTITION flag. A span with a label is one of the seven that tile an
    arm's wall clock; a span with `None` is an OVERLAY carved out of one of them, and putting
    an overlay in the stacked graph double-counts its seconds.
    """

    #: the accumulator key `SectionTimers.add()` takes
    section: str
    #: the worker RESULT-DICT key -- `t_<section>` for ten of the twelve
    result_key: str
    #: the `runtime` DDL column. Never renamed; see the class docstring.
    column: str
    #: the stacked-graph label, or None when this span is an overlay of another
    label: str | None
    #: what the column gets when the worker did not measure this span
    absent: float | None = 0.0


#: The twelve spans, in `SectionTimers`' own declaration order — which matches NEITHER the
#: checkpoint log line (it prints reord, build, smpl, task, pre, sim, extr, cons and puts kf
#: last) NOR the DDL. Preserved exactly as it was so `SectionTimers.SECTIONS`, which is derived
#: from this tuple, keeps the order it has always had.
SPANS: tuple = (
    Span('reord',   't_reord',   'reord_s',   'reorder'),
    Span('build',   't_build',   'build_s',   'batch-build'),
    Span('sample',  't_sample',  'smpl_s',    None),      # a sub-split of build_s
    Span('task',    't_task',    'task_s',    None),      # a sub-split of build_s
    Span('kf',      't_kf',      'kf_s',      None),      # a sub-span of pre_s
    Span('pre',     't_pre',     'pre_s',     'pre-snapshot'),
    Span('sim',     't_sim',     'sim_s',     'sim'),
    Span('extract', 't_extract', 'extract_s', 'extract'),
    Span('inv',     't_inv',     'inv_s',     'bin-accounting'),
    Span('save',    't_save',    'save_s',    'DB-save'),
    # The one pair whose result key and column COINCIDE, which is exactly why calling
    # `t_<name>` "the column" reads as plausible and is wrong for the other ten.
    Span('p1',      'p1_s',      'p1_s',      None),      # a split of sim_s
    Span('p2',      'p2_s',      'p2_s',      None),      # a split of sim_s
    # THE INBOUND CARVE (2026-09-24): overlays of reord_s, charged per batch from the
    # inbound probe (`Warehouse.kernel.perf_probe`) by `_charge_probe`.  `absent=None`: a
    # legacy result dict writes NULL, never zero.
    Span('inb_pre',     't_inb_pre',     'inb_pre_s',     None, None),
    Span('inb_freeze',  't_inb_freeze',  'inb_freeze_s',  None, None),
    Span('inb_pack',    't_inb_pack',    'inb_pack_s',    None, None),
    Span('inb_yplan',   't_inb_yplan',   'inb_yplan_s',   None, None),
    Span('inb_dplan',   't_inb_dplan',   'inb_dplan_s',   None, None),
    Span('inb_unload',  't_inb_unload',  'inb_unload_s',  None, None),
    Span('inb_handoff', 't_inb_handoff', 'inb_handoff_s', None, None),
    Span('put',         't_put',         'put_s',         None, None),
    Span('put_open',    't_put_open',    'put_open_s',    None, None),
)


#: The columns `record_arm` reads off the worker result that are NOT spans. `absent` is the
#: value a row gets when the key is missing, and it is a per-column DECISION rather than a
#: blanket `or 0.0`: NULL means "not measured", 0 means "measured as zero", and for the setup
#: spans telling those apart is the entire job of `precomp_src`.
RESULT_COLUMNS: tuple = (
    # (column, result key, cast or None to pass through, absent)
    ('n_bins',       'n_bins',       int,   0),
    ('regime_bins',  'regime_bins',  int,   0),
    ('n_aisles',     'n_aisles',     int,   0),
    ('gc_pause_s',   'gc_pause_s',   float, 0.0),
    ('gc_gen2',      'gc_gen2',      int,   0),
    # NULLABLE, and not coerced: not every platform reports RSS, and a 0 here would read as
    # "this arm used no memory" rather than "nobody looked".
    ('peak_rss_mib', 'peak_rss_mib', None,  None),
    ('live_objects', 'live_objects', None,  None),
    # The setup spans (2026-08-23 column add), measured OUTSIDE `total_s` — see OUTSIDE_TOTAL.
    # A worker that did not measure the precompute must write NULL, or the analysis cannot
    # tell "no map to build" from "0.0 s".
    ('precomp_s',    't_precompute', None,  None),
    ('map_lap_pct',  'map_lap_pct',  None,  None),
    # The inbound probe's counters (2026-09-24).  NULL when the leaf never drained one.
    ('inb_drains',   'inb_drains',   None,  None),
    ('yard_T_sum',   'yard_T_sum',   None,  None),
    ('yard_T_max',   'yard_T_max',   None,  None),
    ('yard_pulls',   'yard_pulls',   None,  None),
    ('plan_rounds',  'plan_rounds',  None,  None),
    ('plan_places',  'plan_places',  None,  None),
    ('put_opens',    'put_opens',    None,  None),
    ('put_units',    'put_units',    None,  None),
    # Setup outside `total_s` (OUTSIDE_TOTAL): see the DDL comment.
    ('startup_s',    'startup_s',    None,  None),
    ('sib_setup_s',  'sib_setup_s',  None,  None),
)


#: The seven spans that TILE an arm's wall clock, as `(column, label)` for the stacked graph.
#: DERIVED from `SPANS` rather than restated: a partition and an overlay differ by exactly one
#: field, and this list having its own copy is how an overlay gets into it. Adding an overlay
#: here double-counts its seconds; query the column directly instead.
SECTIONS = [(s.column, s.label) for s in SPANS if s.label is not None]

#: Spans measured OUTSIDE `total_s` — the batch loop's clock starts after setup, so the
#: map precompute is in neither `total_s` nor any SECTION.  Kept as its own list for the
#: same reason SECTIONS excludes its overlays, and the failure mode is the mirror image:
#: SECTIONS must not gain an overlay (double-counts), and this must never be folded INTO
#: SECTIONS (stacks a span onto a total that does not contain it).  An arm's real cost is
#: the SUM of the two lists, and any chart showing it says so.
OUTSIDE_TOTAL = [
    ('precomp_s', 'map precompute (setup, once per arm)'),
    ('startup_s', "worker setup before this leaf's loop clock"),
]
# `sib_setup_s` is NOT listed: it is the part of `total_s` that is the sibling leaf's setup
# -- INSIDE the total, not outside it -- so it is a correction to read against total_s,
# never a span to stack on top of it.


def runtime_db_path(run_root: str) -> str:
    return os.path.join(run_root, RUNTIME_DB)


def _parse_arm(arm: str):
    """'uni_rank_labor_norsl' -> ('uni', 'rank_labor'); assignment may contain '_'."""
    body = arm[:-len('_norsl')] if arm.endswith('_norsl') else arm
    initial, _, assignment = body.partition('_')
    return initial, assignment


def record_arm(run_root: str, cell: str, res: dict, *,
               pair: str, config: str, channel: str, arm: str) -> None:
    """Insert one arm's runtime row (idempotent by (cell,pair,config,channel,arm) — a re-run/resume
    of the arm overwrites its row).  `res` = the worker return dict.

    The four key fields are KEYWORD-ONLY, and deliberately.  This took a work-unit uid and
    unpacked it positionally, which was safe only while every uid meant
    `(pair, config, channel, arm)`.  The coupled unit's uid is `(label, 'coupled', arm_store,
    arm_ful)` — same arity, different meanings — so the positional read would have written
    `channel = <arm_store>` into a TEXT NOT NULL column whose IntegrityError the supervisor
    swallows with a warning: neither the wrong value nor a rejected row would announce itself.
    A caller now has to say which is which, and a caller holding a differently-shaped uid gets
    a TypeError at the call site instead of a wrong row.

    Best-effort: callers wrap this so a runtime-DB hiccup never sinks a real run."""
    initial, assignment = _parse_arm(arm)
    total   = float(res.get('elapsed', 0.0) or 0.0)
    batches = int(res.get('done', 0) or 0)
    con = sqlite3.connect(runtime_db_path(run_root))
    try:
        for stmt in _ALL_DDL:
            con.execute(stmt)
        # A RESUME INTO AN OLDER VINTAGE'S ROOT: `CREATE TABLE IF NOT EXISTS` cannot widen
        # the table the first launch created, and the INSERT below names every declared
        # column, so without this the row would fail -- and the caller swallows the error,
        # leaving the arm with no runtime row at all.
        _migrate_setup_columns(con)
        # AN EMPTY RESULT NEVER REPLACES A REAL ONE.
        #
        # A `--resume` re-walks every arm. One whose checkpoint marker says it already finished
        # runs an EMPTY LOOP -- that is the marker working, not failing -- and its result dict
        # carries `done = 0`. `INSERT OR REPLACE` below would then overwrite a true row written
        # by the first process with a row saying the arm ran no batches.
        #
        # Measured on a tiny run killed at 77/136 and resumed: `batches` read 0 instead of 6 on
        # ten arms, and since `batches` is not in the digest's runtime exclusions it is what made
        # a resumed run compare DIFFERS against a clean one. It matters beyond the digest --
        # per-arm quantities get normalised by `batches`, so a resumed run would divide by the
        # wrong denominator, silently, on exactly the runs that had a failure.
        #
        # It still INSERTS when no row exists: an arm the parent never got to record is genuinely
        # unknown, and a missing row is worse than an empty one.
        if batches == 0:
            _prior = con.execute(
                'SELECT batches FROM runtime WHERE cell=? AND pair=? AND config=? '
                'AND channel=? AND arm=?',
                (cell or '', pair, config, channel, arm)).fetchone()
            if _prior is not None and int(_prior[0] or 0) > 0:
                return
        # Stamp + verify the store: worker-safe (warn-once) — record_arm runs per arm, deep
        # inside a sweep; a store gap must nag, not kill the arm.
        _compat.stamp_checked(con, RUNTIME_DB_FAMILY, strict=False)
        # BUILT FROM THE TABLES, not written out (ticket 14).  A span used to declare itself
        # in `SectionTimers.SECTIONS`, flow automatically into the result dict, and then
        # vanish here -- writing nothing, raising nothing.  Now the last hop is derived, and
        # `Tests/unit/test_runtime_span_table.py` asserts the tables and the DDL cover each
        # other exactly.  Every read is still a `.get`, so a crashed or legacy result dict
        # writes its declared `absent` rather than raising.
        cols = ['cell', 'pair', 'config', 'channel', 'arm', 'initial', 'assignment',
                'batches', 'total_s', 'rate']
        vals = [cell or '', pair, config, channel, arm, initial, assignment,
                batches, total, (batches / total if total > 0 else 0.0)]
        for _col, _key, _cast, _absent in RESULT_COLUMNS:
            _v = res.get(_key)
            cols.append(_col)
            vals.append(_absent if _v is None else (_cast(_v) if _cast else _v))
        for _s in SPANS:
            cols.append(_s.column)
            vals.append(float(res.get(_s.result_key) or _s.absent or 0.0)
                        if _s.absent is not None else res.get(_s.result_key))
        # THE ONE DERIVED COLUMN, and it stays written out: its value is a statement ABOUT
        # another column ("was the precompute measured at all") rather than a reading, so a
        # table entry for it would be a table entry holding a sentence.
        cols.append('precomp_src')
        vals.append('inline' if res.get('t_precompute') is not None else None)
        con.execute(
            f'INSERT OR REPLACE INTO runtime ({",".join(cols)}) '
            f'VALUES ({",".join("?" * len(cols))})', tuple(vals))
        con.commit()                     # connect.close checkpoints, it does not commit
    finally:
        _connect.close(con)


#: The setup-phase columns, in DDL order — the ones a pre-2026-08-23 DB predates -- and
#: the inbound carve's, the ones a pre-2026-09-24 DB predates.  All NULLABLE with no
#: default, so an ALTER leaves existing rows reading NULL ("never measured").
_SETUP_COLUMNS = (('precomp_s', 'REAL'), ('precomp_src', 'TEXT'), ('map_lap_pct', 'REAL'),
                  ('inb_pre_s', 'REAL'), ('inb_freeze_s', 'REAL'), ('inb_pack_s', 'REAL'),
                  ('inb_yplan_s', 'REAL'), ('inb_dplan_s', 'REAL'), ('inb_unload_s', 'REAL'),
                  ('inb_handoff_s', 'REAL'), ('put_s', 'REAL'), ('put_open_s', 'REAL'),
                  ('inb_drains', 'INTEGER'), ('yard_T_sum', 'INTEGER'),
                  ('yard_T_max', 'INTEGER'), ('yard_pulls', 'INTEGER'),
                  ('plan_rounds', 'INTEGER'), ('plan_places', 'INTEGER'),
                  ('put_opens', 'INTEGER'), ('put_units', 'INTEGER'),
                  ('startup_s', 'REAL'), ('sib_setup_s', 'REAL'))


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
