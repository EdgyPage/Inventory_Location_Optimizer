# Simulation lifecycle &amp; method

How a run is built, what every result page holds constant, and what it varies. This is the
**reference page** for the terms the [Highlights](highlights.md) and [Everything else](everything-else.md)
pages cite — every stage below names the source code that implements it and reports this
experiment's numbers straight from the run's committed snapshots. Symbols are defined in the
[Glossary](glossary.md).

## Contents

- [Lifecycle at a glance](#lifecycle-at-a-glance)
- [Two independent warehouses](#two-independent-warehouses)
- [1. Generation](#1-generation) — the synthetic SKU catalogue
- [2. Stock](#2-stock-initial-layout) — the initial warehouse layout
- [3. Pick](#3-pick) — batch → tasks, the pick-time cost, and task labor
- [4. Reorder](#4-reorder) — when replenishment is triggered
- [5. Restock](#5-restock) — how replenishment is placed (the assignment function)
- [Invariants vs experiment terms](#invariants-vs-experiment-terms)

All the equations referenced below are collected on the
[Formula reference](formula-reference.md) page.

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

The **reorder → lead queue → restock** loop is the only place a strategy acts: a depleted SKU is
re-ordered, waits out its lead time, then the arriving units are *placed* by the strategy's
assignment function. Picking, demand, and the warehouse are identical across strategies, so any
performance gap is attributable to placement alone.

## Two independent warehouses

Experiment 2's defining change from Experiment 1 is that the operation is split into **two
channels** that are generated from the same catalogue but run as **separate warehouses**, each
with its own cart, crew, travel speeds, and pick-time cost model:

- **Store** — a `StoreCart` operation (25 pickers, cross/along speeds 3/2 ft·s⁻¹) with a steep
  weight term ($0.58\,w^{1.5}$). Only the labor-family restock subset it would actually deploy is
  swept (`STORE_RESTOCKS`: FIFO, Rank_labor, Rank_cartlabor).
- **Fulfillment** — a `FulfillmentCart` operation (20 pickers, cross/along speeds 2/4 ft·s⁻¹) with
  a gentler logarithmic weight term ($0.7\log w$). The **full 17-family suite** is swept here.

The channels never share a cart or a labor measurement; they are analysed independently and can
have **different winners**. Each channel also has two calibrations (see
[experiment terms](#invariants-vs-experiment-terms)). The per-channel cost models are on the
[Formula reference](formula-reference.md#pick-time).

## 1. Generation

The synthetic SKU catalogue is produced once by
[`Warehouse/generation/generate_inventory.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/generation/generate_inventory.py)
and snapshotted to `params.json`. Each SKU gets size, weight, handling, a per-batch demand,
and — derived from that demand — an equilibrium quantity and a reorder point:

!!! abstract "Equilibrium / reorder model"
    {{ reorder_formula('lt0', 'store') }}

    Coverage is **{{ inv_params('lt0')['equilibrium_coverage_batches'] }}**
    batches of expected demand; the reorder point triggers replenishment `lead + safety`
    batches ahead of stock-out. The full eight-category creation plan — shares, dimension,
    weight, handling, and demand distributions — is on the
    [Inventory baselines](inventory.md) page, generated from the same snapshot.

The two committed variants (`lt0`, `ltrand0-5`) share one seed-42, 130,000-SKU catalogue and
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

The headline setup for this run's **store** channel (`lt0`, base calibration):

{{ setup_table('lt0', 'store') }}

…and for the **fulfillment** channel (`lt0`, base calibration) — same catalogue and geometry,
different cart, crew, speeds, and pick-time model:

{{ setup_table('lt0', 'ful_calibrated') }}

## 3. Pick

**Batch → tasks.** Each batch first samples a set of SKUs to pick — the batch size is
$n \sim \mathcal{N}(\text{frac}\cdot N,\ 0.05\,N)$ distinct SKUs (weighted by demand, and by
affinity for co-picked partners; the store channel uses a 0.15 fraction, fulfillment 0.20). Those
picks are then grouped into **tasks, one per aisle**: a task is the ordered sweep through the bins
a picker visits in a single aisle (forward/singleton bins drained before reserve/pallet). The
tasks are handed to the pickers round-robin by aisle.

**Per stop**, the pick-time cost is the channel's cost model (below is the **store** channel's
`store` calibration; the full form and all four calibrations are on the
[Formula reference](formula-reference.md#pick-time)):

{{ pick_time_formula('lt0', 'store') }}

Clearing an aisle costs the realised **task labor** $W = H + T + C$ — **handling** (the
height-scaled at-location picks), **travel** (the *Manhattan* sweep distance), and **cart**
swaps — defined on the [Formula reference](formula-reference.md#task-labor). Summed over a
batch's tasks, $W$ is the [makespan](glossary.md#makespan) proxy every assignment function is
ultimately judged on. Note the placement scorers don't optimise $W$ itself — they use a cheaper
per-bin proxy $\ell(b)$ ([Formula reference](formula-reference.md#placement-primitive-ellb)).

Picking a SKU decrements its on-hand quantity; once its inventory
[**position**](glossary.md#position) (on-hand + queued + in-transit) falls to the reorder point
(ROP), it is flagged for replenishment.

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

`fifo` (first-in-first-out) drops arrivals into a uniform-random bin; the ranked/map/co-demand
families rank and slot them toward the layout optimum, scoring bins with the placement primitive
$\ell(b)$ and demand/affinity. All **17 restock families** — their scoring objectives and the two
channel winners' equations — are catalogued on the
[Formula reference](formula-reference.md#the-families). The winners of this sweep, by total task
time vs the FIFO baseline, are **Rank_labor** for the store channel (≈ −3.7%) and **Compact** for
the fulfillment channel (≈ −3.4%). See the [Highlights](highlights.md).

## Invariants vs experiment terms

Everything a result page can vary, and everything it holds fixed so a comparison is clean.

**Invariants** — identical across every run on these pages:

| Held constant | Value | Source |
|---------------|-------|--------|
| Catalogue seed | {{ inv_params('lt0')['seed'] }} | `params.json` |
| SKUs (catalogue) | {{ '{:,}'.format(inv_params('lt0')['num_skus']) }} | `params.json` |
| Equilibrium coverage | {{ inv_params('lt0')['equilibrium_coverage_batches'] }} batches | `params.json` |
| Supply-CV ceiling | {{ inv_params('lt0')['supply_cv_max'] }} | `params.json` |
| Category creation plan | 8 entries (6 categories × handling) | [Inventory baselines](inventory.md) |
| Batches | see setup tables above | `config.json` |
| Warehouse geometry | identical across channels/variants | `config.json` |

**Experiment terms** — what this sweep varies:

| Varied | Levels |
|--------|--------|
| Warehouse channel | `store` (StoreCart, labor subset) · `fulfillment` (FulfillmentCart, full suite) |
| Pick-time calibration | store: `store` · `store_high_weight` — fulfillment: `ful_calibrated` · `ful_calibrated_fast_walkers` |
| Replenishment lead time | `lt0` (immediate) · `ltrand0-5` (uniform 0–5 batches) |
| Placement strategy | initial layout {uni, opt} × restock family {fifo, rank_labor, comp, map, tmin, …} |

See the [Highlights](highlights.md) for how each channel's winner performs, and
[Everything else](everything-else.md) for the whole strategy suite.
