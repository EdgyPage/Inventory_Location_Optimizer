import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from Schema import capability as _capability
from Schema import compat as _compat
from Schema import dataset as _dataset
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
    # ── the demand side ──────────────────────────────────────────────────────────
    # Units this batch ASKED for, against `total_items` = units it got.  Until this
    # existed the requested quantity never left `Task.from_batch` (it was a loop-local
    # `remaining`, overwritten per SKU), so an arm that placed nothing for a SKU and an
    # arm that placed 500 of it produced identical records -- and every rate in
    # `frames.py` divides by what was PICKED, which makes failing to pick look cheap.
    # 0 on a pre-column vintage; `items_demanded >= total_items` always.
    items_demanded: int = 0
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
    """One archived `bin_inventory` row.  **ARCHIVE-ONLY — no run writes this table any more.**

    Kept, with `load_bin_inventory`, because the ~500 GB archive predates the bin-mutation log
    and `bin_inventory` is the only depletion record those files will ever have.  New runs record
    `bin_placement` + `bin_eviction` + `picks` instead, which reconstruct bin state exactly at
    every batch rather than approximately between keyframes.
    """
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


@dataclass(slots=True)
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
        items_demanded         INTEGER NOT NULL DEFAULT 0,
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

#: ONE row per event from ANY work stream, on the run's absolute axis.
#:
#: Separate from `picker_events` rather than an extension of it, and the reason is that
#: table's nine pick-specific `NOT NULL DEFAULT 0` columns: a put-away row would be nine
#: zeros with no way to tell "this actor carries no cart" from "cart_move was 0.0".
#: `picker_events` stays exactly as it is -- batch-relative, pick-only, dense picker_id --
#: so every existing analysis, figure and viewer route keeps working untouched.
#:
#: TWO ID SPACES, deliberately (see Warehouse/operations/worker.py).  `actor_local` is dense
#: within one crew and is the id `picker_events` carries; `actor_uid` is unique across every
#: crew in the run.  A uid leaking into a local slot lands inside [0, k) and is accepted, so
#: they are two named columns rather than one encoding.
#:
#: `qty` is SIGNED -- negative for a pick, positive for a put -- so both streams are one row
#: shape read in opposite directions, and SUM(qty) over a bin is its net movement.
#:
#: SCOPE: a work unit is (pair, config, channel, strategy) and each is a separate process
#: writing its own sim_<strategy>.db.  Store and fulfillment run INDEPENDENT batch streams
#: with different counts and makespans, so this timeline is per-ARM.  Two arms' t_abs values
#: are not comparable and a cross-channel Gantt built from them would be fiction.
_CREATE_WORK_EVENTS = """
    CREATE TABLE IF NOT EXISTS work_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id    INTEGER NOT NULL,
        seq         INTEGER NOT NULL,           -- order within one instant; see the view
        t_abs       REAL    NOT NULL,           -- seconds since the arm's start
        t_local     REAL    NOT NULL,           -- seconds since this batch's start
        shift_index INTEGER NOT NULL,           -- floor(t_abs / shift_seconds); a LABEL
        actor_uid   INTEGER NOT NULL,           -- unique across every crew in the run
        actor_local INTEGER NOT NULL,           -- dense within this actor's own crew
        role        TEXT    NOT NULL,           -- 'pick' | 'put'
        mode        TEXT    NOT NULL,           -- 'foot' | 'machine'
        event_type  TEXT    NOT NULL,
        aisle_id    INTEGER,
        sku         INTEGER,
        qty         INTEGER,                    -- SIGNED: pick < 0, put > 0
        duration    REAL    NOT NULL DEFAULT 0,
        source      TEXT                        -- put-away origin: intake|reorder|reslot
    )
"""

_CREATE_WORK_EVENTS_IDX = """
    CREATE INDEX IF NOT EXISTS ix_we_run_batch
    ON work_events (run_id, batch_id)
"""

_CREATE_WORK_EVENTS_TIME_IDX = """
    CREATE INDEX IF NOT EXISTS ix_we_run_tabs
    ON work_events (run_id, t_abs)
"""

#: The merged stream, with its order DECLARED.
#:
#: `PickEvent.__lt__` compares time alone, so ties have always fallen through to a stable
#: sort into phase-1 picker order -- correct and reproducible, and nowhere written down.
#: One stream could live with that; two cannot, because the tie-break then decides whether a
#: pick or a put is read first at the same instant.  So it is stated: instant, then role,
#: then BATCH, then role, then mode, then actor, then emission order within that actor.
#:
#: `batch_id` is in the key because `seq` restarts at 0 every batch, and consecutive
#: batches genuinely touch: the arm advances by `batch_start + duration`, which IS the last
#: `done` instant, so batch i's final `done` and batch i+1's first `task_start` for the same
#: picker share a `t_abs`, a role, a mode and an actor.  Without `batch_id` the tie fell to
#: `seq` -- large for the `done`, near 0 for the `task_start` -- and ordered them backwards,
#: which is the exact opposite of the "emission order" this key claims to deliver.
_CREATE_WORK_EVENTS_MERGED = """
    CREATE VIEW IF NOT EXISTS work_events_merged AS
    SELECT * FROM work_events
    ORDER BY t_abs, batch_id, role, mode, actor_uid, seq
"""

_CREATE_PICKER_EVENTS_IDX = """
    CREATE INDEX IF NOT EXISTS ix_pe_run_batch
    ON picker_events (run_id, batch_id)
"""

_CREATE_PICKER_EVENTS_TIME_IDX = """
    CREATE INDEX IF NOT EXISTS ix_pe_run_batch_time
    ON picker_events (run_id, batch_id, time)
"""

# ── bin_inventory — SUNSET.  Read-only, archive-only; no DDL, no writer. ──────
#
# This build does not create the table and never inserts into it.  It was pure redundancy:
# it records picks and NEVER restocks, and `picks` already holds every decrement at better
# (sim_time) resolution — measured, SUM(pre_qty - post_qty) equals SUM(picks.quantity)
# exactly on every arm tested.  The bin-mutation log below supersedes it outright.
#
# `load_bin_inventory` / `BinInventoryRecord` REMAIN because the ~500 GB archive has no log:
# for those arms this table is the only depletion record they will ever have, and deleting
# the reader path would make the archive unreadable.  Its shape there, frozen forever:
#
#   CREATE TABLE bin_inventory (
#       id           INTEGER PRIMARY KEY AUTOINCREMENT,
#       run_id       INTEGER NOT NULL REFERENCES simulation_runs(run_id),
#       batch_id     INTEGER NOT NULL,
#       aisle_id     INTEGER NOT NULL,   bayX INTEGER NOT NULL,  bayY INTEGER NOT NULL,
#       sku          INTEGER NOT NULL,
#       unit_type    TEXT    NOT NULL,   storage_size TEXT NOT NULL,
#       pre_qty      INTEGER NOT NULL,   -- after check_reorders(), before picks
#       post_qty     INTEGER NOT NULL)   -- after all picks applied
#   CREATE INDEX ix_bi_run_batch       ON bin_inventory (run_id, batch_id)
#   CREATE INDEX ix_bi_run_batch_aisle ON bin_inventory (run_id, batch_id, aisle_id)
#
# Two ordering traps when reading an archived one: order by `batch_id, id` (a bin can take
# rows from two branches in one batch, so `batch_id` alone is not a total order), and the
# full snapshot sits at the run's FIRST batch, which on a resumed arm is not batch 0.


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

    ── Why NONE of this module's 28 closes goes through `Schema.connect.close` ──────────
    Deliberate, and measured — not an oversight.  The closes here fall into three groups:

      13 are `load_*` / `find_run` / `run_identity` / the two `declared_*_shape` helpers.
         Read-only or `:memory:`.  There is no WAL to fold back; converting them is noise.

      12 are the per-flush writers — `save_batch_stats`, `save_task_stats`,
         `save_picker_events`, `save_picks`, `save_bin_placements`, `save_bin_evictions`,
         `save_aisle_metrics`, `save_reorder_queue`, `save_bin_keyframe`, and the two score
         writers.  Each opens and closes ONCE PER CHECKPOINT FLUSH (default every 10 batches,
         `strategy_runner.py:583`) against a sim DB that reaches ~1 GB.  A
         `wal_checkpoint(TRUNCATE)` there would fold the whole WAL into the main file every
         flush, on the hot path of a 40-minute arm, for no benefit — see below.

       3 are one-time setup: `init_run_db`, `create_run`, `init_keyframe_db`.  The connection
         is finished but the FILE is not; the run writes to it for the next 40 minutes, so a
         checkpoint changes nothing about the state that is left behind.

    And the benefit really is nil, because a plain close already does the job here.  Measured:
    on a clean single-process write-then-close, SQLite removes `-wal`/`-shm` itself, and
    `connect.close` removes exactly the same set.  What actually strands them is a READER —
    a `mode=ro` connection on a WAL database creates `-shm` and cannot delete it on the way
    out, so every archived sim DB grows sidecars the moment anything fingerprints or plots it.
    No change on the WRITE side can prevent that; only `immutable=1` on the read side can, and
    that is a promise about the file that a live run cannot make.  The one writer here whose
    close is genuinely the file's last is in `Warehouse_Data`, which does convert.
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
# The record `bin_inventory` could not give: it logged picks and NEVER restocks, because
# check_reorders() runs before the pre-batch snapshot, so a restocked bin was already in it at
# its post-restock quantity and the `post_qty == pre_qty` skip then dropped it.  Measured on a
# production arm: 0 rows with post_qty > pre_qty against 20k-42k reorder_placements per batch.
# Rolling that delta stream forward from a keyframe only ever DECAYED — losing 59% of the
# warehouse in 5 batches.  These two tables replace it (see the sunset note above).
#
# Bin state changes at exactly five sites in the codebase (one of them dead code), and picks
# are already fully recorded — verified, SUM(pre_qty-post_qty) equals SUM(picks.quantity)
# exactly on every arm tested.  So PLACE + EVICT + PICK is complete BY CONSTRUCTION, which
# Tests/integration/test_bin_log_replay.py checks against a live sim and the per-batch
# conservation ledger in strategy_runner re-checks at runtime on every real run.
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
        -- The number the assignment policy MINIMISED (or maximised) to choose this bin,
        -- captured at the moment of choice.  Units differ by policy and are not comparable
        -- across arms: seconds of anchor gap for the map policies, seconds of marginal
        -- labor for the balancers, seconds of column distance for compaction.  `policy`
        -- says which.  NULL means no score was available, which happens three ways: a
        -- non-scoring policy (uniform/FIFO), the per-unit straggler path taken when a
        -- group's snapshot is exhausted, or a policy with nothing to report for that unit
        -- (an unmapped SKU, a cold start with no partner placed yet, a capped least-prime
        -- fallback chosen on a different rule).  A zero would claim a perfect placement.
        score      REAL,
        -- 0-based rank of `score` among the scored placements of the same GROUP, best
        -- first in that policy's own direction — so rank 0 is always the best choice
        -- available at that moment, whether the policy minimises or maximises.  This is
        -- the only record of group membership: `bin_placement` has no group column, so a
        -- consumer cannot recover the ranking from `score` alone.  It is also the column
        -- the FIFO work is aimed at — under ranked ordering, rank correlates with queue
        -- position; under a FIFO window it should not.
        score_rank INTEGER,
        -- Which assignment policy chose it.  Constant per run today (simulation_runs
        -- already names the strategy) and deliberately on the row anyway: put-away queues
        -- get their own policies next, at which point one run writes several.
        policy     TEXT,
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
    con.execute(_CREATE_WORK_EVENTS)
    con.execute(_CREATE_WORK_EVENTS_IDX)
    con.execute(_CREATE_WORK_EVENTS_TIME_IDX)
    con.execute(_CREATE_WORK_EVENTS_MERGED)
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
    """Canonical shape of the sibling keyframe DB.

    Includes the `schema_meta` stamp table: a family that declares `meta_table` must DECLARE the
    table in its own shape, or the observed shape (with it, once stamped) would never match the
    declaration (see `identity.META_TABLE_DDL`).
    """
    con = sqlite3.connect(':memory:')
    try:
        con.execute(_CREATE_BIN_KEYFRAME)
        con.execute(_CREATE_BIN_KEYFRAME_IDX)
        con.execute(_identity.meta_ddl())
        return _shape.canonical_shape(con)
    finally:
        con.close()


#: The shape every run in the archive was written with, before `sim_schema_id` existed.  Frozen:
#: it cannot be recomputed from today's source, and those files are never rewritten.
PRE_STAMP_SIM_SCHEMA_ID = '23d0c7f167bc'

def _read_sim_stamp(con) -> str | None:
    """The `simulation_runs.sim_schema_id` a run stamped, or None.

    The sim DB's stamp is a COLUMN VALUE (written by `create_run`), not a meta table, so the
    generic `identity.read_stamp` cannot find it without this.  THE single implementation:
    the viewer once kept its own byte-equivalent copy and now resolves through
    `identity.resolve` with this family, so every consumer dates the archive identically.
    """
    row = con.execute(
        'SELECT sim_schema_id FROM simulation_runs '
        'WHERE sim_schema_id IS NOT NULL ORDER BY run_id LIMIT 1').fetchone()
    return row[0] if row else None


SIM_DB_FAMILY = _identity.register(_identity.Family(
    name='sim_db',
    declared_shape=declared_sim_schema_shape,
    meta_table=None,                 # stamped into simulation_runs.sim_schema_id, not a meta table
    stamp_reader=_read_sim_stamp,    # ...which this teaches identity.resolve to read
    # Every shape this build still opens, newest first.  Each entry is a real window of commits
    # that produced real files; dropping one orphans them, so entries are added, never replaced.
    #   2b7913bcd7e6  the stamp column, before the bin-mutation log
    #   ee5ebabe74fb  the log ADDED, bin_inventory still written (the overlap window)
    # Both surviving vintages of the live archive re-derive to entries in this list: the
    # 2026-07-29 runs to PRE_STAMP_SIM_SCHEMA_ID and the 2026-08-13 runs to ee5ebabe74fb.
    #   6ad0b34af9f1  the bin-mutation log + dossier era, before `work_events`: every run
    #                 from the log's arrival through 2026-08-24.  This is the shape the
    #                 whole published archive was written with.
    #   1a594605a10e  work_events arrived; batch_stats still had no demand side.  A
    #                 short window (2026-08-24) -- no published run used it.
    #   96b8e37f158d  batch_stats gained items_demanded, before bin_placement carried the
    #                 placement score.  A short window (2026-08-22 .. 2026-08-24) -- no
    #                 published run used it.
    known_ids=('96b8e37f158d',
              '1a594605a10e',
              '6ad0b34af9f1',
               PRE_STAMP_SIM_SCHEMA_ID, '2b7913bcd7e6', 'ee5ebabe74fb'),
))

#: Three shapes that ALSO exist in the cold archive and are DELIBERATELY NOT vetted.  Derived
#: from real files, recorded here so nobody re-derives them and assumes the omission was an
#: oversight — every one of them is missing a `batch_stats` column that a loader silently
#: defaults to `0.0`, which is the exact failure this family exists to make loud:
#:   5d8a78b74466  comparison_whatif_20260709_015101   -.
#:   89c7b2babf22  comparison_2026070{6,8}_*            |- no task_makespan / thr_task / thr_batch
#:   e110afa222e3  comparison_20260623_150217          -'  / skus_reordered / units_ordered
#: Analysing one of those through `Performance_Evaluations` would publish a throughput of zero.
#: `Diagnostics/replay_run.py` still reads them, and still may: it does not touch batch_stats'
#: success metrics and captions every curve with the source it actually used.
UNVETTED_ARCHIVE_SIM_SCHEMA_IDS = ('5d8a78b74466', '89c7b2babf22', 'e110afa222e3')

# ── what the shared read layer needs, and what it may only ASK for ──────────────────────────
# `SIM_DB_FAMILY` above vets a whole FILE; these say what a caller may read out of one.  The gap
# between those two granularities is where the silent failure lives: every vetted vintage differs,
# and `check()` passing tells a loader nothing about whether its own columns survived.
#
# Everything here is inside `Schema.compat.guaranteed_surface('sim_db')` — present in EVERY vetted
# shape — so these loaders are version-free by construction and
# `Tests/architecture/test_schema_compatibility.py` fails if a schema change makes that untrue.
#
# The guarded columns are listed DELIBERATELY. `load_batch_stats`/`load_task_stats` default them to
# 0.0 when absent, and seven of them (`sigma_fd`, `W`, `queue_depth`, `reorder_placements`,
# `reload_moves`, `lead_queue_depth`, `in_transit_qty`) flow straight into a published figure or
# CSV — so "guarded" means the failure is silent, not that it is safe.  Declaring them turns a
# dropped column into a CI failure instead of a plausible zero.  (`task_makespan` is the one that
# fails safe: `common/frames.py` converts its 0.0 to NaN, which drops out of summaries.)
REQUIRES = _compat.Requires(
    family='sim_db',
    label='Picking_Data shared read layer',
    tables={
        'batch_stats': ('run_id', 'batch_id', 'duration', 'num_tasks', 'total_items',
                        'avg_concurrent_pickers', 'picking_pct', 'traveling_pct', 'is_outlier',
                        'task_makespan', 'thr_task', 'thr_batch', 'batch_start_time',
                        'batch_end_time', 'sigma_fd', 'reload_moves', 'reorder_placements',
                        'skus_reordered', 'units_ordered', 'queue_depth', 'lead_queue_depth',
                        # NOT 'items_demanded': the guaranteed surface is the INTERSECTION
                        # over every vetted vintage, and a column added today is absent from
                        # all of them.  It rides `_BATCH_OPTIONAL`, which defaults it to 0 on
                        # a pre-column file -- the same treatment every other late column got.
                        'in_transit_qty'),
        'task_stats': ('run_id', 'batch_id', 'aisle_id', 'picker_id', 'task_start_time',
                       'task_end_time', 'duration', 'lift_sum', 'num_bins_visited', 'total_items',
                       'is_outlier', 'W'),
        'picker_events': ('run_id', 'batch_id', 'picker_id', 'time', 'event_type', 'aisle_id',
                          'bayX', 'bayY', 'sku', 'quantity', 'bins_completed', 'total_bins',
                          'items_picked', 'total_items', 'pick_travel_x', 'pick_travel_y',
                          'non_pick_travel_x', 'non_pick_travel_y', 'cart_move'),
        'aisle_metrics': ('run_id', 'batch_id', 'aisle_id', 'n_skus', 'n_bins', 'demand_sum',
                          'lift_sum', 'pick_load_sum'),
        # `unit_type`/`storage_size` are in the PRIMARY select; the inner OperationalError
        # fallback re-queries without them for a pre-enrichment file.  Both are nonetheless in
        # the guaranteed surface — every vetted vintage has them — so the fallback is dead code
        # against anything this family still vets, and declaring them says so.
        'reorder_queue': ('run_id', 'batch_id', 'kind', 'sku', 'qty', 'remaining_lead',
                          'unit_type', 'storage_size'),
        'bin_scores': ('run_id', 'aisle_id', 'bayX', 'bayY', 'travel_d', 'height_mult',
                       'layout_score', 'map_pref'),
        # `SELECT *` -> dict(row); no column is indexed here, so the requirement is the table.
        'sku_scores': _compat.ANY_COLUMNS,
        # `run_identity` does `SELECT *` and indexes 13 columns off the row, each behind an
        # `if k in row.keys()` guard — so a missing one is dropped from the returned dict rather
        # than raising, and the caller sees an absence it cannot distinguish from a NULL.  All of
        # these are guaranteed; the fourteenth, `sim_schema_id`, is NOT, which is why it is in
        # CONDITIONAL_READS below instead of here.  `find_run` adds `strategy_key`.
        'simulation_runs': ('run_id', 'run_type', 'n_batches', 'keyframe_interval',
                            'optimal_sigma_fd', 'optimal_work', 'strategy_key', 'pair_label',
                            'config_label', 'warehouse_fingerprint', 'inventory_label',
                            'channel'),
    })

#: Loaders that read the CONDITIONAL surface — a table only SOME vetted vintages have.  Kept as
#: data so the compatibility test can assert the list is exhaustive: a NEW conditional reader added
#: without a decision fails there rather than raising `no such table` months later, on the archive.
#:
#: None of the first three is guarded at all.  That is correct — they are the archive-replay and
#: viewer paths, whose callers already negotiate (`Diagnostics/replay_run._SOURCES` probes before
#: reading, and degrades with a recorded caveat).  A caller that cannot negotiate must not call
#: them.  `run_identity` is the exception: its `sim_schema_id` read is guarded by `row.keys()` and
#: simply omits the key on a pre-stamp file.
CONDITIONAL_READS = {
    'load_bin_inventory':  'bin_inventory',    # retired; archive-only
    'load_bin_placements': 'bin_placement',    # added 2026-08-13
    'load_bin_evictions':  'bin_eviction',     # added 2026-08-13
    'run_identity':        'simulation_runs.sim_schema_id',
}

# ── the sim DB's capabilities: what a consumer may NEGOTIATE for ────────────────────────────
# The registry lives here, beside the DDL that defines these tables, because `Schema/` is the
# stdlib-only leaf and must not learn what a warehouse is.  It owns the TYPE and the row probe;
# this owns which tables exist and what each is worth.
#
# Every entry is either on the conditional surface (some vetted vintages lack the table) or is
# present-everywhere-but-usually-EMPTY, which for a consumer deciding whether it can draw
# something is the same fact.  Both are why the probe checks for ROWS, not for the table.
#
# `exact`, `phase` and `caveat` are payload, not prose: a consumer that selects one of these is
# expected to carry `capability.provenance(cap)` into whatever it emits.
CAP_BIN_LOG = 'bin_log'
CAP_AISLE_METRICS = 'aisle_metrics'
CAP_BIN_INVENTORY = 'bin_inventory'
CAP_BIN_SCORES = 'bin_scores'
CAP_SKU_SCORES = 'sku_scores'
CAP_REORDER_QUEUE = 'reorder_queue'
CAP_WORK_EVENTS = 'work_events'      # the merged cross-stream timeline
CAP_KEYFRAMES = 'keyframes'          # a sibling .keyframes.db — not table-probed
CAP_VIZ_CACHE = 'viz_cache'          # a FRESH derived sidecar — not table-probed

SIM_CAPABILITIES = {c.name: c for c in (
    _capability.Capability(
        name=CAP_WORK_EVENTS, table='work_events', exact=True,
        phase='per-batch, appended at each checkpoint flush',
        caveat='PER-ARM TIMELINE. A work unit is (pair, config, channel, strategy) and each '
               'is a separate process writing its own DB; store and fulfillment run '
               'INDEPENDENT batch streams with different counts and makespans. t_abs is '
               "seconds since THIS arm's start, so two arms' values are not comparable and a "
               'cross-channel Gantt built from them is fiction. shift_index is a LABEL over a '
               'continuous clock -- nothing dispatches against it, work does not pause at a '
               'boundary, and a task spanning one is recorded under the shift it STARTED in. '
               'qty is SIGNED (pick < 0, put > 0). The put-away cost model is PROVISIONAL: it '
               'mirrors the pick model, charges every put from the aisle mouth, and models no '
               'contention between the two crews.',
        columns=('run_id', 'batch_id', 'seq', 't_abs', 't_local', 'shift_index',
                 'actor_uid', 'actor_local', 'role', 'mode', 'event_type', 'aisle_id',
                 'sku', 'qty', 'duration', 'source')),
    _capability.Capability(
        name=CAP_BIN_LOG, table='bin_placement', exact=True,
        phase="end-of-batch (after this batch's picks)",
        caveat='',
        columns=('run_id', 'batch_id', 'seq', 'aisle_id', 'bayX', 'bayY', 'sku', 'qty', 'cause')),
    _capability.Capability(
        name=CAP_AISLE_METRICS, table='aisle_metrics', exact=False,
        phase="start-of-batch (after restock, BEFORE this batch's picks)",
        caveat="APPROXIMATE. n_bins is the manager's own occupied-bin counter, sampled after the "
               "restock pass and before the batch's picks, so it describes a different instant "
               'than the pick-based sources and it lags a bin emptied by a pick. Only strategies '
               'that maintain aisle state write this table at all.',
        columns=('run_id', 'batch_id', 'aisle_id', 'n_bins')),
    _capability.Capability(
        name=CAP_BIN_INVENTORY, table='bin_inventory', exact=False,
        phase="end-of-batch (after this batch's picks)",
        # Carries the MECHANISM and the measurement, not just the verdict.  An earlier draft
        # trimmed both, and a caveat that asserts a bias without the evidence for it is the kind
        # of sentence a reader talks themselves out of.  This is also the exact body
        # `Diagnostics/replay_run.py` has always exported, so the registry can be its one source.
        caveat='ARCHIVE-ONLY (no run writes this table any more). APPROXIMATE AND BIASED '
               'DOWNWARD. bin_inventory records picks and NEVER restocks '
               '(check_reorders() runs before the pre-batch snapshot, and the post_qty==pre_qty '
               'skip then drops the restocked bin): measured on a production arm, 0 rows with '
               'post_qty > pre_qty against 20,662-42,832 reorder_placements per batch. Rolling '
               'these deltas forward can only DECAY occupancy - 68,271 occupied bins against a '
               'true 165,519 five batches past a keyframe. Treat the SHAPE of this curve as '
               'wrong, not merely noisy.',
        columns=('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'pre_qty', 'post_qty')),
    _capability.Capability(
        name=CAP_BIN_SCORES, table='bin_scores', exact=True,
        phase='static (per run)', caveat=''),
    _capability.Capability(
        name=CAP_SKU_SCORES, table='sku_scores', exact=True,
        phase='static (per run)', caveat=''),
    _capability.Capability(
        name=CAP_REORDER_QUEUE, table='reorder_queue', exact=True,
        phase='start-of-batch', caveat=''),
    _capability.Capability(
        name=CAP_KEYFRAMES, table=None, exact=True,
        phase='the keyframe batch itself', caveat=''),
    _capability.Capability(
        name=CAP_VIZ_CACHE, table=None, exact=True,
        phase='derived (rebuildable)', caveat=''),
)}

#: Occupancy sources, BEST FIRST.  Ordering is a property of the QUESTION, not of the sources, so
#: it lives with the consumer's intent rather than in the registry: an exact end-of-batch fold
#: beats a start-of-batch counter, which beats a downward-biased delta roll.
OCCUPANCY_LADDER = tuple(SIM_CAPABILITIES[n] for n in
                         (CAP_BIN_LOG, CAP_AISLE_METRICS, CAP_BIN_INVENTORY))

# ── the sim DB's NAMED QUERIES: the versioned read layer (publisher side) ───────────────────
# Consumers (the loaders below, and through them all of Performance_Evaluations) bind to the
# LOGICAL output columns declared here, never to physical schema.  A future vintage that renames
# or drops a physical column gets a `_dataset.override(...)` registered FOR ITS SCHEMA ID in a
# small module beside this one — consumers are never edited for a schema change.
#
# `optional` is the registry form of the loaders' historical `row.keys()` guards: a vintage whose
# SQL cannot supply the column has it filled with the declared default, so the output contract is
# identical on every servable vintage.  Today every column below is in the GUARANTEED surface
# (compat REQUIRES validates clean), so the canonical SQL serves all four vetted vintages and no
# override exists yet — the machinery is exercised by tests until the first real rename.
#
# The legacy aliases (`sigma_fw`, `W_a`) are NOT here: they exist only in UNVETTED cold-archive
# shapes, which `dataset.bind` refuses by design.  Those files are served by the loaders' frozen
# legacy fallback bodies below, never by the registry.
_BATCH_OPTIONAL = {'task_makespan': 0.0, 'thr_task': 0.0, 'thr_batch': 0.0,
                   'batch_start_time': 0.0, 'batch_end_time': 0.0, 'sigma_fd': 0.0,
                   'reload_moves': 0, 'reorder_placements': 0, 'skus_reordered': 0,
                   'units_ordered': 0, 'queue_depth': 0, 'lead_queue_depth': 0,
                   'in_transit_qty': 0, 'items_demanded': 0}
_BATCH_COLS = ('run_id', 'batch_id', 'duration', 'num_tasks', 'total_items',
               'avg_concurrent_pickers', 'picking_pct', 'traveling_pct', 'is_outlier',
               *_BATCH_OPTIONAL)

_dataset.register_query(_dataset.Query(
    name='batch_frame', family='sim_db',
    sql=('SELECT ' + ', '.join(_BATCH_COLS)
         + ' FROM batch_stats WHERE run_id = :run_id'),
    columns=_BATCH_COLS,
    tables={'batch_stats': _BATCH_COLS},
    optional=_BATCH_OPTIONAL))

_TASK_COLS = ('run_id', 'batch_id', 'aisle_id', 'picker_id', 'task_start_time',
              'task_end_time', 'duration', 'W', 'lift_sum', 'num_bins_visited',
              'total_items', 'is_outlier')

_dataset.register_query(_dataset.Query(
    name='task_frame', family='sim_db',
    sql=('SELECT ' + ', '.join(_TASK_COLS)
         + ' FROM task_stats WHERE run_id = :run_id'),
    # `tables` = what the CANONICAL sql reads — `W` included: a vintage without it is unservable
    # by this SQL and needs an override that omits the column (optional-fill then supplies 0.0).
    columns=_TASK_COLS,
    tables={'task_stats': _TASK_COLS},
    optional={'W': 0.0}))

_EVENT_OPTIONAL = {'pick_travel_x': 0.0, 'pick_travel_y': 0.0, 'non_pick_travel_x': 0.0,
                   'non_pick_travel_y': 0.0, 'cart_move': 0.0}
_EVENT_COLS = ('run_id', 'batch_id', 'picker_id', 'time', 'event_type', 'aisle_id',
               'bayX', 'bayY', 'sku', 'quantity', 'bins_completed', 'total_bins',
               'items_picked', 'total_items', *_EVENT_OPTIONAL)

_dataset.register_query(_dataset.Query(
    name='picker_events', family='sim_db',
    # :batch_id IS NULL folds the two legacy query variants into one; within a single batch the
    # unified ORDER BY is identical to the old per-batch (picker_id, time) ordering.
    sql=('SELECT ' + ', '.join(_EVENT_COLS)
         + ' FROM picker_events WHERE run_id = :run_id'
           ' AND (:batch_id IS NULL OR batch_id = :batch_id)'
           ' ORDER BY batch_id, picker_id, time'),
    columns=_EVENT_COLS,
    tables={'picker_events': _EVENT_COLS},
    optional=_EVENT_OPTIONAL))


# ── the VIEWER's named queries (publisher side) ─────────────────────────────────────────────
# Visualization/readers/base.py composes these ONCE per (query, vintage) via `dataset.sql_for`
# and executes them on its own per-request read-only connections (the reader must never hold a
# connection — Flask threads).  Because that path skips `Dataset.query`'s optional-fill, the
# publisher rule for viewer queries is: EVERY variant — canonical and override — emits the
# complete logical column set, inlining defaults as SQL.  Scoped queries take an id list as ONE
# JSON parameter via json_each (JSON1 presence is probed at viewer import; plan parity vs the
# literal IN-list is pinned by Tests/unit/test_viewer_named_queries.py).

_VIEWER_BATCH_COLS = ('batch_id', 'duration', 'num_tasks', 'total_items', 'reorder_placements')
_dataset.register_query(_dataset.Query(
    name='batch_timing', family='sim_db',
    sql=('SELECT ' + ', '.join(_VIEWER_BATCH_COLS)
         + ' FROM batch_stats WHERE run_id = :run_id ORDER BY batch_id'),
    columns=_VIEWER_BATCH_COLS,
    tables={'batch_stats': ('run_id', *_VIEWER_BATCH_COLS)}))

_TIMELINE_COLS = ('time', 'picker_id', 'event_type', 'aisle_id', 'bayX', 'bayY', 'sku',
                  'quantity', 'bins_completed', 'total_bins', 'items_picked', 'total_items')
_dataset.register_query(_dataset.Query(
    name='picker_events_timeline', family='sim_db',
    # One batch's timeline in EVENT order (`time, id`), optionally aisle-scoped — distinct from
    # `picker_events` above, which is the analysis frame (all-batch, picker-major ordering).
    sql=('SELECT ' + ', '.join(_TIMELINE_COLS)
         + ' FROM picker_events WHERE run_id = :run_id AND batch_id = :batch_id'
           ' AND (:aisle_id IS NULL OR aisle_id = :aisle_id) ORDER BY time, id'),
    columns=_TIMELINE_COLS,
    tables={'picker_events': ('run_id', 'batch_id', 'id', *_TIMELINE_COLS)}))

_TASK_ROW_COLS = ('batch_id', 'aisle_id', 'picker_id', 'task_start_time', 'task_end_time',
                  'duration', 'W', 'lift_sum', 'num_bins_visited', 'total_items', 'is_outlier')
# Two variants, deliberately: SQLite does not plan `(:batch_id IS NULL OR batch_id=:batch_id)`
# onto the (run_id, batch_id) index, and /api/tasks?batch= is interactive.
_dataset.register_query(_dataset.Query(
    name='task_rows_at_batch', family='sim_db',
    sql=('SELECT ' + ', '.join(_TASK_ROW_COLS)
         + ' FROM task_stats WHERE run_id = :run_id AND batch_id = :batch_id'
           ' AND (:aisle_id IS NULL OR aisle_id = :aisle_id)'
           ' ORDER BY batch_id, task_start_time'),
    columns=_TASK_ROW_COLS,
    tables={'task_stats': ('run_id', *_TASK_ROW_COLS)}))
_dataset.register_query(_dataset.Query(
    name='task_rows_all', family='sim_db',
    sql=('SELECT ' + ', '.join(_TASK_ROW_COLS)
         + ' FROM task_stats WHERE run_id = :run_id'
           ' AND (:aisle_id IS NULL OR aisle_id = :aisle_id)'
           ' ORDER BY batch_id, task_start_time'),
    columns=_TASK_ROW_COLS,
    tables={'task_stats': ('run_id', *_TASK_ROW_COLS)}))

_dataset.register_query(_dataset.Query(
    name='pick_load_by_aisle', family='sim_db',
    sql=('SELECT aisle_id, COUNT(*) AS picks, SUM(quantity) AS units_picked'
         ' FROM picks WHERE run_id = :run_id AND batch_id = :batch_id GROUP BY aisle_id'),
    columns=('aisle_id', 'picks', 'units_picked'),
    tables={'picks': ('run_id', 'batch_id', 'aisle_id', 'quantity')}))

_dataset.register_query(_dataset.Query(
    name='task_load', family='sim_db',
    # Serves BOTH the viewer's per-batch rollup fallback and precompute's all-batch pass —
    # the one place the OR-form is kept, because precompute streams every batch anyway.
    sql=('SELECT batch_id, aisle_id, COUNT(*) AS visits, SUM(duration) AS task_secs'
         ' FROM task_stats WHERE run_id = :run_id'
         ' AND (:batch_id IS NULL OR batch_id = :batch_id) GROUP BY batch_id, aisle_id'),
    columns=('batch_id', 'aisle_id', 'visits', 'task_secs'),
    tables={'task_stats': ('run_id', 'batch_id', 'aisle_id', 'duration')}))

_dataset.register_query(_dataset.Query(
    name='sku_rank_live', family='sim_db',
    sql=('SELECT sku, COUNT(*) AS picks, SUM(quantity) AS units, MIN(batch_id) AS first_batch,'
         ' MAX(batch_id) AS last_batch FROM picks WHERE run_id = :run_id'
         ' GROUP BY sku ORDER BY units DESC, sku LIMIT :n'),
    columns=('sku', 'picks', 'units', 'first_batch', 'last_batch'),
    tables={'picks': ('run_id', 'batch_id', 'sku', 'quantity')}))

_BIN_SCORE_COLS = ('aisle_id', 'bayX', 'bayY', 'travel_d', 'height_mult', 'layout_score',
                   'map_pref')
_dataset.register_query(_dataset.Query(
    name='bin_scores_scoped', family='sim_db',
    sql=('SELECT ' + ', '.join(_BIN_SCORE_COLS)
         + ' FROM bin_scores WHERE run_id = :run_id AND (:aisles IS NULL OR'
           ' aisle_id IN (SELECT value FROM json_each(:aisles)))'),
    columns=_BIN_SCORE_COLS,
    tables={'bin_scores': ('run_id', *_BIN_SCORE_COLS)}))

_dataset.register_query(_dataset.Query(
    name='sku_series_live', family='sim_db',
    sql=('SELECT sku, batch_id, COUNT(*) AS picks, SUM(quantity) AS units'
         ' FROM picks WHERE run_id = :run_id'
         ' AND sku IN (SELECT value FROM json_each(:skus))'
         ' GROUP BY sku, batch_id ORDER BY sku, batch_id'),
    columns=('sku', 'batch_id', 'picks', 'units'),
    tables={'picks': ('run_id', 'batch_id', 'sku', 'quantity')}))

# ── the keyframes DB's named queries — the first non-sim vocabulary; DDL lives below ────────
_dataset.register_query(_dataset.Query(
    name='keyframe_batches', family='keyframes_db',
    sql=('SELECT DISTINCT batch_id FROM bin_keyframe WHERE run_id = :run_id'
         ' ORDER BY batch_id'),
    columns=('batch_id',),
    tables={'bin_keyframe': ('run_id', 'batch_id')}))

_dataset.register_query(_dataset.Query(
    name='bin_history', family='keyframes_db',
    # The LIVE twin of viz_cache_db's `bin_history`: same logical columns, so the viewer's
    # fallback is a second query, not a shape change.  Each keyframe observation is a
    # degenerate span (t_from == t_to == the observed batch).
    sql=('SELECT batch_id AS t_from, batch_id AS t_to, sku, qty AS qty_at_from'
         ' FROM bin_keyframe WHERE run_id = :run_id AND aisle_id = :aisle_id'
         ' AND bayX = :bayX AND bayY = :bayY ORDER BY batch_id'),
    columns=('t_from', 't_to', 'sku', 'qty_at_from'),
    tables={'bin_keyframe': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'qty')}))

_dataset.register_query(_dataset.Query(
    name='keyframe_bins_nonzero', family='keyframes_db',
    sql=('SELECT aisle_id, bayX, bayY, sku, qty FROM bin_keyframe'
         ' WHERE run_id = :run_id AND batch_id = :batch_id AND qty > 0'),
    columns=('aisle_id', 'bayX', 'bayY', 'sku', 'qty'),
    tables={'bin_keyframe': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'qty')}))

_dataset.register_query(_dataset.Query(
    name='keyframe_state_scoped', family='keyframes_db',
    # No qty>0 predicate: the consumer's Python filter is part of the fold and stays there.
    sql=('SELECT aisle_id, bayX, bayY, sku, qty FROM bin_keyframe'
         ' WHERE run_id = :run_id AND batch_id = :batch_id AND (:aisles IS NULL OR'
         ' aisle_id IN (SELECT value FROM json_each(:aisles)))'),
    columns=('aisle_id', 'bayX', 'bayY', 'sku', 'qty'),
    tables={'bin_keyframe': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'qty')}))


def _query_rows(name: str, path: str, **params):
    """Rows via the named-query registry when this file's vintage is servable, else None.

    None routes the caller to its FROZEN LEGACY body: `Diagnostics/replay_run.py` deliberately
    reads UNVETTED cold-archive vintages (`UNVETTED_ARCHIVE_SIM_SCHEMA_IDS`), which `bind`
    refuses by design — for those, the pre-registry `SELECT *` + `row.keys()` behavior is kept
    verbatim, and a golden test asserts both paths agree on every vetted vintage.
    """
    try:
        # immutable=True, deliberately: the legacy loaders' plain RW connects cleaned their WAL
        # sidecars up on close, but a mode=ro open CREATES `-wal`/`-shm` and cannot remove them
        # (see the wal-sidecars memory) — the preflight canaries caught the litter as an
        # undeclared tree path.  Immutable neither creates sidecars nor reads a hot WAL, and the
        # promise it requires — nothing is writing — holds here: these loaders serve analysis,
        # replay and the viewer, all of which read arms whose writer has checkpoint-closed
        # (see 183b1e6, "close the WAL on a writer").
        with _dataset.bind(path, 'sim_db', immutable=True) as ds:
            return ds.query(name, **params)
    except _identity.SchemaError:
        return None


KEYFRAME_DB_FAMILY = _identity.register(_identity.Family(
    name='keyframes_db',
    declared_shape=declared_keyframe_shape,
    # Stamped since 2026-08-15 — the first family whose stamp table arrived through the
    # --sync -> DDL edit -> --accept pipeline rather than by hand (the dogfood run of the
    # adoption automation).  Before that this family was deliberately derived-only.
    meta_table='schema_meta',
    known_ids=('e1149f95dfed',  # every sidecar before the stamp table (2026-06-23 .. 2026-08-15);
               # `bin_keyframe` itself never changed — 24 sidecars sampled across nine archived
               # runs all re-derive to this id.  Adopted by the first `--accept` run.
               ),
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
        # The stamp is the column value above; `stamp_checked` here is the VERIFY half only —
        # warn-once (never raise) because this runs inside spawned workers, hours into a sweep
        # the run-start precheck already blessed.  A store gap nags; it must not kill an arm.
        _compat.stamp_checked(con, SIM_DB_FAMILY, strict=False)
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
    """Create the bin_keyframe table + index (and the schema_meta stamp) if absent."""
    con = _open_db(path)
    try:
        con.execute(_CREATE_BIN_KEYFRAME)
        con.execute(_CREATE_BIN_KEYFRAME_IDX)
        # Stamp + verify the store, worker-safe (warn-once, never raise): keyframes are written
        # beside the sim DB, deep inside a sweep.  `stamp` creates schema_meta idempotently.
        _compat.stamp_checked(con, KEYFRAME_DB_FAMILY, strict=False)
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

def _insert_batch_stats(con: sqlite3.Connection, run_id: int, records: list) -> None:
    con.executemany(
        'INSERT INTO batch_stats '
        '(run_id,batch_id,duration,num_tasks,total_items,'
        'task_makespan,thr_task,thr_batch,'
        'avg_concurrent_pickers,picking_pct,traveling_pct,'
        'batch_start_time,batch_end_time,'
        'sigma_fd,reload_moves,reorder_placements,skus_reordered,units_ordered,'
        'queue_depth,lead_queue_depth,in_transit_qty,items_demanded,is_outlier) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [
            (run_id, r.batch_id, r.duration, r.num_tasks, r.total_items,
             r.task_makespan, r.thr_task, r.thr_batch,
             r.avg_concurrent_pickers, r.picking_pct, r.traveling_pct,
             r.batch_start_time, r.batch_end_time,
             r.sigma_fd, r.reload_moves, r.reorder_placements, r.skus_reordered, r.units_ordered,
             r.queue_depth, r.lead_queue_depth, r.in_transit_qty, r.items_demanded,
             int(r.is_outlier))
            for r in records
        ],
    )


def save_batch_stats(path: str, run_id: int, records: list[BatchStats]) -> None:
    con = _open_db(path)
    try:
        _insert_batch_stats(con, run_id, records)
        con.commit()
    finally:
        con.close()


def load_batch_stats(path: str, run_id: int) -> list[BatchStats]:
    """One BatchStats per batch, version-adaptive.

    A VETTED file is served by the `batch_frame` named query (per-vintage overrides and
    optional-fill included), so a consumer of this loader is version-free without knowing it.
    An unvetted file — the cold archive `Diagnostics/replay_run.py` deliberately reads — falls
    through to the frozen pre-registry body below, byte-for-byte the historical behavior.
    """
    recs = _query_rows('batch_frame', path, run_id=run_id)
    if recs is not None:
        return [BatchStats(**{**r, 'is_outlier': bool(r['is_outlier'])}) for r in recs]
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
        _insert_reorder_queue(con, run_id, records)
        con.commit()
    finally:
        con.close()


def _insert_reorder_queue(con: sqlite3.Connection, run_id: int, records: list) -> None:
    con.executemany(
        'INSERT INTO reorder_queue '
        '(run_id,batch_id,kind,sku,qty,remaining_lead,unit_type,storage_size) '
        'VALUES (?,?,?,?,?,?,?,?)',
        [(run_id, int(b), str(k), int(s), int(q), int(rl), ut, ss)
         for (b, k, s, q, rl, ut, ss) in records],
    )


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
        # Generator, not a list: records is ~400k rows and executemany consumes the
        # argument lazily — a list here briefly doubled the retained row storage.
        con.executemany(
            'INSERT OR REPLACE INTO bin_scores '
            '(run_id,aisle_id,bayX,bayY,travel_d,height_mult,layout_score,map_pref) '
            'VALUES (?,?,?,?,?,?,?,?)',
            ((run_id, int(a), int(bx), int(by), float(td), float(hm), float(ls),
              None if mp is None else float(mp))
             for (a, bx, by, td, hm, ls, mp) in records),
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

def _insert_task_stats(con: sqlite3.Connection, run_id: int, records: list) -> None:
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


def save_task_stats(path: str, run_id: int, records: list[TaskStats]) -> None:
    con = _open_db(path)
    try:
        _insert_task_stats(con, run_id, records)
        con.commit()
    finally:
        con.close()


def load_task_stats(path: str, run_id: int) -> list[TaskStats]:
    """One TaskStats per (batch, aisle) task — version-adaptive; see `load_batch_stats`."""
    recs = _query_rows('task_frame', path, run_id=run_id)
    if recs is not None:
        return [TaskStats(**{**r, 'is_outlier': bool(r['is_outlier'])}) for r in recs]
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


def _insert_picker_events(con: sqlite3.Connection, run_id: int, records: list) -> None:
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


_WORK_EVENT_COLS = ('batch_id', 'seq', 't_abs', 't_local', 'shift_index', 'actor_uid',
                    'actor_local', 'role', 'mode', 'event_type', 'aisle_id', 'sku', 'qty',
                    'duration', 'source')


def _insert_work_events(con: sqlite3.Connection, run_id: int, rows: list) -> None:
    con.executemany(
        'INSERT INTO work_events (run_id,' + ','.join(_WORK_EVENT_COLS) + ') '
        'VALUES (?' + ',?' * len(_WORK_EVENT_COLS) + ')',
        [(run_id, *r) for r in rows])


def save_work_events(path: str, run_id: int, rows: list) -> None:
    """Append merged-stream rows.  Each row is `_WORK_EVENT_COLS` in order."""
    if not rows:
        return
    con = _open_db(path)
    try:
        _insert_work_events(con, run_id, rows)
        con.commit()
    finally:
        con.close()


def save_picker_events(path: str, run_id: int, records: list) -> None:
    con = _open_db(path)
    try:
        _insert_picker_events(con, run_id, records)
        con.commit()
    finally:
        con.close()


def _insert_picks(con: sqlite3.Connection, run_id: int, records: list) -> None:
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


def save_picks(path: str, run_id: int, records: list) -> None:
    """Persist individual pick events extracted from the picker event stream."""
    con = _open_db(path)
    try:
        _insert_picks(con, run_id, records)
        con.commit()
    finally:
        con.close()


def load_picker_events(path: str, run_id: int, batch_id: int | None = None) -> list:
    """Load PickerEventRecord rows for *run_id*, optionally filtered to one batch.

    Returns records ordered by (batch_id, picker_id, time) for sequential replay.
    Version-adaptive; see `load_batch_stats`.
    """
    recs = _query_rows('picker_events', path, run_id=run_id, batch_id=batch_id)
    if recs is not None:
        return [PickerEventRecord(**r) for r in recs]
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


# ── BinInventory DB — READER ONLY (the table is legacy/archive-only) ──────────
#
# There is deliberately no `save_bin_inventory`: this build does not create the table and never
# writes it.  This loader exists solely so archived runs — written before `bin_placement` /
# `bin_eviction` existed, and never to be rewritten — stay readable.  On a DB from this build
# the table is absent and every call here raises `sqlite3.OperationalError: no such table`;
# callers that may see either vintage must probe first (see `Diagnostics/replay_run._has_rows`).

def load_bin_inventory(
    path     : str,
    run_id   : int,
    batch_id : int | None = None,
    aisle_id : int | None = None,
) -> list:
    """Load BinInventoryRecord rows from an ARCHIVED run, optionally filtered to batch/aisle.

    LEGACY/ARCHIVE-ONLY.  The table is no longer written by any run; a DB produced by this
    build has no `bin_inventory` at all and this raises `sqlite3.OperationalError`.
    """
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
        _insert_aisle_metrics(con, run_id, records)
        con.commit()
    finally:
        con.close()


def _insert_aisle_metrics(con: sqlite3.Connection, run_id: int, records: list) -> None:
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
    # Defaulted, so every existing construction site keeps working and an unscored path
    # says so by omission rather than by inventing a number.  See the DDL comment.
    score:      float | None = None
    score_rank: int   | None = None
    policy:     str   | None = None


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
        _insert_bin_placements(con, run_id, records)
        con.commit()
    finally:
        con.close()


def _insert_bin_placements(con: sqlite3.Connection, run_id: int, records: list) -> None:
    con.executemany(
        'INSERT OR REPLACE INTO bin_placement '
        '(run_id, batch_id, seq, aisle_id, bayX, bayY, sku, qty, cause, '
        ' score, score_rank, policy) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        [(run_id, r.batch_id, r.seq, r.aisle_id, r.bayX, r.bayY, r.sku, r.qty, r.cause,
          r.score, r.score_rank, r.policy)
         for r in records])


def save_bin_evictions(path: str, run_id: int, records: list) -> None:
    """Persist EVICT events.  Zero rows on a `norsl` arm, which is every shipped arm today."""
    if not records:
        return
    con = _open_db(path)
    try:
        _insert_bin_evictions(con, run_id, records)
        con.commit()
    finally:
        con.close()


def _insert_bin_evictions(con: sqlite3.Connection, run_id: int, records: list) -> None:
    con.executemany(
        'INSERT OR REPLACE INTO bin_eviction '
        '(run_id, batch_id, seq, aisle_id, bayX, bayY, sku, qty) VALUES (?,?,?,?,?,?,?,?)',
        [(run_id, r.batch_id, r.seq, r.aisle_id, r.bayX, r.bayY, r.sku, r.qty)
         for r in records])


def save_checkpoint_bundle(
    path           : str,
    run_id         : int,
    *,
    batch_stats    : list,
    task_stats     : list,
    picker_events  : list,
    picks          : list,
    bin_placements : list,
    bin_evictions  : list,
    aisle_metrics  : list,
    reorder_queue  : list,
    work_events    : list | None = None,
) -> None:
    """All per-checkpoint writers on ONE connection with ONE commit.

    `work_events` is the merged cross-stream timeline and is keyword-OPTIONAL, so a caller
    that predates it -- a test, a Diagnostics harness -- is unchanged and writes no rows.

    strategy_runner's checkpoint flush used to call the eight `save_*` writers back to
    back, each paying its own open + commit + close against a ~1 GB WAL DB — measured
    at 40k as ~2s of CPU inside ~8.7s of t_save, the rest drive latency multiplied by
    the per-writer connection churn (and the maker of the synchronized 18-worker save
    storm early in a run).  This bundles them: the INSERT bodies are the writers' own
    (shared `_insert_*` helpers), executed in exactly the historical call order, so
    every table receives identical rows in identical order — per-table rowids and the
    run digest are unchanged.  The three writers that early-return on empty keep that
    skip here (parity; the tables all pre-exist from init_run_db either way).
    """
    con = _open_db(path)
    try:
        _insert_batch_stats(con, run_id, batch_stats)
        _insert_task_stats(con, run_id, task_stats)
        _insert_picker_events(con, run_id, picker_events)
        _insert_picks(con, run_id, picks)
        if bin_placements:
            _insert_bin_placements(con, run_id, bin_placements)
        if bin_evictions:
            _insert_bin_evictions(con, run_id, bin_evictions)
        _insert_aisle_metrics(con, run_id, aisle_metrics)
        if work_events:
            _insert_work_events(con, run_id, work_events)
        if reorder_queue:
            _insert_reorder_queue(con, run_id, reorder_queue)
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
