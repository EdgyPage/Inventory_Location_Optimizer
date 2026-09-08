# Simulation lifecycle &amp; method

How a run is built, what every result page holds constant, and what it varies. This is the
**reference page** for the terms the comparison write-ups cite — every stage below names the
source code that implements it and reports this experiment's numbers straight from the run's
committed snapshots. Symbols are defined in the [Glossary](glossary.md).

{% set inv0 = (experiment().inventories.keys() | list) | first %}

## Contents

- [Lifecycle at a glance](#lifecycle-at-a-glance)
- [1. Generation](#1-generation) — the synthetic SKU catalogue
- [2. Stock](#2-stock-initial-layout) — the stock levels the run declares, and the initial layout
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
   (catalogue: size,      (declare q_eq and     (demand +        (t_pick per       │        ▲
    weight, demand,        ROP from declared     affinity)        task, W)         │        │
    lead — and no          days of cover, then                               reorder│        │restock
    stock levels)          the opt | uni layout)                             (ROP)  ▼        │(lead arrives)
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
and snapshotted to `params.json`. Each SKU gets a size, a weight, a handling class, a per-batch
demand, a replenishment lead time and a supply reliability — and **no stock levels**. A
catalogue describes the goods a site sells; how much of each to keep on the shelf is a decision
about how *this* site is run, so the run declares it at setup
([§2 below](#stock-declaration)) instead of inheriting it from the SKU. Two runs on
the same catalogue can therefore hold quite different stock.

The full creation plan — shares, dimension, weight, handling, and demand distributions — is on
the [Inventory distributions](inventory.md) page, generated from the same snapshot.

This experiment's inventory variants:
{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — lead time {{ inv_lead_time(key) }}.
{% endfor %}

## 2. Stock (initial layout)

Before batch 1 the run settles the two questions the catalogue leaves open: **how much** of each
SKU to hold, and **where** to put it.

### How much — the stock declaration { #stock-declaration }

Stock is declared in **days of cover**. The run first works out how many pick lines a day its
declared crew can clear; that turns each SKU's share of the demand into an expected number of
units per day, $d_s$. Three declared inputs then fix that SKU's two levels:

| Declared input | What it means | Default |
|----------------|---------------|---------|
| `coverage_days` | days of its own demand a SKU holds when it is full | 10 |
| `safety_days` | extra days of buffer folded into the reorder trigger | 2 |
| `floor_lines` | the least a SKU ever holds, in lines of its own average pick | 1 |

```text
line floor L    = ceil(floor_lines × the SKU's average line)      (at least 1 unit)
equilibrium_qty = max(round(coverage_days × d_s), L)              the order-up-to target
reorder_point   = min(equilibrium_qty − 1,
                      max(round(d_s × (lead + safety_days)), L))  the trigger
```

The floor is the operational safeguard: a SKU never holds less than one pick's worth of itself,
so a single line can always be filled off the shelf. A slow mover whose cover is shorter than
the interval between its own picks lands **on** that floor, with a trigger one unit below its
target — every pick reorders exactly what it took, which is the textbook base-stock policy. How
much of a catalogue sits there is worth checking before reading a coverage figure as the binding
constraint: on a slow-moving assortment it can be most of it.

The three inputs are recorded per run in the run spec under `staffing.inputs`. The derivation
that follows from them — the declared days, each round of the sizing loop, the share of SKUs
(and of demand) sitting on the floor, and the expected first-pass fill rate — is recorded under
`staffing.calibration[<pair>].coverage`.

### Where — the initial layout { #initial-layout }

The declared stock is then put away once, into bins grouped by
`BinKey = (handling, category, storage_size, unit_type)`. Two initial layouts bracket the
starting point (see [strategy_runner.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/simdriver/strategy_runner.py) — the `stock_mode` branch):

- **`uni`** — `enqueue_all` → uniform-random placement (a deliberately poor start); the
  assignment function has to *climb* from there.
- **`opt`** — **policy-stocked**: the strategy's own reorder placement is built *first*, then
  the whole inventory is placed **through that same assignment function**, so each arm starts
  at **its own** ideal layout (not a generic optimum). The batch loop then perturbs it and the
  reorder rule must hold it.

The headline setup for a representative variant — including the levels this run declared,
averaged over the SKUs it actually fielded:

{{ setup_table(inv0) }}

## 3. Pick

**Batch → tasks.** Each batch first samples a set of SKUs to pick — the batch size is
$n \sim \mathcal{N}(0.15\,N,\ 0.05\,N)$ distinct SKUs (weighted by demand, and by affinity for
co-picked partners). Those picks are then grouped into **tasks, one per aisle**: a task is the
ordered sweep through the bins a picker visits in a single aisle (forward/singleton bins drained
before reserve/pallet). The tasks are handed to the $K$ pickers round-robin by aisle.

**Per stop**, the pick-time cost is the model below (this experiment's `calibrated` config; the
full form and all calibrations are on the [Formula reference](formula-reference.md#pick-time)):

{{ pick_time_formula(inv0) }}

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
([Warehouse/inventory/inventory_reorder.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/inventory/inventory_reorder.py))
scans flagged SKUs and issues an order-up-to quantity for any whose position ≤ ROP:

```text
position = on_hand + queued + in_transit
if position ≤ reorder_point:
    order  ≈ equilibrium_qty + pipeline − position     (received qty jittered by supply_cv)
    enter lead queue with delay = lead_time  (lt0: 0 batches;  ltrand0-5: uniform 0–5)
```

The position check fires an order **at most once** per SKU while stock is in transit, so
orders don't stack. `pipeline` is the stock expected to be in transit over the lead — stamped
onto the SKU by the same declaration that set its levels ([§2](#stock-declaration)) — so on-hand
lands back **at** the target when the delivery arrives rather than short of it.

## 5. Restock

Each batch the lead queue ages by one; arrived orders are released to stock and **placed by
the strategy's assignment function** — this is the only step strategies differ on:

```text
per batch:  lead_queue[*].remaining -= 1
            for orders with remaining ≤ 0:  release units ─► place via placement.place_one / place_wave
                                                             └──────────── back to §3 Pick ───────────┘
```

`{{ experiment().baseline }}` drops arrivals into a uniform-random bin; the ranked/map families
rank and slot them toward the layout optimum, scoring bins with the placement primitive
$\ell(b)$ and demand/affinity. All **16 restock families** — their scoring objectives and the
winners' equations — are catalogued on the [Formula reference](formula-reference.md#the-families).
The winners highlighted in this experiment are
{% for w in experiment().winners %}`{{ w }}`{% if not loop.last %}, {% endif %}{% endfor %}.

## Invariants vs experiment terms

Everything a result page can vary, and everything it holds fixed so a comparison is clean.

**Invariants** — identical across every run on these pages:

<!-- The stock-coverage row names the run's declared inputs; no macro exposes their values, so
     write this run's numbers in by hand from its run spec (`staffing.inputs`) — or leave the
     row as it stands, which is true of any run under the defaults. -->

| Held constant | Value | Source |
|---------------|-------|--------|
| Catalogue seed | {{ inv_params(inv0)['seed'] }} | `params.json` |
| SKUs | {{ '{:,}'.format(inv_params(inv0)['num_skus']) }} | `params.json` |
| Stock coverage | declared in days, once per run — `coverage_days` / `safety_days` / `floor_lines` (see [§2](#stock-declaration)) | run spec, `staffing.inputs` |
| Supply-CV ceiling | {{ inv_params(inv0)['supply_cv_max'] }} | `params.json` |
| Batches / pickers | see setup table above | `config.json` |
| Warehouse geometry, pick-time model | identical across variants | `config.json` / [run_simulation.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/run_simulation.py) |

**Experiment terms** — what this comparison sweeps:

| Varied | Levels |
|--------|--------|
| Replenishment lead time | {% for key, inv in experiment().inventories.items() %}`{{ key }}`{% if not loop.last %} · {% endif %}{% endfor %} |
| Pick-time calibration | {% for c in experiment().configs %}`{{ c.name }}`{% if not loop.last %} · {% endif %}{% endfor %} |
| Placement strategy | initial layout {uni, opt} × 16 restock families (see [Formula reference](formula-reference.md#the-families)) |

See the [experiment overview](index.md) for how each strategy performs on these catalogues.
