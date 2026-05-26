import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PickRecord:
    sku: int
    quantity: int
    timestamp: datetime
    location: tuple[int, int, int]   # (aisle_id, bayX, bayY)
    handling_type: str
    category_type: str


_COLUMNS = ('sku', 'quantity', 'timestamp', 'aisle_id', 'bayX', 'bayY', 'handling_type', 'category_type')

_CREATE_TABLE = f"""
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


def _to_row(r: PickRecord) -> dict:
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


def _from_row(row: dict) -> PickRecord:
    return PickRecord(
        sku           = int(row['sku']),
        quantity      = int(row['quantity']),
        timestamp     = datetime.fromisoformat(row['timestamp']),
        location      = (int(row['aisle_id']), int(row['bayX']), int(row['bayY'])),
        handling_type = row['handling_type'],
        category_type = row['category_type'],
    )


# ── CSV ──────────────────────────────────────────────────────────────────────

def load_picks_csv(path: str) -> list[PickRecord]:
    with open(path, newline='') as f:
        return [_from_row(row) for row in csv.DictReader(f)]


def save_picks_csv(records: list[PickRecord], path: str) -> None:
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(_to_row(r) for r in records)


# ── SQLite ───────────────────────────────────────────────────────────────────

def load_picks_db(path: str) -> list[PickRecord]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute('SELECT * FROM picks').fetchall()
        return [_from_row(dict(row)) for row in rows]
    finally:
        con.close()


def save_picks_db(records: list[PickRecord], path: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(_CREATE_TABLE)
        con.executemany(
            'INSERT INTO picks VALUES (:sku,:quantity,:timestamp,:aisle_id,:bayX,:bayY,:handling_type,:category_type)',
            (_to_row(r) for r in records),
        )
        con.commit()
    finally:
        con.close()
