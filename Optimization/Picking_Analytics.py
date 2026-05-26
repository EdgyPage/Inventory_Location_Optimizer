from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from Picking_Data import PickRecord


@dataclass
class BatchConfig:
    items: dict[int, int] = field(default_factory=dict)   # skuID -> quantity


@dataclass
class LoadParams:
    lambda_: float = 1.0   # startup-cost multiplier
    k: float       = 1.0   # number of pickers (operational, usually known)
    gamma: float   = 1.5   # congestion exponent


# (sku_i, sku_j) -> lift(i, j); symmetric, only pairs meeting min_support are present
AffMatrix = dict[tuple[int, int], float]


def compute_affinity(batches: list[Batch], min_support: int = 5) -> AffMatrix:
    """Build B_ij = lift(i, j) from historical batches.

    lift(i,j) = P(i∩j) / (P(i)·P(j))

    Pairs whose co-occurrence count is below min_support are excluded;
    absent keys default to 0.0 in sum_affinity.  Both (i,j) and (j,i)
    are stored since lift is symmetric.
    """
    n = len(batches)
    if n == 0:
        return {}

    sku_counts: dict[int, int] = defaultdict(int)
    pair_counts: dict[tuple[int, int], int] = defaultdict(int)

    for batch in batches:
        skus = list(batch.config.items.keys())
        for sku in skus:
            sku_counts[sku] += 1
        for a in range(len(skus)):
            for b in range(a + 1, len(skus)):
                key = (min(skus[a], skus[b]), max(skus[a], skus[b]))
                pair_counts[key] += 1

    affinity: AffMatrix = {}
    for (i, j), count_ij in pair_counts.items():
        if count_ij < min_support:
            continue
        lift_val = (count_ij / n) / ((sku_counts[i] / n) * (sku_counts[j] / n))
        affinity[(i, j)] = lift_val
        affinity[(j, i)] = lift_val

    return affinity


class Batch:
    def __init__(self, config: BatchConfig) -> None:
        self.config = config

    @property
    def total_quantity(self) -> int:
        return sum(self.config.items.values())

    @property
    def num_skus(self) -> int:
        return len(self.config.items)

    def sum_affinity(self, affinity: AffMatrix) -> float:
        """SUM_ij B_ij for all ordered pairs i != j among SKUs in this batch."""
        skus = list(self.config.items.keys())
        return sum(affinity.get((i, j), 0.0) for i in skus for j in skus if i != j)

    def true_load(self, params: LoadParams, affinity: AffMatrix) -> float:
        """L_a = W_a + (lambda * (W_a / k) ^ gamma) * SUM_ij(B_ij)"""
        W = float(self.total_quantity)
        return W + (params.lambda_ * (W / params.k) ** params.gamma) * self.sum_affinity(affinity)


def simulate_loads(
    batches: list[Batch],
    params: LoadParams,
    affinity: AffMatrix,
    noise_std: float = 1.0,
    seed: int | None = None,
) -> list[float]:
    """Return noisy load observations generated from the true equation."""
    rng = random.Random(seed)
    return [b.true_load(params, affinity) + rng.gauss(0.0, noise_std) for b in batches]


def recover_load_params(
    batches: list[Batch],
    observed_loads: list[float],
    affinity: AffMatrix,
    k: float,
) -> LoadParams:
    """Recover lambda_ and gamma via log-linear OLS.

    Rearranging L_a = W_a + lambda*(W_a/k)^gamma * SUM_B:
        log((L_a - W_a) / SUM_B) = log(lambda) + gamma * log(W_a / k)
    which is linear in [log(lambda), gamma].  Samples where L_a - W_a <= 0
    or SUM_B <= 0 are skipped (noise drove them out of the valid domain).
    """
    rows_x: list[list[float]] = []
    rows_y: list[float] = []

    for batch, L_obs in zip(batches, observed_loads):
        W = float(batch.total_quantity)
        s = batch.sum_affinity(affinity)
        residual = L_obs - W
        if residual <= 0 or s <= 0 or W <= 0:
            continue
        rows_x.append([1.0, float(np.log(W / k))])
        rows_y.append(float(np.log(residual / s)))

    X = np.array(rows_x, dtype=float)
    y = np.array(rows_y, dtype=float)
    log_lambda, gamma = np.linalg.lstsq(X, y, rcond=None)[0]
    return LoadParams(lambda_=float(np.exp(log_lambda)), k=k, gamma=float(gamma))


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
            if b.storage_handling_type == unit.carton.storage_type and b.storage is None
        ]
        if not candidates:
            return None
        v_score: float = scores.get(unit.carton.sku, 0.5)
        sorted_bins: list[Any] = sorted(candidates, key=lambda b: travel_cost(b.location, origin))
        idx: int = round((1.0 - v_score) * (len(sorted_bins) - 1))
        return sorted_bins[idx]

    return _fn
