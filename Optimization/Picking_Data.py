import csv
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


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
    run_id:     int
    batch_id:   int
    aisle_id:   int
    n_skus:     int    # unique SKUs placed in this aisle
    n_bins:     int    # occupied bin count in this aisle
    demand_sum: float  # Σ f_i * q_i — trip-cost secondary score (demand mass)
    lift_sum:   float  # affinity pairwise lift sum — co-location quality


@dataclass
class BatchStats:
    run_id: int
    batch_id: int
    duration: float               # max picker done-time — wall-clock for entire batch
    num_tasks: int                # unique aisles visited
    total_items: int              # items picked across all pickers
    avg_concurrent_pickers: float # time-weighted mean pickers in "picking" state
    picking_pct: float            # fraction of aggregate picker-time spent picking
    traveling_pct: float          # fraction of aggregate picker-time spent traveling
    batch_start_time: float = 0.0 # min picker-event time (batch-relative clock)
    batch_end_time:   float = 0.0 # max picker-event time (≈ duration)
    sigma_fw: float = 0.0         # realised demand-weighted within-aisle travel (Sigma f*W)
    reload_moves: int = 0         # re-slot bin moves this batch (layout churn)
    reorder_placements: int = 0   # reorder unit placements this batch (restock churn)
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
    W_a: float              # analytical aisle workload baseline
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
        keyframe_interval INTEGER
    )
"""

# Run-param columns set by create_run(params=...); order matches the INSERT.
_RUN_PARAM_COLS = ('num_pickers', 'x_speed', 'y_speed', 'pick_intercept',
                   'pick_weight_coef', 'pick_volume_coef', 'cart_swap_coef',
                   'k_pickers', 'n_batches', 'seed_world', 'keyframe_interval')

_CREATE_AISLE_METRICS = """
    CREATE TABLE IF NOT EXISTS aisle_metrics (
        run_id     INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id   INTEGER NOT NULL,
        aisle_id   INTEGER NOT NULL,
        n_skus     INTEGER NOT NULL DEFAULT 0,
        n_bins     INTEGER NOT NULL DEFAULT 0,
        demand_sum REAL    NOT NULL DEFAULT 0.0,
        lift_sum   REAL    NOT NULL DEFAULT 0.0,
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
        avg_concurrent_pickers REAL    NOT NULL,
        picking_pct            REAL    NOT NULL,
        traveling_pct          REAL    NOT NULL,
        batch_start_time       REAL    NOT NULL DEFAULT 0,
        batch_end_time         REAL    NOT NULL DEFAULT 0,
        sigma_fw               REAL    NOT NULL DEFAULT 0,
        reload_moves           INTEGER NOT NULL DEFAULT 0,
        reorder_placements     INTEGER NOT NULL DEFAULT 0,
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
        W_a              REAL    NOT NULL,
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
        total_items    INTEGER NOT NULL DEFAULT 0
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


# ── PickRecord helpers ────────────────────────────────────────────────────────

def _pick_to_row(r: PickRecord) -> dict:
    return {
        'sku':           r.sku,
        'quantity':      r.quantity,
        'timestamp':     r.timestamp.isoformat(),
        'aisle_id':      r.location[0],
        'bayX':          r.location[1],
        'bayY':          r.location[2],
        'handling_type': r.handling_type,
        'category_type': r.category_type,
    }


def _pick_from_row(row: dict) -> PickRecord:
    return PickRecord(
        sku           = int(row['sku']),
        quantity      = int(row['quantity']),
        timestamp     = datetime.fromisoformat(row['timestamp']),
        location      = (int(row['aisle_id']), int(row['bayX']), int(row['bayY'])),
        handling_type = row['handling_type'],
        category_type = row['category_type'],
    )


# ── PickRecord CSV / SQLite ───────────────────────────────────────────────────

def load_picks_csv(path: str) -> list[PickRecord]:
    with open(path, newline='') as f:
        return [_pick_from_row(row) for row in csv.DictReader(f)]


def save_picks_csv(records: list[PickRecord], path: str) -> None:
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=_PICK_COLS)
        writer.writeheader()
        writer.writerows(_pick_to_row(r) for r in records)


def load_picks_db(path: str) -> list[PickRecord]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute('SELECT * FROM picks').fetchall()
        return [_pick_from_row(dict(row)) for row in rows]
    finally:
        con.close()


def save_picks_db(records: list[PickRecord], path: str) -> None:
    con = _open_db(path)
    try:
        con.execute(_CREATE_PICKS)
        con.executemany(
            'INSERT INTO picks VALUES '
            '(:sku,:quantity,:timestamp,:aisle_id,:bayX,:bayY,:handling_type,:category_type)',
            (_pick_to_row(r) for r in records),
        )
        con.commit()
    finally:
        con.close()


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


def init_run_db(path: str) -> None:
    """Create all tables and indexes if they don't already exist, and enable WAL mode."""
    con = _open_db(path)
    try:
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
        con.commit()
    finally:
        con.close()


def create_run(path: str, run_type: str, params: dict | None = None) -> int:
    """Insert a new simulation run row; return the assigned run_id.

    params (optional): run configuration recorded for reconstruction —
    keys from _RUN_PARAM_COLS (num_pickers, x_speed, …, keyframe_interval).
    Missing keys are stored NULL.
    """
    params = params or {}
    cols = ('run_type', 'created') + _RUN_PARAM_COLS
    vals = ([run_type, datetime.now(timezone.utc).isoformat()]
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
            'avg_concurrent_pickers,picking_pct,traveling_pct,'
            'batch_start_time,batch_end_time,'
            'sigma_fw,reload_moves,reorder_placements,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.duration, r.num_tasks, r.total_items,
                 r.avg_concurrent_pickers, r.picking_pct, r.traveling_pct,
                 r.batch_start_time, r.batch_end_time,
                 r.sigma_fw, r.reload_moves, r.reorder_placements, int(r.is_outlier))
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
                batch_start_time       = (row['batch_start_time']
                                          if 'batch_start_time' in row.keys() else 0.0),
                batch_end_time         = (row['batch_end_time']
                                          if 'batch_end_time' in row.keys() else 0.0),
                sigma_fw               = (row['sigma_fw']
                                          if 'sigma_fw' in row.keys() else 0.0),
                reload_moves           = (row['reload_moves']
                                          if 'reload_moves' in row.keys() else 0),
                reorder_placements     = (row['reorder_placements']
                                          if 'reorder_placements' in row.keys() else 0),
                is_outlier             = bool(row['is_outlier']),
            )
            for row in rows
        ]
    finally:
        con.close()


# ── TaskStats DB ──────────────────────────────────────────────────────────────

def save_task_stats(path: str, run_id: int, records: list[TaskStats]) -> None:
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO task_stats '
            '(run_id,batch_id,aisle_id,picker_id,task_start_time,task_end_time,'
            'duration,W_a,lift_sum,num_bins_visited,total_items,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id, r.picker_id,
                 r.task_start_time, r.task_end_time, r.duration,
                 r.W_a, r.lift_sum, r.num_bins_visited,
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
                W_a              = row['W_a'],
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
            'sku,quantity,bins_completed,total_bins,items_picked,total_items) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.picker_id, r.time, r.event_type,
                 r.aisle_id, r.bayX, r.bayY, r.sku, r.quantity,
                 r.bins_completed, r.total_bins, r.items_picked, r.total_items)
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
            '(run_id,batch_id,aisle_id,n_skus,n_bins,demand_sum,lift_sum) '
            'VALUES (?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id,
                 r.n_skus, r.n_bins, r.demand_sum, r.lift_sum)
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
                run_id     = row['run_id'],
                batch_id   = row['batch_id'],
                aisle_id   = row['aisle_id'],
                n_skus     = row['n_skus'],
                n_bins     = row['n_bins'],
                demand_sum = row['demand_sum'],
                lift_sum   = row['lift_sum'],
            )
            for row in rows
        ]
    finally:
        con.close()
