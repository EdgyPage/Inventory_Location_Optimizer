import csv
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


def load_picks(path: str) -> list[PickRecord]:
    records: list[PickRecord] = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            records.append(PickRecord(
                sku          = int(row['sku']),
                quantity     = int(row['quantity']),
                timestamp    = datetime.fromisoformat(row['timestamp']),
                location     = (int(row['aisle_id']), int(row['bayX']), int(row['bayY'])),
                handling_type= row['handling_type'],
                category_type= row['category_type'],
            ))
    return records
