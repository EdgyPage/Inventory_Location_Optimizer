# Simulation lifecycle &amp; method

How a run is built, what every result page holds constant, and what it varies. This is the
**reference page** for the terms the comparison write-ups cite — every stage below names the
source code that implements it and reports this experiment's numbers straight from the run's
committed snapshots. Symbols are defined in the [Glossary](glossary.md).

## Contents

- [Lifecycle at a glance](#lifecycle-at-a-glance)
- [1. Generation](#1-generation) — the synthetic SKU catalogue
- [2. Stock](#2-stock-initial-layout) — the initial warehouse layout
- [3. Pick](#3-pick) — batch demand and the pick-time cost
- [4. Reorder](#4-reorder) — when replenishment is triggered
- [5. Restock](#5-restock) — how replenishment is placed (the assignment function)
- [Top-3 assignment functions](#top-3-assignment-functions) — the winning equations
- [Invariants vs experiment terms](#invariants-vs-experiment-terms)

## Lifecycle at a glance

A run is one synthetic inventory, stocked once, then picked over many batches. Only the
**restock** step differs between strategies — everything else is shared.

```text
                         ┌──────────────────────── repeat × N_BATCHES ────────────────────────┐
                         │                                                                     │
  generate_inventory ─►  stock warehouse  ─►  sample batch ─► pick / deplete ─► check_reorders ─┘
   (catalogue +           (opt | uni            (demand +        (t_pick per       │        ▲
    q_eq, ROP, lead        initial layout)       affinity)        task, W)         │        │
    from params.json)                                                        reorder│        │restock
                                                                             (ROP)  ▼        │(lead arrives)
                                                                           lead queue ───────┘
                                                                          (wait `lead` batches,
                                                                           then place via the
                                                                           assignment function)
```

The **reorder → lead queue → restock** loop is the only place a strategy acts: a depleted
SKU is re-ordered, waits out its lead time, then the arriving units are *placed* by the
strategy's assignment function. Picking, demand, and the warehouse are identical across
strategies, so any performance gap is attributable to placement alone.

## 1. Generation

The synthetic SKU catalogue is produced once by
[`Warehouse/generation/generate_inventory.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/generation/generate_inventory.py)
and snapshotted to `params.json`. Each SKU gets size, weight, handling, a per-batch demand,
and — derived from that demand — an equilibrium quantity and a reorder point:

!!! abstract "Equilibrium / reorder model"
    {{ reorder_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

    Coverage is **{{ inv_params('mixed_realistic_lt0')['equilibrium_coverage_batches'] }}**
    batches of expected demand; the reorder point triggers replenishment `lead + safety`
    batches ahead of stock-out. The full six-category creation plan — shares, dimension,
    weight, handling, and demand distributions — is on the
    [Inventory distributions](inventory.md) page, generated from the same snapshot.

The two committed variants (`lt0`, `ltrand0-5`) share one seed-42, 100,000-SKU catalogue and
differ **only** in replenishment lead time.

## 2. Stock (initial layout)

Before batch 1 the whole catalogue is stocked once, into bins grouped by
`BinKey = (handling, category, storage_size, unit_type)`. Two initial layouts bracket the
starting point (see [strategy_runner.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/strategy_runner.py) — the `stock_mode` branch):

- **`uni`** — `enqueue_all` → uniform-random placement (a deliberately poor start); the
  assignment function has to *climb* from there.
- **`opt`** — **policy-stocked**: the strategy's own reorder placement is built *first*, then
  the whole inventory is placed **through that same assignment function**, so each arm starts
  at **its own** ideal layout (not a generic optimum). The batch loop then perturbs it and the
  reorder rule must hold it.

The headline setup for this run's `lt0` variant:

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

## 3. Pick

Each batch draws SKUs weighted by demand (and affinity, for co-picked partners), then
simulates the pickers clearing them. The per-task cost model:

{{ pick_time_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

Realised aisle workload decomposes as `W = D + P + C` (travel + pick + cart) — the
*measurement* that drives [makespan](glossary.md#makespan); the assignment functions optimise
proxies of it (defined in the [Glossary](glossary.md#workload)). Picking a SKU decrements its
on-hand quantity; once its inventory [**position**](glossary.md#position) (on-hand + queued +
in-transit) falls to the reorder point (ROP), it is flagged for replenishment.

## 4. Reorder

Once per batch, `check_reorders`
([Warehouse/inventory_reorder.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/inventory_reorder.py))
scans flagged SKUs and issues an order-up-to quantity for any whose position ≤ ROP:

```text
position = on_hand + queued + in_transit
if position ≤ reorder_point:
    order  ≈ equilibrium_qty + pipeline − position     (received qty jittered by supply_cv)
    enter lead queue with delay = lead_time  (lt0: 0 batches;  ltrand0-5: uniform 0–5)
```

The position check fires an order **at most once** per SKU while stock is in transit, so
orders don't stack.

## 5. Restock

Each batch the lead queue ages by one; arrived orders are released to stock and **placed by
the strategy's assignment function** — this is the only step strategies differ on:

```text
per batch:  lead_queue[*].remaining -= 1
            for orders with remaining ≤ 0:  release units ─► place via placement.place_one / place_wave
                                                             └──────────── back to §3 Pick ───────────┘
```

`fifo` (first-in-first-out) drops arrivals into a uniform-random bin; the ranked/map families
rank and slot them toward the layout optimum. All **16 restock families** and two initial
layouts are catalogued on the [Assignment functions](assignment-functions.md) page; the three
that win are below.

## Top-3 assignment functions

The winners of the committed sweep, by total task time vs the FIFO baseline: **Rank_labor**
(≈ −3.9%), **Map**, and **Map_rank** (≈ −3.0%). Their objective functions, transcribed from
the source that defines them (there is no JSON snapshot of these yet — see the
[macros note](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/docs/macros.py)):

{{ assignment_formulas() }}

## Invariants vs experiment terms

Everything a result page can vary, and everything it holds fixed so a comparison is clean.

**Invariants** — identical across every run on these pages:

| Held constant | Value | Source |
|---------------|-------|--------|
| Catalogue seed | {{ inv_params('mixed_realistic_lt0')['seed'] }} | `params.json` |
| SKUs | {{ '{:,}'.format(inv_params('mixed_realistic_lt0')['num_skus']) }} | `params.json` |
| Equilibrium coverage | {{ inv_params('mixed_realistic_lt0')['equilibrium_coverage_batches'] }} batches | `params.json` |
| Supply-CV ceiling | {{ inv_params('mixed_realistic_lt0')['supply_cv_max'] }} | `params.json` |
| Category creation plan | 6 categories | [Inventory distributions](inventory.md) |
| Batches / pickers | see setup table above | `config.json` |
| Warehouse geometry, pick-time model | identical across variants | `config.json` / [run_simulation.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/run_simulation.py) |

**Experiment terms** — what a comparison sweeps:

| Varied | Levels |
|--------|--------|
| Replenishment lead time | `lt0` (immediate) · `ltrand0-5` (uniform 0–5 batches) |
| Pick-time calibration | `calibrated` · `_high_weight` · `_high_height` · `_high_weight_high_height` |
| Placement strategy | initial layout {uni, opt} × restock family {fifo, rank_labor, map, map_rank, tmin, …} |

See the [results write-ups](index.md) for how each strategy performs on these catalogues.
