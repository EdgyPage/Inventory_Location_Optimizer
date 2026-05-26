from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from Picking_Data import PickRecord


@dataclass
class SkuMetrics:
    sku: int
    pick_count: int
    total_quantity: int
    velocity: float        # picks per day
    avg_quantity: float
    peak_location: tuple[int, int, int]


def compute_sku_metrics(records: list[PickRecord]) -> dict[int, SkuMetrics]:
    pick_counts: dict[int, int] = defaultdict(int)
    quantities: dict[int, int] = defaultdict(int)
    location_hits: dict[int, dict[tuple[int, int, int], int]] = defaultdict(lambda: defaultdict(int))
    timestamps: dict[int, list] = defaultdict(list)

    for r in records:
        pick_counts[r.sku] += 1
        quantities[r.sku] += r.quantity
        location_hits[r.sku][r.location] += 1
        timestamps[r.sku].append(r.timestamp)

    metrics: dict[int, SkuMetrics] = {}
    for sku in pick_counts:
        ts = timestamps[sku]
        span_days: float = max((max(ts) - min(ts)).total_seconds() / 86_400, 1.0)
        count: int = pick_counts[sku]
        peak_loc: tuple[int, int, int] = max(location_hits[sku], key=lambda loc: location_hits[sku][loc])
        metrics[sku] = SkuMetrics(
            sku=sku,
            pick_count=count,
            total_quantity=quantities[sku],
            velocity=count / span_days,
            avg_quantity=quantities[sku] / count,
            peak_location=peak_loc,
        )
    return metrics


def velocity_scores(metrics: dict[int, SkuMetrics]) -> dict[int, float]:
    """Normalize pick velocity to [0, 1] across all SKUs."""
    if not metrics:
        return {}
    max_v: float = max(m.velocity for m in metrics.values())
    if max_v == 0.0:
        return {sku: 0.0 for sku in metrics}
    return {sku: m.velocity / max_v for sku, m in metrics.items()}


def travel_cost(
    location: tuple[int, int, int],
    origin: tuple[int, int, int] = (1, 1, 1),
) -> float:
    """Manhattan distance from origin to bin location (aisle_id, bayX, bayY)."""
    return float(sum(abs(a - b) for a, b in zip(location, origin)))


def build_velocity_assignment_fn(
    records: list[PickRecord],
    origin: tuple[int, int, int] = (1, 1, 1),
) -> Callable[[Any, list[Any]], Any | None]:
    """
    Returns an AssignmentFn that places high-velocity SKUs closest to origin.

    SKUs absent from pick history receive a default mid-velocity score of 0.5.
    Compatible with Inventory_Manager's AssignmentFn signature:
        (StorageUnit, list[Aisle.Bin]) -> Aisle.Bin | None
    """
    scores: dict[int, float] = velocity_scores(compute_sku_metrics(records))

    def _fn(unit: Any, available_bins: list[Any]) -> Any | None:
        candidates: list[Any] = [
            b for b in available_bins
            if b.storage_type == unit.carton.storage_type[0] and b.storage is None
        ]
        if not candidates:
            return None
        v_score: float = scores.get(unit.carton.sku, 0.5)
        sorted_bins: list[Any] = sorted(candidates, key=lambda b: travel_cost(b.location, origin))
        idx: int = round((1.0 - v_score) * (len(sorted_bins) - 1))
        return sorted_bins[idx]

    return _fn
