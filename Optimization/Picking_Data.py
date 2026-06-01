import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class PickRecord:
    sku: int
    quantity: int
    timestamp: datetime
    location: tuple[int, int, int]   # (aisle_id, bayX, bayY)
    handling_type: str
    category_type: str


@dataclass
class AisleLoadRecord:
    batch_id: int
    aisle_id: int
    W_a: float          # base aisle workload from aisle_workload()
    lift_sum: float     # sum_lift() for batch SKUs in this aisle
    observed_L_a: float # pick time from simulation or formula + noise
    is_outlier: bool = False
    run_id: int = 0     # assigned by create_run() before DB write


@dataclass
class RecoveredParams:
    run_id: int
    lambda_: float
    k: float
    gamma: float
    n_samples: int   # total observations used in fit attempt
    n_clean: int     # observations after outlier removal
    rmse_raw: float  # RMSE across all samples with raw-fit params
    rmse_clean: float
    timestamp: str


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
    is_outlier: bool = False


@dataclass
class TaskStats:
    run_id: int
    batch_id: int
    aisle_id: int
    picker_id: int
    duration: float       # task_end.time − task_start.time
    W_a: float            # analytical aisle workload baseline
    lift_sum: float       # sum_lift for this aisle's SKUs
    num_bins_visited: int # bins with at least one pick
    total_items: int      # items picked in this aisle
    is_outlier: bool = False


# ── PickRecord columns ────────────────────────────────────────────────────────

_PICK_COLS = ('sku', 'quantity', 'timestamp', 'aisle_id', 'bayX', 'bayY',
              'handling_type', 'category_type')

_CREATE_PICKS = """
    CREATE TABLE IF NOT EXISTS picks (
        sku           INTEGER NOT NULL,
        quantity      INTEGER NOT NULL,
        timestamp     TEXT    NOT NULL,
        aisle_id      INTEGER NOT NULL,
        bayX          INTEGER NOT NULL,
        bayY          INTEGER NOT NULL,
        handling_type TEXT    NOT NULL,
        category_type TEXT    NOT NULL
    )
"""

# ── Run DB schema ─────────────────────────────────────────────────────────────

_CREATE_RUNS = """
    CREATE TABLE IF NOT EXISTS simulation_runs (
        run_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type TEXT    NOT NULL,
        created  TEXT    NOT NULL
    )
"""

_CREATE_AISLE_LOADS = """
    CREATE TABLE IF NOT EXISTS aisle_loads (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id     INTEGER NOT NULL,
        aisle_id     INTEGER NOT NULL,
        W_a          REAL    NOT NULL,
        lift_sum     REAL    NOT NULL,
        observed_L_a REAL    NOT NULL,
        is_outlier   INTEGER NOT NULL DEFAULT 0
    )
"""

_CREATE_RECOVERED = """
    CREATE TABLE IF NOT EXISTS recovered_params (
        run_id      INTEGER PRIMARY KEY REFERENCES simulation_runs(run_id),
        lambda_     REAL    NOT NULL,
        k           REAL    NOT NULL,
        gamma       REAL    NOT NULL,
        n_samples   INTEGER NOT NULL,
        n_clean     INTEGER NOT NULL,
        rmse_raw    REAL    NOT NULL,
        rmse_clean  REAL    NOT NULL,
        timestamp   TEXT    NOT NULL
    )
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
        duration         REAL    NOT NULL,
        W_a              REAL    NOT NULL,
        lift_sum         REAL    NOT NULL,
        num_bins_visited INTEGER NOT NULL,
        total_items      INTEGER NOT NULL,
        is_outlier       INTEGER NOT NULL DEFAULT 0
    )
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
    """Create all tables if they don't already exist, and enable WAL mode."""
    con = _open_db(path)
    try:
        con.execute(_CREATE_PICKS)
        con.execute(_CREATE_RUNS)
        con.execute(_CREATE_AISLE_LOADS)
        con.execute(_CREATE_RECOVERED)
        con.execute(_CREATE_BATCH_STATS)
        con.execute(_CREATE_TASK_STATS)
        con.commit()
    finally:
        con.close()


def create_run(path: str, run_type: str) -> int:
    """Insert a new simulation run row; return the assigned run_id."""
    con = _open_db(path)
    try:
        cur = con.execute(
            'INSERT INTO simulation_runs (run_type, created) VALUES (?, ?)',
            (run_type, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        con.close()


def save_aisle_loads(path: str, run_id: int, records: list[AisleLoadRecord]) -> None:
    """Insert aisle load records, overwriting run_id on each record."""
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO aisle_loads '
            '(run_id,batch_id,aisle_id,W_a,lift_sum,observed_L_a,is_outlier) '
            'VALUES (?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id,
                 r.W_a, r.lift_sum, r.observed_L_a, int(r.is_outlier))
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()


def load_aisle_loads(path: str, run_id: int) -> list[AisleLoadRecord]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            'SELECT * FROM aisle_loads WHERE run_id = ?', (run_id,)
        ).fetchall()
        return [
            AisleLoadRecord(
                run_id       = row['run_id'],
                batch_id     = row['batch_id'],
                aisle_id     = row['aisle_id'],
                W_a          = row['W_a'],
                lift_sum     = row['lift_sum'],
                observed_L_a = row['observed_L_a'],
                is_outlier   = bool(row['is_outlier']),
            )
            for row in rows
        ]
    finally:
        con.close()


def save_recovered_params(path: str, rp: RecoveredParams) -> None:
    con = _open_db(path)
    try:
        con.execute(
            'INSERT OR REPLACE INTO recovered_params '
            '(run_id,lambda_,k,gamma,n_samples,n_clean,rmse_raw,rmse_clean,timestamp) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (rp.run_id, rp.lambda_, rp.k, rp.gamma,
             rp.n_samples, rp.n_clean, rp.rmse_raw, rp.rmse_clean, rp.timestamp),
        )
        con.commit()
    finally:
        con.close()


def load_recovered_params(path: str, run_id: int) -> RecoveredParams | None:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            'SELECT * FROM recovered_params WHERE run_id = ?', (run_id,)
        ).fetchone()
        if row is None:
            return None
        return RecoveredParams(
            run_id     = row['run_id'],
            lambda_    = row['lambda_'],
            k          = row['k'],
            gamma      = row['gamma'],
            n_samples  = row['n_samples'],
            n_clean    = row['n_clean'],
            rmse_raw   = row['rmse_raw'],
            rmse_clean = row['rmse_clean'],
            timestamp  = row['timestamp'],
        )
    finally:
        con.close()


def export_params_json(rp: RecoveredParams, path: str) -> None:
    """Write recovered LoadParams to JSON for import in any folder.

    Callers load it with:
        import json
        from Picking_Analytics import LoadParams
        p = LoadParams(**json.load(open('recovered_params.json')))
    """
    with open(path, 'w') as f:
        json.dump(
            {
                'lambda_':   rp.lambda_,
                'k':         rp.k,
                'gamma':     rp.gamma,
                'n_samples': rp.n_samples,
                'n_clean':   rp.n_clean,
                'rmse_raw':  rp.rmse_raw,
                'rmse_clean': rp.rmse_clean,
                'recovered_at': rp.timestamp,
            },
            f,
            indent=2,
        )


# ── BatchStats DB ─────────────────────────────────────────────────────────────

def save_batch_stats(path: str, run_id: int, records: list[BatchStats]) -> None:
    con = _open_db(path)
    try:
        con.executemany(
            'INSERT INTO batch_stats '
            '(run_id,batch_id,duration,num_tasks,total_items,'
            'avg_concurrent_pickers,picking_pct,traveling_pct,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.duration, r.num_tasks, r.total_items,
                 r.avg_concurrent_pickers, r.picking_pct, r.traveling_pct,
                 int(r.is_outlier))
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
            '(run_id,batch_id,aisle_id,picker_id,duration,W_a,lift_sum,'
            'num_bins_visited,total_items,is_outlier) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            [
                (run_id, r.batch_id, r.aisle_id, r.picker_id, r.duration,
                 r.W_a, r.lift_sum, r.num_bins_visited, r.total_items,
                 int(r.is_outlier))
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
