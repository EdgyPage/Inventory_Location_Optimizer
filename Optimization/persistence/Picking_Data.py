import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from Schema import identity as _identity
from Schema import shape as _shape
from Schema.connect import read_only as _ro_conn


@dataclass
class PickRecord:
    run_id:    int
    batch_id:  int
    picker_id: int
    sim_time:  float
    aisle_id:  int
    bayX:      int
    bayY:      int
    sku:       int
    quantity:  int


@dataclass
class AisleMetricRecord:
    run_id:        int
    batch_id:      int
    aisle_id:      int
    n_skus:        int    # unique SKUs placed in this aisle
    n_bins:        int    # occupied bin count in this aisle
    demand_sum:    float  # Σ f_i * q_i — trip-cost secondary score (demand mass)
    lift_sum:      float  # affinity pairwise lift sum — co-location quality
    pick_load_sum: float = 0.0  # Σ f_i*q_i*per-pick cost — labor-balance score (Rank_labor)


@dataclass
class BatchStats:
    run_id: int
    batch_id: int
    duration: float               # BATCH MAKESPAN: max picker done-time — parallel wall-clock
    num_tasks: int                # unique aisles visited
    total_items: int              # items picked across all pickers
    avg_concurrent_pickers: float # time-weighted mean pickers in "picking" state
    picking_pct: float            # fraction of aggregate picker-time spent picking
    traveling_pct: float          # fraction of aggregate picker-time spent traveling
    # ── success metrics (see extract_batch_stats) ────────────────────────────────
    task_makespan: float = 0.0    # TASK MAKESPAN: Σ per-picker done-times = total labor (serial)
    thr_task: float = 0.0         # throughput / task makespan  = total_items / task_makespan
    thr_batch: float = 0.0        # throughput / batch makespan = total_items / duration
    batch_start_time: float = 0.0 # min picker-event time (batch-relative clock)
    batch_end_time:   float = 0.0 # max picker-event time (≈ duration)
    sigma_fd: float = 0.0         # realised demand-weighted within-aisle travel (Sigma f*D)
    reload_moves: int = 0         # re-slot bin moves this batch (layout churn)
    reorder_placements: int = 0   # reorder unit PLACEMENTS this batch (units binned; restock churn)
    skus_reordered: int = 0       # distinct SKUs reordered this batch (N in the reorder log line)
    units_ordered: int = 0        # units ORDERED this batch (Σ reorder qty entering the lead queue)
    queue_depth: int = 0          # put-away backlog: storage units packed but not yet binned
    lead_queue_depth: int = 0     # in-transit reorders (records awaiting lead-time arrival)
    in_transit_qty: int = 0       # total items in the lead queue (on-order, not yet arrived)
    is_outlier: bool = False


@dataclass
class TaskStats:
    run_id: int
    batch_id: int
    aisle_id: int
    picker_id: int
    task_start_time: float  # sim time when the picker started this task
    task_end_time:   float  # sim time when the picker finished this task
    duration: float         # task_end_time − task_start_time
    W: float              # analytical aisle workload baseline
    lift_sum: float         # sum_lift for this aisle's SKUs
    num_bins_visited: int   # bins in the task path (planned visit count)
    total_items: int        # items picked in this aisle
    is_outlier: bool = False


@dataclass
class BinInventoryRecord:
    run_id:       int
    batch_id:     int
    aisle_id:     int
    bayX:         int
    bayY:         int
    sku:          int
    unit_type:    str   # 'pallet' or 'singleton'
    storage_size: str   # bin's physical storage size slot
    pre_qty:      int   # quantity after check_reorders(), before picks
    post_qty:     int   # quantity after all picks applied


@dataclass
class PickerEventRecord:
    run_id:         int
    batch_id:       int
    picker_id:      int
    time:           float
    event_type:     str          # task_start|arrive|cart_swap|pick|task_end|done
    aisle_id:       int | None
    bayX:           int | None
    bayY:           int | None
    sku:            int | None
    quantity:       int | None
    bins_completed: int
    total_bins:     int
    items_picked:   int
    total_items:    int
    # Travel decomposition (seconds; see Warehouse/Pick.PickEvent).  Defaulted so legacy
    # DBs lacking these columns still construct.
    pick_travel_x:     float = 0.0
    pick_travel_y:     float = 0.0
    non_pick_travel_x: float = 0.0
    non_pick_travel_y: float = 0.0
    cart_move:         float = 0.0


# ── PickRecord columns ────────────────────────────────────────────────────────

_PICK_COLS = ('sku', 'quantity', 'timestamp', 'aisle_id', 'bayX', 'bayY',
              'handling_type', 'category_type')

_CREATE_PICKS = """
    CREATE TABLE IF NOT EXISTS picks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id    INTEGER NOT NULL,
        picker_id   INTEGER NOT NULL,
        sim_time    REAL    NOT NULL,
        aisle_id    INTEGER NOT NULL,
        bayX        INTEGER NOT NULL,
        bayY        INTEGER NOT NULL,
        sku         INTEGER NOT NULL,
        quantity    INTEGER NOT NULL
    )
"""

_CREATE_PICKS_BATCH_IDX = """
    CREATE INDEX IF NOT EXISTS ix_picks_run_batch ON picks (run_id, batch_id)
"""

_CREATE_PICKS_SKU_IDX = """
    CREATE INDEX IF NOT EXISTS ix_picks_run_sku ON picks (run_id, sku)
"""

# ── Run DB schema ─────────────────────────────────────────────────────────────

_CREATE_RUNS = """
    CREATE TABLE IF NOT EXISTS simulation_runs (
        run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type          TEXT    NOT NULL,
        created           TEXT    NOT NULL,
        strategy_key          TEXT,   -- identity (rename-proof): same as run_type, explicit
        pair_label            TEXT,   -- inventory pair folder name (not a path)
        config_label          TEXT,   -- regression config name
        warehouse_fingerprint TEXT,   -- stable hash tying this run to its warehouse.db
        inventory_label       TEXT,   -- planned-inventory profile label
        channel               TEXT,   -- operation/channel ('store'|'fulfillment'); NULL = legacy store-only
        sim_schema_id         TEXT,   -- THIS file's SQL shape (see sim_schema_id()); NULL = pre-stamp run
        num_pickers       INTEGER,
        x_speed           REAL,
        y_speed           REAL,
        pick_intercept    REAL,
        pick_weight_coef  REAL,
        pick_volume_coef  REAL,
        cart_swap_coef    REAL,
        k_pickers         INTEGER,
        n_batches         INTEGER,
        seed_world        INTEGER,
        keyframe_interval INTEGER,
        optimal_sigma_fd  REAL,       -- warehouse yardstick: minimal achievable Σ f*D
        optimal_work      REAL        -- warehouse yardstick: minimal achievable work W*
    )
"""

# Identity columns set by create_run(identity=...); rename-proof run association.
# 'channel' distinguishes the store vs fulfillment run subtrees in a mixed warehouse
# (NULL for legacy store-only runs that don't pass it).
# 'sim_schema_id' is defaulted by create_run rather than supplied by the caller — it describes
# the FILE's shape, not the run's provenance.  Runs written before it existed leave it NULL and
# the reader derives the id instead (Visualization/readers).
_IDENTITY_COLS = ('strategy_key', 'pair_label', 'config_label',
                  'warehouse_fingerprint', 'inventory_label', 'channel',
                  'sim_schema_id')

# Run-param columns set by create_run(params=...); order matches the INSERT.
_RUN_PARAM_COLS = ('num_pickers', 'x_speed', 'y_speed', 'pick_intercept',
                   'pick_weight_coef', 'pick_volume_coef', 'cart_swap_coef',
                   'k_pickers', 'n_batches', 'seed_world', 'keyframe_interval',
                   'optimal_sigma_fd', 'optimal_work')

_CREATE_AISLE_METRICS = """
    CREATE TABLE IF NOT EXISTS aisle_metrics (
        run_id        INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id      INTEGER NOT NULL,
        aisle_id      INTEGER NOT NULL,
        n_skus        INTEGER NOT NULL DEFAULT 0,
        n_bins        INTEGER NOT NULL DEFAULT 0,
        demand_sum    REAL    NOT NULL DEFAULT 0.0,
        lift_sum      REAL    NOT NULL DEFAULT 0.0,
        pick_load_sum REAL    NOT NULL DEFAULT 0.0,
        PRIMARY KEY (run_id, batch_id, aisle_id)
    )
"""

# Trend query — how one aisle evolves over batches across strategies:
#   SELECT am.batch_id, sr.run_type, am.demand_sum, am.lift_sum, am.n_skus
#   FROM   aisle_metrics am JOIN simulation_runs sr USING (run_id)
#   WHERE  am.aisle_id = ? ORDER BY sr.run_type, am.batch_id
#
# Snapshot query — all aisles at a given batch (e.g. batch 50):
#   SELECT aisle_id, demand_sum, lift_sum, n_skus, n_bins
#   FROM   aisle_metrics WHERE run_id=? AND batch_id=50
#   ORDER  BY demand_sum DESC

_CREATE_AISLE_METRICS_BATCH_IDX = """
    CREATE INDEX IF NOT EXISTS ix_am_run_batch
    ON aisle_metrics (run_id, batch_id)
"""

_CREATE_AISLE_METRICS_AISLE_IDX = """
    CREATE INDEX IF NOT EXISTS ix_am_run_aisle
    ON aisle_metrics (run_id, aisle_id)
"""

_CREATE_BATCH_STATS = """
    CREATE TABLE IF NOT EXISTS batch_stats (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id                 INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id               INTEGER NOT NULL,
        duration               REAL    NOT NULL,
        num_tasks              INTEGER NOT NULL,
        total_items            INTEGER NOT NULL,
        task_makespan          REAL    NOT NULL DEFAULT 0,
        thr_task               REAL    NOT NULL DEFAULT 0,
        thr_batch              REAL    NOT NULL DEFAULT 0,
        avg_concurrent_pickers REAL    NOT NULL,
        picking_pct            REAL    NOT NULL,
        traveling_pct          REAL    NOT NULL,
        batch_start_time       REAL    NOT NULL DEFAULT 0,
        batch_end_time         REAL    NOT NULL DEFAULT 0,
        sigma_fd               REAL    NOT NULL DEFAULT 0,
        reload_moves           INTEGER NOT NULL DEFAULT 0,
        reorder_placements     INTEGER NOT NULL DEFAULT 0,
        skus_reordered         INTEGER NOT NULL DEFAULT 0,
        units_ordered          INTEGER NOT NULL DEFAULT 0,
        queue_depth            INTEGER NOT NULL DEFAULT 0,
        lead_queue_depth       INTEGER NOT NULL DEFAULT 0,
        in_transit_qty         INTEGER NOT NULL DEFAULT 0,
        is_outlier             INTEGER NOT NULL DEFAULT 0
    )
"""

_CREATE_TASK_STATS = """
    CREATE TABLE IF NOT EXISTS task_stats (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id           INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id         INTEGER NOT NULL,
        aisle_id         INTEGER NOT NULL,
        picker_id        INTEGER NOT NULL,
        task_start_time  REAL    NOT NULL DEFAULT 0,
        task_end_time    REAL    NOT NULL DEFAULT 0,
        duration         REAL    NOT NULL,
        W              REAL    NOT NULL,
        lift_sum         REAL    NOT NULL,
        num_bins_visited INTEGER NOT NULL,
        total_items      INTEGER NOT NULL,
        is_outlier       INTEGER NOT NULL DEFAULT 0
    )
"""

_CREATE_PICKER_EVENTS = """
    CREATE TABLE IF NOT EXISTS picker_events (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id         INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id       INTEGER NOT NULL,
        picker_id      INTEGER NOT NULL,
        time           REAL    NOT NULL,
        event_type     TEXT    NOT NULL,
        aisle_id       INTEGER,
        bayX           INTEGER,
        bayY           INTEGER,
        sku            INTEGER,
        quantity       INTEGER,
        bins_completed INTEGER NOT NULL DEFAULT 0,
        total_bins     INTEGER NOT NULL DEFAULT 0,
        items_picked   INTEGER NOT NULL DEFAULT 0,
        total_items    INTEGER NOT NULL DEFAULT 0,
        pick_travel_x     REAL NOT NULL DEFAULT 0,
        pick_travel_y     REAL NOT NULL DEFAULT 0,
        non_pick_travel_x REAL NOT NULL DEFAULT 0,
        non_pick_travel_y REAL NOT NULL DEFAULT 0,
        cart_move         REAL NOT NULL DEFAULT 0
    )
"""

_CREATE_PICKER_EVENTS_IDX = """
    CREATE INDEX IF NOT EXISTS ix_pe_run_batch
    ON picker_events (run_id, batch_id)
"""

_CREATE_PICKER_EVENTS_TIME_IDX = """
    CREATE INDEX IF NOT EXISTS ix_pe_run_batch_time
    ON picker_events (run_id, batch_id, time)
"""

_CREATE_BIN_INVENTORY = """
    CREATE TABLE IF NOT EXISTS bin_inventory (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id     INTEGER NOT NULL,
        aisle_id     INTEGER NOT NULL,
        bayX         INTEGER NOT NULL,
        bayY         INTEGER NOT NULL,
        sku          INTEGER NOT NULL,
        unit_type    TEXT    NOT NULL,
        storage_size TEXT    NOT NULL,
        pre_qty      INTEGER NOT NULL,
        post_qty     INTEGER NOT NULL
    )
"""

# Query pattern: load full warehouse snapshot at start of batch B
#   SELECT * FROM bin_inventory WHERE run_id=? AND batch_id=? ORDER BY aisle_id, bayX, bayY
#
# Derive inventory at sim-time T mid-batch (join with picker_events):
#   WITH pre AS (
#       SELECT aisle_id, bayX, bayY, sku, pre_qty
#       FROM   bin_inventory WHERE run_id=? AND batch_id=?
#   ),
#   picks AS (
#       SELECT aisle_id, bayX, bayY, SUM(quantity) AS picked
#       FROM   picker_events
#       WHERE  run_id=? AND batch_id=? AND event_type='pick' AND time <= ?
#       GROUP  BY aisle_id, bayX, bayY
#   )
#   SELECT p.aisle_id, p.bayX, p.bayY, p.sku,
#          MAX(0, p.pre_qty - COALESCE(pk.picked, 0)) AS qty_at_t
#   FROM   pre p LEFT JOIN picks pk
#          ON p.aisle_id=pk.aisle_id AND p.bayX=pk.bayX AND p.bayY=pk.bayY
#
# Sanity check — total picked per bin must equal pre_qty - post_qty:
#   SELECT b.aisle_id, b.bayX, b.bayY, b.sku,
#          b.pre_qty - b.post_qty        AS expected_picked,
#          COALESCE(SUM(pe.quantity), 0) AS actual_picked,
#          (b.pre_qty - b.post_qty) - COALESCE(SUM(pe.quantity), 0) AS drift
#   FROM   bin_inventory b
#   LEFT JOIN picker_events pe
#          ON  pe.run_id=b.run_id AND pe.batch_id=b.batch_id
#          AND pe.aisle_id=b.aisle_id AND pe.bayX=b.bayX AND pe.bayY=b.bayY
#          AND pe.event_type='pick'
#   WHERE  b.run_id=? AND b.batch_id=?
#   GROUP  BY b.aisle_id, b.bayX, b.bayY
#   HAVING drift != 0

_CREATE_BIN_INVENTORY_IDX = """
    CREATE INDEX IF NOT EXISTS ix_bi_run_batch
    ON bin_inventory (run_id, batch_id)
"""

_CREATE_BIN_INVENTORY_AISLE_IDX = """
    CREATE INDEX IF NOT EXISTS ix_bi_run_batch_aisle
    ON bin_inventory (run_id, batch_id, aisle_id)
"""


# NOTE: the standalone PickRecord CSV/SQLite pair (load/save_picks_csv, load/save_picks_db) and
# their _pick_to_row/_pick_from_row helpers were removed — superseded by `save_picks` below, which
# is what strategy_runner actually calls.  PickRecord itself is very much alive
# (Simulation_Analytics.extract_picks builds them; save_picks persists them).


# ── Run DB public API ─────────────────────────────────────────────────────────

def _open_db(path: str, timeout: float = 60.0) -> sqlite3.Connection:
    """Open *path* with WAL journal mode and a generous busy timeout.

    WAL allows multiple concurrent readers and one writer without blocking
    readers.  Writers that arrive while another write is in progress wait up
    to *timeout* seconds before raising OperationalError, giving the three
    parallel strategy workers enough headroom to avoid spurious lock errors
    when their 100-batch checkpoints happen to coincide.
    """
    con = sqlite3.connect(path, timeout=timeout)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    return con


_CREATE_REORDER_QUEUE = """
    CREATE TABLE IF NOT EXISTS reorder_queue (
        run_id         INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id       INTEGER NOT NULL,
        kind           TEXT    NOT NULL,   -- 'lead' (in-transit) | 'stock' (awaiting bin)
        sku            INTEGER NOT NULL,
        qty            INTEGER NOT NULL,   -- items in this queue entry
        remaining_lead INTEGER NOT NULL DEFAULT 0,  -- batches until arrival ('lead' only)
        unit_type      TEXT,               -- 'pallet'|'singleton' for stock units (NULL for lead)
        storage_size   TEXT                -- bin size tier for stock units (NULL for lead)
    )
"""
_CREATE_REORDER_QUEUE_IDX = """
    CREATE INDEX IF NOT EXISTS ix_rq_run_batch ON reorder_queue (run_id, batch_id)
"""

# ── Score tables ──────────────────────────────────────────────────────────────
# Static per-run scores the assignment functions compute (geometry/config-fixed), saved
# once after warehouse build so the viewer reads them instead of recomputing.  bin_scores:
# the layout "goodness" of every bin (travel + golden-zone height) plus the optimal-map
# preferred score (map/map_rank only; NULL otherwise).  sku_scores: per-SKU placement scores.

_CREATE_BIN_SCORES = """
    CREATE TABLE IF NOT EXISTS bin_scores (
        run_id       INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        aisle_id     INTEGER NOT NULL,
        bayX         INTEGER NOT NULL,
        bayY         INTEGER NOT NULL,
        travel_d     REAL    NOT NULL,   -- D = x_pace*x_phys + y_pace*y_phys (s)
        height_mult  REAL    NOT NULL,   -- golden-zone height multiplier M(y_phys)
        layout_score REAL    NOT NULL,   -- D + M  (lower = cheaper bin; viewer heatmap)
        map_pref     REAL,               -- optimal-map _bin_pref (map/map_rank only; else NULL)
        PRIMARY KEY (run_id, aisle_id, bayX, bayY)
    )
"""
_CREATE_BIN_SCORES_IDX = """
    CREATE INDEX IF NOT EXISTS ix_bs_run ON bin_scores (run_id)
"""

_CREATE_SKU_SCORES = """
    CREATE TABLE IF NOT EXISTS sku_scores (
        run_id              INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        sku                 INTEGER NOT NULL,
        map_target          REAL,        -- optimal-map target pref (map/map_rank only)
        labor_cost          REAL,        -- per-unit pick effort (intercept + handle_var)
        handle_var          REAL,        -- per-unit weight/volume handling term
        expected_popularity REAL,        -- freq * qty
        expected_labor      REAL,        -- freq * qty * labor_cost
        equilibrium_qty     INTEGER,
        reorder_point       INTEGER,
        lead_time_mean      REAL,
        PRIMARY KEY (run_id, sku)
    )
"""
_CREATE_SKU_SCORES_IDX = """
    CREATE INDEX IF NOT EXISTS ix_ss_run ON sku_scores (run_id)
"""

# ── The bin-mutation log ──────────────────────────────────────────────────────
# `bin_inventory` records picks and NEVER restocks: check_reorders() runs before the pre-batch
# snapshot, so a restocked bin is already in it at its post-restock quantity, and the
# `post_qty == pre_qty` skip then drops it.  Measured on a production arm: 0 rows with
# post_qty > pre_qty against 20k-42k reorder_placements per batch.  Rolling the delta stream
# forward from a keyframe therefore only ever DECAYS — losing 59% of the warehouse in 5 batches.
#
# These two tables close the gap.  Bin state changes at exactly five sites in the codebase (one
# of them dead code), and picks are already fully recorded — verified, SUM(pre_qty-post_qty)
# equals SUM(picks.quantity) exactly on every arm tested.  So PLACE + EVICT + PICK is complete
# BY CONSTRUCTION, which Tests/integration/test_bin_log_replay.py checks against a live sim.
#
# `(batch_id, seq)` is the true resolution, not a compromise: check_reorders() runs entirely
# between one batch's picks and the next batch's simulation, so no finer ordering EXISTS to lose.
# Picks keep sim_time, so intra-batch animation stays exact.

_CREATE_BIN_PLACEMENT = """
    CREATE TABLE IF NOT EXISTS bin_placement (
        run_id   INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id INTEGER NOT NULL,
        seq      INTEGER NOT NULL,   -- run-scoped monotonic; ascending within a batch.
                                     -- NOT reset per batch: initial stocking records at
                                     -- batch 0 before the loop, so a per-batch counter
                                     -- collided with batch-0 reorders on this PK.
        aisle_id INTEGER NOT NULL,
        bayX     INTEGER NOT NULL,
        bayY     INTEGER NOT NULL,
        sku      INTEGER NOT NULL,
        qty      INTEGER NOT NULL,   -- units placed into this bin
        cause    TEXT    NOT NULL,   -- 'initial'|'reorder'|'reslot'
        PRIMARY KEY (run_id, batch_id, seq)
    ) WITHOUT ROWID
"""

_CREATE_BIN_PLACEMENT_IDX = """
    CREATE INDEX IF NOT EXISTS ix_bp_bin
        ON bin_placement (run_id, aisle_id, bayX, bayY, batch_id)
"""

_CREATE_BIN_EVICTION = """
    CREATE TABLE IF NOT EXISTS bin_eviction (
        run_id   INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id INTEGER NOT NULL,
        seq      INTEGER NOT NULL,   -- run-scoped monotonic; ascending within a batch
        aisle_id INTEGER NOT NULL,
        bayX     INTEGER NOT NULL,
        bayY     INTEGER NOT NULL,
        sku      INTEGER NOT NULL,
        qty      INTEGER NOT NULL,   -- units removed; the unit re-enters the stock queue
        PRIMARY KEY (run_id, batch_id, seq)
    ) WITHOUT ROWID
"""

_CREATE_BIN_EVICTION_IDX = """
    CREATE INDEX IF NOT EXISTS ix_be_bin
        ON bin_eviction (run_id, aisle_id, bayX, bayY, batch_id)
"""


def _apply_run_schema(con: sqlite3.Connection) -> None:
    """Issue every CREATE for the run DB on an already-open connection.

    Split out of init_run_db so sim_schema_id() can build the schema in memory and hash what
    the writer ACTUALLY creates — the declared id is never a hand-maintained list of columns,
    so it cannot drift from this function.
    """
    con.execute(_CREATE_PICKS)
    con.execute(_CREATE_PICKS_BATCH_IDX)
    con.execute(_CREATE_PICKS_SKU_IDX)
    con.execute(_CREATE_RUNS)
    con.execute(_CREATE_BATCH_STATS)
    con.execute(_CREATE_TASK_STATS)
    con.execute(_CREATE_PICKER_EVENTS)
    con.execute(_CREATE_PICKER_EVENTS_IDX)
    con.execute(_CREATE_PICKER_EVENTS_TIME_IDX)
    con.execute(_CREATE_BIN_INVENTORY)
    con.execute(_CREATE_BIN_INVENTORY_IDX)
    con.execute(_CREATE_BIN_INVENTORY_AISLE_IDX)
    con.execute(_CREATE_AISLE_METRICS)
    con.execute(_CREATE_AISLE_METRICS_BATCH_IDX)
    con.execute(_CREATE_AISLE_METRICS_AISLE_IDX)
    con.execute(_CREATE_REORDER_QUEUE)
    con.execute(_CREATE_REORDER_QUEUE_IDX)
    con.execute(_CREATE_BIN_SCORES)
    con.execute(_CREATE_BIN_SCORES_IDX)
    con.execute(_CREATE_SKU_SCORES)
    con.execute(_CREATE_SKU_SCORES_IDX)
    con.execute(_CREATE_BIN_PLACEMENT)
    con.execute(_CREATE_BIN_PLACEMENT_IDX)
    con.execute(_CREATE_BIN_EVICTION)
    con.execute(_CREATE_BIN_EVICTION_IDX)
    _migrate_run_columns(con)


# Columns added to simulation_runs after runs already existed.  Every CREATE here is
# IF NOT EXISTS, so reopening an archived DB gains missing TABLES but never missing COLUMNS —
# without this, the next create_run() dies with "no column named sim_schema_id" on any DB
# written by an earlier build (which is every DB in the archive).
_RUN_ADDED_COLS = (('sim_schema_id', 'TEXT'),)


def _migrate_run_columns(con: sqlite3.Connection) -> None:
    """Add any simulation_runs column this build expects but an older file lacks.

    ALTER TABLE ADD COLUMN is O(1) in SQLite (it only rewrites the schema), and adding the
    column also lifts the file's observed shape onto the current declared id — which is exactly
    what the reader registry wants.  Existing rows keep NULL, and a NULL stamp is the documented
    "derive it" path.
    """
    have = {r[1] for r in con.execute('PRAGMA table_info(simulation_runs)')}
    for name, decl in _RUN_ADDED_COLS:
        if name not in have:
            con.execute(f'ALTER TABLE simulation_runs ADD COLUMN {name} {decl}')


def init_run_db(path: str) -> None:
    """Create all tables and indexes if they don't already exist, and enable WAL mode."""
    con = _open_db(path)
    try:
        _apply_run_schema(con)
        con.commit()
    finally:
        con.close()


# ── Schema identity ───────────────────────────────────────────────────────────
# The shape normalizer and the id derivation live in `Schema/` so every DB family shares one
# implementation.  The family REGISTERS ITSELF here rather than Schema/ importing this module:
# Schema is a stdlib-only leaf and must not depend on any writer.
#
# The declared shape is produced by BUILDING the schema this module creates, in memory — never
# by hand-listing columns, which is exactly the kind of declaration that drifts from its writer.

_DECLARED_SIM_SCHEMA_ID: str | None = None


def declared_sim_schema_shape() -> dict:
    """Canonical shape of a run DB as `_apply_run_schema` creates it, built in memory."""
    con = sqlite3.connect(':memory:')
    try:
        _apply_run_schema(con)
        return _shape.canonical_shape(con)
    finally:
        con.close()


def sim_schema_id() -> str:
    """The id this build stamps into new run DBs.  Computed once, then cached."""
    global _DECLARED_SIM_SCHEMA_ID
    if _DECLARED_SIM_SCHEMA_ID is None:
        _DECLARED_SIM_SCHEMA_ID = _shape.shape_id(declared_sim_schema_shape())
    return _DECLARED_SIM_SCHEMA_ID


def declared_keyframe_shape() -> dict:
    """Canonical shape of the sibling keyframe DB."""
    con = sqlite3.connect(':memory:')
    try:
        con.execute(_CREATE_BIN_KEYFRAME)
        con.execute(_CREATE_BIN_KEYFRAME_IDX)
        return _shape.canonical_shape(con)
    finally:
        con.close()


#: The shape every run in the archive was written with, before `sim_schema_id` existed.  Frozen:
#: it cannot be recomputed from today's source, and those files are never rewritten.
PRE_STAMP_SIM_SCHEMA_ID = '23d0c7f167bc'

SIM_DB_FAMILY = _identity.register(_identity.Family(
    name='sim_db',
    declared_shape=declared_sim_schema_shape,
    meta_table=None,                 # stamped into simulation_runs.sim_schema_id, not a meta table
    # 2b7913bcd7e6 = the stamp column, before the bin-mutation log.  Kept so a DB
    # written between those two commits still opens.
    known_ids=(PRE_STAMP_SIM_SCHEMA_ID, '2b7913bcd7e6'),
))

KEYFRAME_DB_FAMILY = _identity.register(_identity.Family(
    name='keyframes_db',
    declared_shape=declared_keyframe_shape,
    meta_table=None,
))


def create_run(path: str, run_type: str, params: dict | None = None,
               identity: dict | None = None) -> int:
    """Insert a new simulation run row; return the assigned run_id.

    params (optional): run configuration recorded for reconstruction —
    keys from _RUN_PARAM_COLS (num_pickers, x_speed, …, optimal_work).
    identity (optional): rename-proof association — keys from _IDENTITY_COLS
    (strategy_key, pair_label, config_label, warehouse_fingerprint, inventory_label).
    Missing keys are stored NULL.
    """
    params = params or {}
    # sim_schema_id describes the FILE, not the run, so it is defaulted here rather than being
    # threaded through every caller.  An explicit value still wins (tests pin an older id).
    identity = {'sim_schema_id': sim_schema_id(), **(identity or {})}
    cols = ('run_type', 'created') + _IDENTITY_COLS + _RUN_PARAM_COLS
    vals = ([run_type, datetime.now(timezone.utc).isoformat()]
            + [identity.get(k) for k in _IDENTITY_COLS]
            + [params.get(k) for k in _RUN_PARAM_COLS])
    con = _open_db(path)
    try:
        cur = con.execute(
            f'INSERT INTO simulation_runs ({",".join(cols)}) '
            f'VALUES ({",".join("?" * len(cols))})',
            vals,
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        con.close()


def find_run(path: str, strategy_key: str | None = None) -> int | None:
    """Resolve a run_id WITHOUT relying on the file name.

    Matches on the stored strategy_key/run_type (which equal the strategy key) so a
    renamed sim_*.db still resolves; falls back to the first run in the DB.  Returns
    None if the DB has no runs.  Tolerates the older schema (no strategy_key column).
    """
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        if strategy_key is not None:
            try:
                row = con.execute(
                    'SELECT run_id FROM simulation_runs '
                    'WHERE strategy_key=? OR run_type=? ORDER BY run_id LIMIT 1',
                    (strategy_key, strategy_key)).fetchone()
            except sqlite3.OperationalError:   # pre-identity schema
                row = con.execute(
                    'SELECT run_id FROM simulation_runs WHERE run_type=? '
                    'ORDER BY run_id LIMIT 1', (strategy_key,)).fetchone()
            if row is not None:
                return int(row['run_id'])
        row = con.execute(
            'SELECT run_id FROM simulation_runs ORDER BY run_id LIMIT 1').fetchone()
        return int(row['run_id']) if row else None
    finally:
        con.close()


def run_identity(path: str, run_id: int) -> dict:
    """Return the stored identity + key params for a run (rename-proof metadata).
    Missing columns (older schema) are simply omitted from the dict."""
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            'SELECT * FROM simulation_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            return {}
        keys = set(row.keys())
        wanted = (('run_id', 'run_type', 'n_batches', 'keyframe_interval',
                   'optimal_sigma_fd', 'optimal_work') + _IDENTITY_COLS)
        return {k: row[k] for k in wanted if k in keys}
    finally:
        con.close()


# ── Keyframe DB (separate file per strategy) ──────────────────────────────────
# Full occupied-bin snapshot every K batches, so the visualizer can jump to a
# batch's start state without replaying all deltas from batch 0.  Kept in its own
# file (one per strategy run) so the parallel A/B/C workers never contend.

_CREATE_BIN_KEYFRAME = """
    CREATE TABLE IF NOT EXISTS bin_keyframe (
        run_id       INTEGER NOT NULL,
        batch_id     INTEGER NOT NULL,
        aisle_id     INTEGER NOT NULL,
        bayX         INTEGER NOT NULL,
        bayY         INTEGER NOT NULL,
        sku          INTEGER NOT NULL,
        unit_type    TEXT    NOT NULL,
        storage_size TEXT    NOT NULL,
        qty          INTEGER NOT NULL,
        PRIMARY KEY (run_id, batch_id, aisle_id, bayX, bayY)
    )
"""
_CREATE_BIN_KEYFRAME_IDX = """
    CREATE INDEX IF NOT EXISTS ix_kf_run_batch ON bin_keyframe (run_id, batch_id)
"""


def keyframe_db_path(run_db_path: str) -> str:
    """Sibling keyframe-DB path for a strategy's run DB (run.db → run.keyframes.db)."""
    base, _ext = os.path.splitext(run_db_path)
    return base + '.keyframes.db'


def init_keyframe_db(path: str) -> None:
    """Create the bin_keyframe table + index if absent."""
    con = _open_db(path)
    try:
        con.execute(_CREATE_BIN_KEYFRAME)
        con.execute(_CREATE_BIN_KEYFRAME_IDX)
        con.commit()
    finally:
        con.close()


def save_bin_keyframe(path: str, run_id: int, batch_id: int, records: list) -> None:
    """Write one full occupied-bin snapshot (keyframe) for (run_id, batch_id).

    records: iterable of dicts with keys aisle_id, bayX, bayY, sku, unit_type,
    storage_size, qty.  INSERT OR REPLACE so re-running a batch overwrites cleanly.
    """
    con = _open_db(path)
    try:
        con.execute(_CREATE_BIN_KEYFRAME)
        con.executemany(
            'INSERT OR REPLACE INTO bin_keyframe '
            '(run_id,batch_id,aisle_id,bayX,bayY,sku,unit_type,storage_size,qty) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [(run_id, batch_id, r['aisle_id'], r['bayX'], r['bayY'], r['sku'],
              r['unit_type'], r['storage_size'], r['qty']) for r in records],
        )
        con.commit()
    finally:
        con.close()


# ── BatchStats DB ─────────────────────────────────────────────────────────────

def save_batch_stats(path: str, run_id: int, records: list[BatchStats]) -> None:
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO batch_stats '
            '(run_id,batch_id,duration,num_tasks,total_items,'
            'task_makespan,thr_task,thr_batch,'
            'avg_concurrent_pickers,picking_pct,traveling_pct,'
            'batch_start_time,batch_end_time,'
            'sigma_fd,reload_moves,reorder_placements,skus_reordered,units_ordered,'
            'queue_depth,lead_queue_depth,in_transit_qty,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.duration, r.num_tasks, r.total_items,
                 r.task_makespan, r.thr_task, r.thr_batch,
                 r.avg_concurrent_pickers, r.picking_pct, r.traveling_pct,
                 r.batch_start_time, r.batch_end_time,
                 r.sigma_fd, r.reload_moves, r.reorder_placements, r.skus_reordered, r.units_ordered,
                 r.queue_depth, r.lead_queue_depth, r.in_transit_qty, int(r.is_outlier))
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_batch_stats(path: str, run_id: int) -> list[BatchStats]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT * FROM batch_stats WHERE run_id = ?', (run_id,)
        ).fetchall()
        return [
            BatchStats(
                run_id                 = row['run_id'],
                batch_id               = row['batch_id'],
                duration               = row['duration'],
                num_tasks              = row['num_tasks'],
                total_items            = row['total_items'],
                avg_concurrent_pickers = row['avg_concurrent_pickers'],
                picking_pct            = row['picking_pct'],
                traveling_pct          = row['traveling_pct'],
                task_makespan          = (row['task_makespan']
                                          if 'task_makespan' in row.keys() else 0.0),
                thr_task               = (row['thr_task']
                                          if 'thr_task' in row.keys() else 0.0),
                thr_batch              = (row['thr_batch']
                                          if 'thr_batch' in row.keys() else 0.0),
                batch_start_time       = (row['batch_start_time']
                                          if 'batch_start_time' in row.keys() else 0.0),
                batch_end_time         = (row['batch_end_time']
                                          if 'batch_end_time' in row.keys() else 0.0),
                sigma_fd               = (row['sigma_fd'] if 'sigma_fd' in row.keys()
                                          else (row['sigma_fw'] if 'sigma_fw' in row.keys() else 0.0)),
                reload_moves           = (row['reload_moves']
                                          if 'reload_moves' in row.keys() else 0),
                reorder_placements     = (row['reorder_placements']
                                          if 'reorder_placements' in row.keys() else 0),
                skus_reordered         = (row['skus_reordered']
                                          if 'skus_reordered' in row.keys() else 0),
                units_ordered          = (row['units_ordered']
                                          if 'units_ordered' in row.keys() else 0),
                queue_depth            = (row['queue_depth']
                                          if 'queue_depth' in row.keys() else 0),
                lead_queue_depth       = (row['lead_queue_depth']
                                          if 'lead_queue_depth' in row.keys() else 0),
                in_transit_qty         = (row['in_transit_qty']
                                          if 'in_transit_qty' in row.keys() else 0),
                is_outlier             = bool(row['is_outlier']),
            )
            for row in rows
        ]
    finally:
        con.close()


# ── ReorderQueue DB ───────────────────────────────────────────────────────────
# Per-batch snapshot of the replenishment queues *after* check_reorders (i.e. the
# state the viewer shows draining into bins at t=0 of the batch).  Each record is a
# (batch_id, kind, sku, qty, remaining_lead) tuple; kind ∈ {'lead','stock'}.

def save_reorder_queue(path: str, run_id: int, records: list[tuple]) -> None:
    """Persist per-batch queue snapshots.  Each record is
    (batch_id, kind, sku, qty, remaining_lead, unit_type, storage_size); the last two
    are None for 'lead' entries (in-transit) and carry the bin tier for 'stock' units."""
    if not records:
        return
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO reorder_queue '
            '(run_id,batch_id,kind,sku,qty,remaining_lead,unit_type,storage_size) '
            'VALUES (?,?,?,?,?,?,?,?)',
            [(run_id, int(b), str(k), int(s), int(q), int(rl), ut, ss)
             for (b, k, s, q, rl, ut, ss) in records],
        )
        con.commit()
    finally:
        con.close()


def load_reorder_queue(path: str, run_id: int, batch_id: int) -> list[dict]:
    """Queue contents at the start of one batch.  Empty list if the table is absent
    (older runs predate it) so the viewer degrades gracefully.  Falls back to the
    pre-enrichment columns when unit_type/storage_size are missing."""
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        try:
            rows = con.execute(
                'SELECT kind, sku, qty, remaining_lead, unit_type, storage_size '
                'FROM reorder_queue WHERE run_id=? AND batch_id=?',
                (run_id, batch_id)).fetchall()
        except sqlite3.OperationalError:       # pre-enrichment schema (no unit_type/size)
            rows = con.execute(
                'SELECT kind, sku, qty, remaining_lead FROM reorder_queue '
                'WHERE run_id=? AND batch_id=?', (run_id, batch_id)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# ── Score DBs (bin_scores / sku_scores) ───────────────────────────────────────

def save_bin_scores(path: str, run_id: int, records: list[tuple]) -> None:
    """Per-bin static scores.  records: (aisle_id, bayX, bayY, travel_d, height_mult,
    layout_score, map_pref); map_pref None for non-map strategies."""
    if not records:
        return
    con = _open_db(path)
    try:
        con.execute(_CREATE_BIN_SCORES)
        con.executemany(
            'INSERT OR REPLACE INTO bin_scores '
            '(run_id,aisle_id,bayX,bayY,travel_d,height_mult,layout_score,map_pref) '
            'VALUES (?,?,?,?,?,?,?,?)',
            [(run_id, int(a), int(bx), int(by), float(td), float(hm), float(ls),
              None if mp is None else float(mp))
             for (a, bx, by, td, hm, ls, mp) in records],
        )
        con.commit()
    finally:
        con.close()


def load_bin_scores(path: str, run_id: int) -> list[dict]:
    """All per-bin scores for a run; empty list if the table is absent (older runs)."""
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT aisle_id, bayX, bayY, travel_d, height_mult, layout_score, map_pref '
            'FROM bin_scores WHERE run_id=?', (run_id,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def save_sku_scores(path: str, run_id: int, records: list[tuple]) -> None:
    """Per-SKU placement scores.  records: (sku, map_target, labor_cost, handle_var,
    expected_popularity, expected_labor, equilibrium_qty, reorder_point, lead_time_mean)."""
    if not records:
        return
    con = _open_db(path)
    try:
        con.execute(_CREATE_SKU_SCORES)
        con.executemany(
            'INSERT OR REPLACE INTO sku_scores '
            '(run_id,sku,map_target,labor_cost,handle_var,expected_popularity,'
            'expected_labor,equilibrium_qty,reorder_point,lead_time_mean) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            [(run_id, int(sku),
              None if mt is None else float(mt),
              float(lc), float(hv), float(ep), float(el),
              int(eq), int(rp), float(lt))
             for (sku, mt, lc, hv, ep, el, eq, rp, lt) in records],
        )
        con.commit()
    finally:
        con.close()


def load_sku_scores(path: str, run_id: int) -> list[dict]:
    """All per-SKU scores for a run; empty list if the table is absent (older runs)."""
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT * FROM sku_scores WHERE run_id=?', (run_id,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# ── TaskStats DB ──────────────────────────────────────────────────────────────

def save_task_stats(path: str, run_id: int, records: list[TaskStats]) -> None:
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO task_stats '
            '(run_id,batch_id,aisle_id,picker_id,task_start_time,task_end_time,'
            'duration,W,lift_sum,num_bins_visited,total_items,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id, r.picker_id,
                 r.task_start_time, r.task_end_time, r.duration,
                 r.W, r.lift_sum, r.num_bins_visited,
                 r.total_items, int(r.is_outlier))
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_task_stats(path: str, run_id: int) -> list[TaskStats]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT * FROM task_stats WHERE run_id = ?', (run_id,)
        ).fetchall()
        return [
            TaskStats(
                run_id           = row['run_id'],
                batch_id         = row['batch_id'],
                aisle_id         = row['aisle_id'],
                picker_id        = row['picker_id'],
                task_start_time  = row['task_start_time'],
                task_end_time    = row['task_end_time'],
                duration         = row['duration'],
                W              = (row['W'] if 'W' in row.keys()
                                  else (row['W_a'] if 'W_a' in row.keys() else 0.0)),
                lift_sum         = row['lift_sum'],
                num_bins_visited = row['num_bins_visited'],
                total_items      = row['total_items'],
                is_outlier       = bool(row['is_outlier']),
            )
            for row in rows
        ]
    finally:
        con.close()


def save_picker_events(path: str, run_id: int, records: list) -> None:
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO picker_events '
            '(run_id,batch_id,picker_id,time,event_type,aisle_id,bayX,bayY,'
            'sku,quantity,bins_completed,total_bins,items_picked,total_items,'
            'pick_travel_x,pick_travel_y,non_pick_travel_x,non_pick_travel_y,cart_move) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.picker_id, r.time, r.event_type,
                 r.aisle_id, r.bayX, r.bayY, r.sku, r.quantity,
                 r.bins_completed, r.total_bins, r.items_picked, r.total_items,
                 r.pick_travel_x, r.pick_travel_y, r.non_pick_travel_x,
                 r.non_pick_travel_y, r.cart_move)
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def save_picks(path: str, run_id: int, records: list) -> None:
    """Persist individual pick events extracted from the picker event stream."""
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO picks '
            '(run_id,batch_id,picker_id,sim_time,aisle_id,bayX,bayY,sku,quantity) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.picker_id, r.sim_time,
                 r.aisle_id, r.bayX, r.bayY, r.sku, r.quantity)
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_picker_events(path: str, run_id: int, batch_id: int | None = None) -> list:
    """Load PickerEventRecord rows for *run_id*, optionally filtered to one batch.

    Returns records ordered by (batch_id, picker_id, time) for sequential replay.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        if batch_id is None:
            rows = con.execute(
                'SELECT * FROM picker_events WHERE run_id = ? '
                'ORDER BY batch_id, picker_id, time',
                (run_id,),
            ).fetchall()
        else:
            rows = con.execute(
                'SELECT * FROM picker_events WHERE run_id = ? AND batch_id = ? '
                'ORDER BY picker_id, time',
                (run_id, batch_id),
            ).fetchall()
        # Travel-decomposition columns are recent; a legacy DB lacks them, so read defensively
        # (sqlite3.Row raises on a missing key) and fall back to 0.0.
        cols = set(rows[0].keys()) if rows else set()
        def _g(row, col):
            return row[col] if col in cols else 0.0
        return [
            PickerEventRecord(
                run_id         = row['run_id'],
                batch_id       = row['batch_id'],
                picker_id      = row['picker_id'],
                time           = row['time'],
                event_type     = row['event_type'],
                aisle_id       = row['aisle_id'],
                bayX           = row['bayX'],
                bayY           = row['bayY'],
                sku            = row['sku'],
                quantity       = row['quantity'],
                bins_completed = row['bins_completed'],
                total_bins     = row['total_bins'],
                items_picked   = row['items_picked'],
                total_items    = row['total_items'],
                pick_travel_x     = _g(row, 'pick_travel_x'),
                pick_travel_y     = _g(row, 'pick_travel_y'),
                non_pick_travel_x = _g(row, 'non_pick_travel_x'),
                non_pick_travel_y = _g(row, 'non_pick_travel_y'),
                cart_move         = _g(row, 'cart_move'),
            )
            for row in rows
        ]
    finally:
        con.close()


# ── BinInventory DB ───────────────────────────────────────────────────────────

def save_bin_inventory(path: str, run_id: int, records: list) -> None:
    """Persist pre/post batch bin inventory snapshots.

    Each record covers one non-empty bin for one batch: pre_qty is the
    quantity after check_reorders() (before picks), post_qty is the
    quantity after all picks are applied.  Bins empty throughout are omitted.
    """
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO bin_inventory '
            '(run_id,batch_id,aisle_id,bayX,bayY,sku,unit_type,storage_size,'
            'pre_qty,post_qty) VALUES (?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id, r.bayX, r.bayY,
                 r.sku, r.unit_type, r.storage_size, r.pre_qty, r.post_qty)
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_bin_inventory(
    path     : str,
    run_id   : int,
    batch_id : int | None = None,
    aisle_id : int | None = None,
) -> list:
    """Load BinInventoryRecord rows, optionally filtered to one batch or aisle."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        if batch_id is not None and aisle_id is not None:
            rows = con.execute(
                'SELECT * FROM bin_inventory '
                'WHERE run_id=? AND batch_id=? AND aisle_id=? '
                'ORDER BY bayX, bayY',
                (run_id, batch_id, aisle_id),
            ).fetchall()
        elif batch_id is not None:
            rows = con.execute(
                'SELECT * FROM bin_inventory '
                'WHERE run_id=? AND batch_id=? '
                'ORDER BY aisle_id, bayX, bayY',
                (run_id, batch_id),
            ).fetchall()
        else:
            rows = con.execute(
                'SELECT * FROM bin_inventory WHERE run_id=? '
                'ORDER BY batch_id, aisle_id, bayX, bayY',
                (run_id,),
            ).fetchall()
        return [
            BinInventoryRecord(
                run_id       = row['run_id'],
                batch_id     = row['batch_id'],
                aisle_id     = row['aisle_id'],
                bayX         = row['bayX'],
                bayY         = row['bayY'],
                sku          = row['sku'],
                unit_type    = row['unit_type'],
                storage_size = row['storage_size'],
                pre_qty      = row['pre_qty'],
                post_qty     = row['post_qty'],
            )
            for row in rows
        ]
    finally:
        con.close()


# ── AisleMetrics DB ───────────────────────────────────────────────────────────

def save_aisle_metrics(path: str, run_id: int, records: list) -> None:
    """Persist per-aisle trip-cost equation state snapshots.

    Captured once per batch after check_reorders() — reflects the warehouse
    layout as it evolves under the assignment function.  Strategy A rows carry
    zeros because affinity state is not maintained for uniform placement.
    """
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT OR REPLACE INTO aisle_metrics '
            '(run_id,batch_id,aisle_id,n_skus,n_bins,demand_sum,lift_sum,pick_load_sum) '
            'VALUES (?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id,
                 r.n_skus, r.n_bins, r.demand_sum, r.lift_sum,
                 getattr(r, 'pick_load_sum', 0.0))
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_aisle_metrics(
    path     : str,
    run_id   : int,
    batch_id : int | None = None,
    aisle_id : int | None = None,
) -> list:
    """Load AisleMetricRecord rows, optionally filtered to one batch or aisle."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        if batch_id is not None and aisle_id is not None:
            rows = con.execute(
                'SELECT * FROM aisle_metrics WHERE run_id=? AND batch_id=? AND aisle_id=?',
                (run_id, batch_id, aisle_id),
            ).fetchall()
        elif batch_id is not None:
            rows = con.execute(
                'SELECT * FROM aisle_metrics WHERE run_id=? AND batch_id=? '
                'ORDER BY aisle_id',
                (run_id, batch_id),
            ).fetchall()
        elif aisle_id is not None:
            rows = con.execute(
                'SELECT * FROM aisle_metrics WHERE run_id=? AND aisle_id=? '
                'ORDER BY batch_id',
                (run_id, aisle_id),
            ).fetchall()
        else:
            rows = con.execute(
                'SELECT * FROM aisle_metrics WHERE run_id=? '
                'ORDER BY batch_id, aisle_id',
                (run_id,),
            ).fetchall()
        return [
            AisleMetricRecord(
                run_id        = row['run_id'],
                batch_id      = row['batch_id'],
                aisle_id      = row['aisle_id'],
                n_skus        = row['n_skus'],
                n_bins        = row['n_bins'],
                demand_sum    = row['demand_sum'],
                lift_sum      = row['lift_sum'],
                pick_load_sum = (row['pick_load_sum']
                                 if 'pick_load_sum' in row.keys() else 0.0),
            )
            for row in rows
        ]
    finally:
        con.close()


# ── bin-mutation log: records + savers ────────────────────────────────────────

@dataclass
class BinPlacementRecord:
    run_id:   int
    batch_id: int
    seq:      int
    aisle_id: int
    bayX:     int
    bayY:     int
    sku:      int
    qty:      int
    cause:    str    # 'initial' | 'reorder' | 'reslot'


@dataclass
class BinEvictionRecord:
    run_id:   int
    batch_id: int
    seq:      int
    aisle_id: int
    bayX:     int
    bayY:     int
    sku:      int
    qty:      int


def save_bin_placements(path: str, run_id: int, records: list) -> None:
    """Persist PLACE events — the term the record was missing."""
    if not records:
        return
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT OR REPLACE INTO bin_placement '
            '(run_id, batch_id, seq, aisle_id, bayX, bayY, sku, qty, cause) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [(run_id, r.batch_id, r.seq, r.aisle_id, r.bayX, r.bayY, r.sku, r.qty, r.cause)
             for r in records])
        con.commit()
    finally:
        con.close()


def save_bin_evictions(path: str, run_id: int, records: list) -> None:
    """Persist EVICT events.  Zero rows on a `norsl` arm, which is every shipped arm today."""
    if not records:
        return
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT OR REPLACE INTO bin_eviction '
            '(run_id, batch_id, seq, aisle_id, bayX, bayY, sku, qty) VALUES (?,?,?,?,?,?,?,?)',
            [(run_id, r.batch_id, r.seq, r.aisle_id, r.bayX, r.bayY, r.sku, r.qty)
             for r in records])
        con.commit()
    finally:
        con.close()


def load_bin_placements(path: str, run_id: int, batch_id: int | None = None) -> list:
    """PLACE events, ordered as applied."""
    con = _ro_conn(path)
    try:
        sql = ('SELECT batch_id, seq, aisle_id, bayX, bayY, sku, qty, cause FROM bin_placement '
               'WHERE run_id=?')
        args = [run_id]
        if batch_id is not None:
            sql += ' AND batch_id=?'
            args.append(batch_id)
        return [dict(r) for r in con.execute(sql + ' ORDER BY batch_id, seq', args)]
    finally:
        con.close()


def load_bin_evictions(path: str, run_id: int, batch_id: int | None = None) -> list:
    con = _ro_conn(path)
    try:
        sql = ('SELECT batch_id, seq, aisle_id, bayX, bayY, sku, qty FROM bin_eviction '
               'WHERE run_id=?')
        args = [run_id]
        if batch_id is not None:
            sql += ' AND batch_id=?'
            args.append(batch_id)
        return [dict(r) for r in con.execute(sql + ' ORDER BY batch_id, seq', args)]
    finally:
        con.close()
