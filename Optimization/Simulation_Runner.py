"""Simulation_Runner.py — end-to-end pipeline for load-parameter recovery.

Workflow
--------
1. _build_world()        — construct 24-aisle warehouse + 120-SKU inventory,
                           stock bins, compute affinity (fixed for the run).
2. generate_formula_records()
                         — for each synthetic batch: derive tasks, compute W_a
                           and lift_sum analytically, evaluate the true L_a
                           formula, then add Gaussian noise and random outliers.
3. generate_simulation_records()
                         — same setup, but observed L_a comes from an actual
                           PickSimulation run rather than the formula.
4. recover_and_export()  — flag outliers (IQR), fit LoadParams on clean data,
                           store everything in SQLite, export JSON.
5. run_formula_pipeline() / run_simulation_pipeline()
                         — convenience wrappers for the full flow.

Usage (CLI)
-----------
    python Simulation_Runner.py              # formula pipeline, defaults
    python Simulation_Runner.py --sim        # simulation pipeline
    python Simulation_Runner.py --batches 500 --noise 3.0 --no-plot
"""
from __future__ import annotations

import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Warehouse'))

from Aisle_Storage import Aisle
from Carton import Carton
from Inventory_Builder import Inventory, Inventory_Builder, InventoryConfig
from Inventory_Management import Inventory_Manager
from Pick import PickConfig, PickSimulation
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch as WBBatch, BatchConfig as WBBatchConfig, Task

from Picking_Analytics import (
    AffMatrix,
    LoadParams,
    aisle_load_from_sum,
    flag_outliers,
    plot_loads,
    recover_params_from_records,
    sum_lift,
)
from Picking_Data import (
    AisleLoadRecord,
    RecoveredParams,
    create_run,
    export_params_json,
    init_run_db,
    save_aisle_loads,
    save_recovered_params,
)
from Workload import WorkloadParams, aisle_workload


# ── configuration ─────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    """Parameters that control a single end-to-end pipeline run."""
    n_batches: int          = 300    # synthetic batches to generate
    inventory_qty: int      = 10     # units per SKU stocked in the warehouse
    k_pickers: int          = 3      # operational picker count (fixed, known)
    noise_std: float        = 2.0    # Gaussian noise σ added to each observation
    outlier_fraction: float = 0.05   # fraction of observations replaced by outliers
    outlier_scale: float    = 6.0    # outlier L_a ≈ true_L_a × U(0.5,1.5) × scale
    true_lambda: float      = 1.0    # ground-truth λ  (formula pipeline only)
    true_gamma: float       = 1.5    # ground-truth γ  (formula pipeline only)
    seed: int               = 42


# ── warehouse / inventory builder ─────────────────────────────────────────────

_WAREHOUSE_CONFIG = WarehouseConfig(
    total_aisles=24,
    aisle_splits=[1 / 24] * 24,
    aisle_configs=[
        AisleConfig('conveyable',     'food',       'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'food',       'singleton', 5, 6, ['medium']),
        AisleConfig('conveyable',     'clothing',   'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'clothing',   'singleton', 5, 6, ['medium']),
        AisleConfig('conveyable',     'electronic', 'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'electronic', 'singleton', 5, 6, ['medium']),
        AisleConfig('conveyable',     'furniture',  'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'furniture',  'singleton', 5, 6, ['medium']),
        AisleConfig('conveyable',     'seasonal',   'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'seasonal',   'singleton', 5, 6, ['medium']),
        AisleConfig('conveyable',     'chemical',   'pallet',    5, 6, ['medium']),
        AisleConfig('conveyable',     'chemical',   'singleton', 5, 6, ['medium']),
        AisleConfig('non-conveyable', 'food',       'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'food',       'singleton', 4, 6, ['large']),
        AisleConfig('non-conveyable', 'clothing',   'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'clothing',   'singleton', 4, 6, ['large']),
        AisleConfig('non-conveyable', 'electronic', 'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'electronic', 'singleton', 4, 6, ['large']),
        AisleConfig('non-conveyable', 'furniture',  'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'furniture',  'singleton', 4, 6, ['large']),
        AisleConfig('non-conveyable', 'seasonal',   'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'seasonal',   'singleton', 4, 6, ['large']),
        AisleConfig('non-conveyable', 'chemical',   'pallet',    4, 6, ['large']),
        AisleConfig('non-conveyable', 'chemical',   'singleton', 4, 6, ['large']),
    ],
)

_INVENTORY_CONFIG = InventoryConfig(
    num_skus=120,
    handling_splits=[0.5, 0.5],
    category_splits=[1 / 6] * 6,
)


def _build_world(config: SimConfig) -> tuple:
    """Reset globals, seed RNG, build warehouse + inventory, compute affinity.

    Returns (warehouse, inventory, affinity, WorkloadParams).
    """
    Carton.next_sku = 1
    Aisle.next_aisle_id = 1
    random.seed(config.seed)

    warehouse = Warehouse_Builder().from_config(_WAREHOUSE_CONFIG).build()
    inventory = Inventory_Builder().from_config(_INVENTORY_CONFIG).build()
    Inventory_Manager(warehouse).enqueue_all(inventory.cartons, quantity=config.inventory_qty)

    affinity: AffMatrix = inventory.affinity_matrix()
    wp = WorkloadParams()   # uses updated defaults (pick_weight_coef=0.02)
    return warehouse, inventory, affinity, wp


# ── pick-line extraction ───────────────────────────────────────────────────────

def _pick_lines(task: Task) -> list[tuple[int, int, int]]:
    """(weight, volume, qty) per bin stop that has inventory in this task."""
    return [
        (b.storage.carton.weight, b.storage.carton.volume(), task.items[b.storage.carton.sku])
        for b in task.path
        if b.storage is not None and b.storage.carton.sku in task.items
    ]


# ── formula-based synthetic data ───────────────────────────────────────────────

def generate_formula_records(
    config: SimConfig,
    warehouse,
    inventory: Inventory,
    affinity: AffMatrix,
    wp: WorkloadParams,
) -> list[AisleLoadRecord]:
    """Generate (W_a, lift_sum, observed_L_a) records from the true formula.

    For each batch × aisle:
      1. Compute W_a via aisle_workload().
      2. Compute lift_sum via sum_lift().
      3. True L_a = aisle_load_from_sum(W_a, lift_sum, true_params).
      4. observed_L_a = true_L_a + N(0, noise_std).
      5. With probability outlier_fraction: replace with a scaled outlier.
    """
    rng = random.Random(config.seed + 1)
    true_params = LoadParams(
        lambda_=config.true_lambda,
        k=float(config.k_pickers),
        gamma=config.true_gamma,
    )
    batch_cfg = WBBatchConfig(
        inventory_size=len(inventory.cartons),
        mean_fraction=0.30,
        std_fraction=0.05,
    )

    records: list[AisleLoadRecord] = []
    for batch_id in range(config.n_batches):
        batch = WBBatch(batch_cfg, inventory, affinity=affinity)
        tasks  = Task.from_batch(batch, warehouse)

        for task in tasks:
            lines = _pick_lines(task)
            if not lines:
                continue
            W_a = aisle_workload(
                task.x_traversed, task.y_traversed,
                task.carts_required, lines, wp,
            )
            if W_a <= 0:
                continue
            ls = sum_lift(list(task.items.keys()), affinity)
            if ls <= 0:
                continue

            L_true   = aisle_load_from_sum(W_a, ls, true_params)
            observed = L_true + rng.gauss(0.0, config.noise_std)
            if rng.random() < config.outlier_fraction:
                observed = L_true * config.outlier_scale * rng.uniform(0.5, 1.5)

            records.append(AisleLoadRecord(
                batch_id     = batch_id,
                aisle_id     = task.aisle_id,
                W_a          = W_a,
                lift_sum     = ls,
                observed_L_a = max(0.01, observed),
            ))

    return records


# ── simulation-based data ─────────────────────────────────────────────────────

def generate_simulation_records(
    config: SimConfig,
    warehouse,
    inventory: Inventory,
    affinity: AffMatrix,
    pick_cfg: PickConfig,
    wp: WorkloadParams,
) -> list[AisleLoadRecord]:
    """Generate records where observed_L_a is an actual PickSimulation duration.

    For each batch:
      1. Create Tasks, run PickSimulation.
      2. observed_L_a = task_end.time − task_start.time per aisle.
      3. Compute W_a (analytical baseline) and lift_sum.
      4. Optionally add small measurement noise and inject outliers.
    """
    rng = random.Random(config.seed + 2)
    sim_cfg = PickConfig(
        num_pickers      = config.k_pickers,
        x_move_time      = pick_cfg.x_move_time,
        y_move_time      = pick_cfg.y_move_time,
        pick_intercept   = pick_cfg.pick_intercept,
        pick_weight_coef = pick_cfg.pick_weight_coef,
        pick_volume_coef = pick_cfg.pick_volume_coef,
        cart_swap_coef   = pick_cfg.cart_swap_coef,
    )
    batch_cfg = WBBatchConfig(
        inventory_size=len(inventory.cartons),
        mean_fraction=0.30,
        std_fraction=0.05,
    )

    records: list[AisleLoadRecord] = []
    for batch_id in range(config.n_batches):
        batch = WBBatch(batch_cfg, inventory, affinity=affinity)
        tasks = Task.from_batch(batch, warehouse)
        if not tasks:
            continue

        events = PickSimulation(tasks, sim_cfg).run()

        # Extract per-aisle task duration from task_start / task_end events
        aisle_start: dict[int, float] = {}
        aisle_dur:   dict[int, float] = {}
        for e in events:
            if e.event_type == 'task_start' and e.aisle_id is not None:
                aisle_start[e.aisle_id] = e.time
            elif e.event_type == 'task_end' and e.aisle_id is not None:
                if e.aisle_id in aisle_start:
                    aisle_dur[e.aisle_id] = e.time - aisle_start[e.aisle_id]

        task_by_aisle = {t.aisle_id: t for t in tasks}

        for aisle_id, sim_L_a in aisle_dur.items():
            if sim_L_a <= 0:
                continue
            task  = task_by_aisle.get(aisle_id)
            if task is None:
                continue
            lines = _pick_lines(task)
            if not lines:
                continue
            W_a = aisle_workload(
                task.x_traversed, task.y_traversed,
                task.carts_required, lines, wp,
            )
            if W_a <= 0:
                continue
            ls = sum_lift(list(task.items.keys()), affinity)

            observed = sim_L_a + rng.gauss(0.0, config.noise_std)
            if rng.random() < config.outlier_fraction:
                observed = sim_L_a * config.outlier_scale * rng.uniform(0.5, 1.5)

            records.append(AisleLoadRecord(
                batch_id     = batch_id,
                aisle_id     = aisle_id,
                W_a          = W_a,
                lift_sum     = ls,
                observed_L_a = max(0.01, observed),
            ))

    return records


# ── recovery + export ─────────────────────────────────────────────────────────

def recover_and_export(
    records: list[AisleLoadRecord],
    config: SimConfig,
    run_id: int,
    db_path: str,
    json_path: str,
    do_plot: bool = True,
) -> RecoveredParams:
    """Flag outliers, fit LoadParams, persist to DB and JSON, optionally plot.

    Steps
    -----
    1. Fit on all records → raw_params.
    2. Apply IQR outlier flagging → flagged list.
    3. Fit on clean subset → clean_params.
    4. Compute RMSE for both.
    5. Assign run_id, save flagged records + RecoveredParams to DB.
    6. Export clean params to JSON.
    7. Optionally plot.
    """
    k = float(config.k_pickers)

    # Raw fit
    raw_params = recover_params_from_records(records, k)
    raw_rmse = sqrt(
        sum((aisle_load_from_sum(r.W_a, r.lift_sum, raw_params) - r.observed_L_a) ** 2
            for r in records) / max(len(records), 1)
    )

    # Outlier flagging + clean fit
    flagged = flag_outliers(records, iqr_factor=1.5)
    clean   = [r for r in flagged if not r.is_outlier]

    if len(clean) >= 3:
        clean_params = recover_params_from_records(clean, k)
        clean_rmse = sqrt(
            sum((aisle_load_from_sum(r.W_a, r.lift_sum, clean_params) - r.observed_L_a) ** 2
                for r in clean) / len(clean)
        )
    else:
        clean_params = raw_params
        clean_rmse   = raw_rmse

    # Assign run_id to all records before DB write
    for r in flagged:
        r.run_id = run_id

    save_aisle_loads(db_path, run_id, flagged)

    rp = RecoveredParams(
        run_id     = run_id,
        lambda_    = clean_params.lambda_,
        k          = clean_params.k,
        gamma      = clean_params.gamma,
        n_samples  = len(records),
        n_clean    = len(clean),
        rmse_raw   = raw_rmse,
        rmse_clean = clean_rmse,
        timestamp  = datetime.now(timezone.utc).isoformat(),
    )
    save_recovered_params(db_path, rp)
    export_params_json(rp, json_path)

    print(
        f'  Run {run_id}: n={len(records)}, clean={len(clean)}, '
        f'RMSE raw={raw_rmse:.3f}, clean={clean_rmse:.3f}\n'
        f'  Recovered  lambda={clean_params.lambda_:.3f}  '
        f'gamma={clean_params.gamma:.3f}  k={clean_params.k}'
    )

    if do_plot:
        plot_loads(
            flagged,
            raw_params=raw_params,
            clean_params=clean_params,
            title=f'Run {run_id} — Aisle Load Recovery',
        )

    return rp


# ── public pipeline entry points ──────────────────────────────────────────────

def run_formula_pipeline(
    config: SimConfig | None = None,
    db_path: str = 'optimization_runs.db',
    json_path: str = 'recovered_params.json',
    plot: bool = True,
) -> RecoveredParams:
    """Formula-based pipeline: synthetic data from the true equation."""
    if config is None:
        config = SimConfig()

    print(f'[formula] seed={config.seed}  n_batches={config.n_batches}  '
          f'true_lambda={config.true_lambda}  true_gamma={config.true_gamma}')

    warehouse, inventory, affinity, wp = _build_world(config)
    records = generate_formula_records(config, warehouse, inventory, affinity, wp)
    print(f'  Generated {len(records)} aisle-load observations')

    init_run_db(db_path)
    run_id = create_run(db_path, 'formula')

    return recover_and_export(records, config, run_id, db_path, json_path, plot)


def run_simulation_pipeline(
    config: SimConfig | None = None,
    pick_cfg: PickConfig | None = None,
    db_path: str = 'optimization_runs.db',
    json_path: str = 'recovered_params.json',
    plot: bool = True,
) -> RecoveredParams:
    """Simulation-based pipeline: observed L_a from actual PickSimulation runs."""
    if config is None:
        config = SimConfig()
    if pick_cfg is None:
        pick_cfg = PickConfig(num_pickers=config.k_pickers)

    print(f'[simulation] seed={config.seed}  n_batches={config.n_batches}  '
          f'k_pickers={config.k_pickers}', flush=True)

    warehouse, inventory, affinity, wp = _build_world(config)
    wp = WorkloadParams.from_pick_config(pick_cfg)
    records = generate_simulation_records(
        config, warehouse, inventory, affinity, pick_cfg, wp
    )
    print(f'  Generated {len(records)} aisle-load observations')

    init_run_db(db_path)
    run_id = create_run(db_path, 'simulation')

    return recover_and_export(records, config, run_id, db_path, json_path, plot)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run aisle-load parameter recovery pipeline.')
    parser.add_argument('--sim',      action='store_true', help='Use simulation pipeline instead of formula')
    parser.add_argument('--batches',  type=int,   default=300,  help='Number of synthetic batches')
    parser.add_argument('--noise',    type=float, default=2.0,  help='Gaussian noise std')
    parser.add_argument('--outliers', type=float, default=0.05, help='Outlier fraction (0–1)')
    parser.add_argument('--seed',     type=int,   default=42)
    parser.add_argument('--db',       type=str,   default='optimization_runs.db')
    parser.add_argument('--json',     type=str,   default='recovered_params.json')
    parser.add_argument('--no-plot',  action='store_true')
    args = parser.parse_args()

    cfg = SimConfig(
        n_batches        = args.batches,
        noise_std        = args.noise,
        outlier_fraction = args.outliers,
        seed             = args.seed,
    )

    # Resolve paths relative to this file's directory
    base = os.path.dirname(os.path.abspath(__file__))
    db_path   = os.path.join(base, args.db)
    json_path = os.path.join(base, args.json)

    if args.sim:
        run_simulation_pipeline(cfg, db_path=db_path, json_path=json_path, plot=not args.no_plot)
    else:
        run_formula_pipeline(cfg, db_path=db_path, json_path=json_path, plot=not args.no_plot)
