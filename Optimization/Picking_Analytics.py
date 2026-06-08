from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from dataclasses import dataclass as _dataclass

from Picking_Data import PickRecord
from Workload import WorkloadParams, aisle_workload
from Inventory_Management import LoadParams


@_dataclass
class AisleLoadRecord:
    """Aisle observation used for load-model parameter recovery.

    Retained here for the analytics / plotting pipeline; the corresponding DB
    tables (aisle_loads, recovered_params) have been removed from the
    simulation DB since the load model is no longer the active objective.
    """
    batch_id:     int
    aisle_id:     int
    W:          float   # base aisle workload from aisle_workload()
    lift_sum:     float   # sum_lift() for batch SKUs in this aisle
    observed_L_a: float   # pick time from simulation or formula + noise
    is_outlier:   bool = False
    run_id:       int  = 0


@dataclass
class BatchConfig:
    items: dict[int, int] = field(default_factory=dict)   # skuID -> quantity


# (sku_i, sku_j) -> lift(i, j); symmetric, only pairs meeting min_support are present
AffMatrix = dict[tuple[int, int], float]


def compute_affinity(batches: list[Batch], min_support: int = 5) -> AffMatrix:
    """Build B_ij = lift(i, j) from historical batches.

    lift(i,j) = P(i∩j) / (P(i)·P(j))

    Pairs whose co-occurrence count is below min_support are excluded;
    absent keys default to 0.0 in sum_lift.  Both (i,j) and (j,i) are stored.
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


def sum_lift(skus: list[int], affinity: AffMatrix) -> float:
    """Sum of pairwise lift over all ordered pairs (i, j), i != j, in `skus`.

    Uses ordered pairs so each unordered pair {i,j} contributes twice — once
    for (i→j) and once for (j→i) — matching the symmetric AffMatrix storage.
    """
    return sum(affinity.get((i, j), 0.0) for i in skus for j in skus if i != j)


def aisle_load(W: float, aisle_skus: list[int], params: LoadParams, affinity: AffMatrix) -> float:
    """L_a = W + λ*(W/k)^γ * SUM(LIFT(cartons in aisle))

    Parameters
    ----------
    W        : base aisle workload, computed via aisle_workload()
    aisle_skus : SKU IDs of every carton picked in this aisle for this batch
    params     : LoadParams (lambda_, k, gamma)
    affinity   : pairwise lift matrix from compute_affinity()
    """
    return W + (params.lambda_ * (W / params.k) ** params.gamma) * sum_lift(aisle_skus, affinity)


def aisle_load_from_sum(W: float, lift_sum: float, params: LoadParams) -> float:
    """L_a formula with precomputed lift_sum — avoids recomputing from SKUs.

    Useful when AisleLoadRecord already stores the lift_sum directly.
    """
    return W + (params.lambda_ * (W / params.k) ** params.gamma) * lift_sum


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
        return sum_lift(list(self.config.items.keys()), affinity)

    def true_load(self, params: LoadParams, affinity: AffMatrix, W: float) -> float:
        """L_a = W + λ*(W/k)^γ * SUM_ij(B_ij); delegates to aisle_load()."""
        return aisle_load(W, list(self.config.items.keys()), params, affinity)


def simulate_loads(
    batches: list[Batch],
    workloads: list[float],
    params: LoadParams,
    affinity: AffMatrix,
    noise_std: float = 1.0,
    seed: int | None = None,
) -> list[float]:
    """Return noisy load observations generated from the true equation.

    workloads[i] is W for batches[i], computed via aisle_workload().
    """
    rng = random.Random(seed)
    return [
        b.true_load(params, affinity, W) + rng.gauss(0.0, noise_std)
        for b, W in zip(batches, workloads)
    ]


def recover_load_params(
    batches: list[Batch],
    observed_loads: list[float],
    workloads: list[float],
    affinity: AffMatrix,
    k: float,
) -> LoadParams:
    """Recover lambda_ and gamma via log-linear OLS from Batch/workload lists.

    Rearranging L_a = W + lambda*(W/k)^gamma * SUM_B:
        log((L_a - W) / SUM_B) = log(lambda) + gamma * log(W / k)
    Samples where L_a - W <= 0 or SUM_B <= 0 are skipped.

    workloads[i] is W for batches[i], computed via aisle_workload().
    """
    rows_x: list[list[float]] = []
    rows_y: list[float] = []

    for batch, L_obs, W in zip(batches, observed_loads, workloads):
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


def recover_params_from_records(
    records: list[AisleLoadRecord],
    k: float,
) -> LoadParams:
    """Recover lambda_ and gamma via log-linear OLS from AisleLoadRecord list.

    Uses stored (W, lift_sum, observed_L_a) directly — no need for the
    original Batch objects or affinity matrix.

    Rearranging L_a = W + λ*(W/k)^γ * S:
        log((L_a - W) / S) = log(λ) + γ * log(W / k)
    Records where residual <= 0, lift_sum <= 0, or W <= 0 are skipped.
    Returns default LoadParams(k=k) if too few valid points remain.
    """
    rows_x: list[list[float]] = []
    rows_y: list[float] = []

    for r in records:
        residual = r.observed_L_a - r.W
        if residual <= 0 or r.lift_sum <= 0 or r.W <= 0:
            continue
        rows_x.append([1.0, float(np.log(r.W / k))])
        rows_y.append(float(np.log(residual / r.lift_sum)))

    if len(rows_x) < 2:
        return LoadParams(lambda_=1.0, k=k, gamma=1.5)

    X = np.array(rows_x, dtype=float)
    y = np.array(rows_y, dtype=float)
    log_lambda, gamma = np.linalg.lstsq(X, y, rcond=None)[0]
    return LoadParams(lambda_=float(np.exp(log_lambda)), k=k, gamma=float(gamma))


def flag_outliers(
    records: list[AisleLoadRecord],
    iqr_factor: float = 1.5,
) -> list[AisleLoadRecord]:
    """Return a new list with is_outlier set using Tukey IQR fences.

    Fences are applied to the load residual (observed_L_a - W) so that
    observations whose excess load is implausibly large or negative are flagged
    without penalising high-W aisles whose absolute L_a is legitimately large.
    """
    residuals = np.array([r.observed_L_a - r.W for r in records])
    q1, q3 = float(np.percentile(residuals, 25)), float(np.percentile(residuals, 75))
    iqr = q3 - q1
    lo, hi = q1 - iqr_factor * iqr, q3 + iqr_factor * iqr

    return [
        AisleLoadRecord(
            run_id       = r.run_id,
            batch_id     = r.batch_id,
            aisle_id     = r.aisle_id,
            W          = r.W,
            lift_sum     = r.lift_sum,
            observed_L_a = r.observed_L_a,
            is_outlier   = bool(res < lo or res > hi),
        )
        for r, res in zip(records, residuals)
    ]


def plot_loads(
    records: list[AisleLoadRecord],
    raw_params: LoadParams | None = None,
    clean_params: LoadParams | None = None,
    title: str = "Aisle Load Recovery",
    save_path: str | None = None,
) -> None:
    """Two-panel figure showing raw data and the log-linear regression fit.

    Panel 1 — W vs observed_L_a scatter:
      • Blue dots  = clean observations
      • Red  ×     = flagged outliers
      • Grey line  = identity (L_a = W, zero affinity baseline)
      • Curves for raw-fit and clean-fit models at the median lift_sum

    Panel 2 — Linearised form used for OLS:
      x = log(W / k),  y = log((L_a - W) / lift_sum)
      Fitted lines show recovered slope (gamma) and intercept (log lambda).
      Only points with positive residual and positive lift_sum are plotted.
    """
    import matplotlib.pyplot as plt

    clean   = [r for r in records if not r.is_outlier]
    outliers = [r for r in records if r.is_outlier]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle(title, fontsize=13)

    # ── Panel 1: W vs L_a ──────────────────────────────────────────────────
    if clean:
        ax1.scatter(
            [r.W for r in clean], [r.observed_L_a for r in clean],
            s=16, alpha=0.55, color='steelblue', label=f'Clean (n={len(clean)})',
        )
    if outliers:
        ax1.scatter(
            [r.W for r in outliers], [r.observed_L_a for r in outliers],
            s=20, alpha=0.7, color='tomato', marker='x',
            label=f'Outlier (n={len(outliers)})',
        )

    W_vals = [r.W for r in records]
    W_lo, W_hi = min(W_vals) * 0.95, max(W_vals) * 1.05
    W_curve = np.linspace(W_lo, W_hi, 300)
    ax1.plot(W_curve, W_curve, color='#999', linewidth=1, linestyle='--',
             label='L_a = W (no lift)')

    med_lift = float(np.median([r.lift_sum for r in records if r.lift_sum > 0] or [1.0]))
    for params, color, lbl in [
        (raw_params,   'orange', 'Raw fit'),
        (clean_params, 'green',  'Clean fit'),
    ]:
        if params is not None:
            L_curve = [aisle_load_from_sum(w, med_lift, params) for w in W_curve]
            ax1.plot(W_curve, L_curve, color=color, linewidth=1.8, label=lbl)

    ax1.set_xlabel('W  (base workload)')
    ax1.set_ylabel('Observed L_a')
    ax1.legend(fontsize=8)
    ax1.set_title('Workload vs Observed Load')

    # ── Panel 2: log-linear form ──────────────────────────────────────────────
    def _log_points(recs: list[AisleLoadRecord], k: float) -> tuple[list, list]:
        xs, ys = [], []
        for r in recs:
            res = r.observed_L_a - r.W
            if res > 0 and r.lift_sum > 0 and r.W > 0:
                xs.append(np.log(r.W / k))
                ys.append(np.log(res / r.lift_sum))
        return xs, ys

    k_ref = (raw_params or clean_params or LoadParams()).k
    if clean:
        xs, ys = _log_points(clean, k_ref)
        ax2.scatter(xs, ys, s=16, alpha=0.55, color='steelblue')
    if outliers:
        xs_o, ys_o = _log_points(outliers, k_ref)
        ax2.scatter(xs_o, ys_o, s=20, alpha=0.7, color='tomato', marker='x')

    x_range = np.array([r.W / k_ref for r in records if r.W > 0])
    if len(x_range) > 0:
        lx = np.linspace(float(np.log(x_range.min())), float(np.log(x_range.max())), 200)
        for params, color, lbl in [
            (raw_params,   'orange', f'Raw  λ={raw_params.lambda_:.2f} γ={raw_params.gamma:.2f}' if raw_params else ''),
            (clean_params, 'green',  f'Clean λ={clean_params.lambda_:.2f} γ={clean_params.gamma:.2f}' if clean_params else ''),
        ]:
            if params is not None:
                ly = np.log(params.lambda_) + params.gamma * lx
                ax2.plot(lx, ly, color=color, linewidth=1.8, label=lbl)

    ax2.set_xlabel('log(W / k)')
    ax2.set_ylabel('log((L_a − W) / lift_sum)')
    ax2.legend(fontsize=8)
    ax2.set_title('Log-linear fit  (OLS regression space)')

    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.show()


# ── SKU-level analytics (unchanged from original) ────────────────────────────

@dataclass
class SkuMetrics:
    sku: int
    pick_count: int
    total_quantity: int
    velocity: float
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
    bin_: Any,
    x_speed: float = 1.0,
    y_speed: float = 0.5,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> float:
    """Estimated travel time from origin to a bin using physical coordinates.

    Uses b.x_phys and b.y_phys (physical centre position of the bin) weighted
    by x_speed / y_speed (time per physical unit), matching the simulation's
    cost model.  The aisle_id dimension is excluded — cross-aisle routing is
    handled by the Inventory_Manager's candidate pre-filtering.
    """
    return x_speed * abs(bin_.x_phys - origin_x) + y_speed * abs(bin_.y_phys - origin_y)


_SINGLETON_SIZES: frozenset[str] = frozenset({'small', 'medium'})
_PALLET_SIZES: frozenset[str] = frozenset({'large', 'extra_large'})


def build_velocity_assignment_fn(
    records : list[PickRecord],
    wp      : WorkloadParams | None = None,
    origin_x: int = 1,
    origin_y: int = 1,
) -> Callable[[Any, list[Any]], Any | None]:
    """Returns an AssignmentFn placing high-velocity SKUs closest to origin.

    Bins are ranked by weighted travel cost (b.bayX * x_time + b.bayY * y_time)
    so the scoring reflects how long a picker physically takes to reach each bin,
    consistent with the simulation's move-time model.

    wp          : WorkloadParams supplying x_speed / y_speed.
                  Defaults to WorkloadParams() (x=1.0, y=0.5) if not provided.
    origin_x/y  : physical starting position within an aisle (default: 0.0).

    Compatible with Inventory_Manager's AssignmentFn signature:
        (StorageUnit, list[Aisle.Bin]) -> Aisle.Bin | None
    """
    if wp is None:
        wp = WorkloadParams()
    x_speed = wp.x_speed
    y_speed = wp.y_speed

    scores: dict[int, float] = velocity_scores(compute_sku_metrics(records))

    def _fn(unit: Any, available_bins: list[Any]) -> Any | None:
        candidates: list[Any] = [
            b for b in available_bins
            if b.handling_type == unit.carton.storage_type[0]
            and b.storage_type == unit.carton.storage_type[1]
            and b.storage is None
        ]
        if not candidates:
            return None

        singleton_bins: list[Any] = [b for b in candidates if b.storage_size in _SINGLETON_SIZES]
        pallet_bins: list[Any]    = [b for b in candidates if b.storage_size in _PALLET_SIZES]
        pool: list[Any] = singleton_bins if singleton_bins else pallet_bins
        if not pool:
            pool = candidates

        v_score: float = scores.get(unit.carton.sku, 0.5)
        sorted_pool: list[Any] = sorted(
            pool, key=lambda b: travel_cost(b, x_speed, y_speed, origin_x, origin_y)
        )
        idx: int = round((1.0 - v_score) * (len(sorted_pool) - 1))
        return sorted_pool[idx]

    return _fn
