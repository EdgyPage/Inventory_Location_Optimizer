# Simulation lifecycle &amp; method

How a run is built, what every result page holds constant, and what it varies. This is the
**reference page** for the terms the results pages cite — every stage below names the
source code that implements it and reports this experiment's numbers straight from the run's
committed snapshots. Symbols are defined in the [Glossary](glossary.md).

{% set inv0 = (experiment().inventories.keys() | list) | first %}

## Contents

- [Lifecycle at a glance](#lifecycle-at-a-glance)
- [1. Generation](#1-generation) — the synthetic SKU catalogue
- [2. Stock](#2-stock-initial-layout) — the initial warehouse layout
- [3. Pick](#3-pick) — batch → tasks, the pick-time cost, and task labor
- [4. Reorder](#4-reorder) — when replenishment is triggered
- [5. Restock](#5-restock) — how replenishment is placed (the assignment function)
- [Invariants vs experiment terms](#invariants-vs-experiment-terms)

All the equations referenced below are collected on the
[Formula reference](formula-reference.md) page.

!!! note "Why a synthetic warehouse, and what that buys"
    The catalogue is synthetic and the pick-time model is stated, not fitted to a particular real
    building — deliberately. A real building cannot run the same day twice, let alone 68 times;
    the simulation replays the **identical** order stream under every rule, so a gap between two
    runs is attributable to the one decision that changed, with no seasonality, staffing, or
    demand noise in the way. That control is the product; the price is that absolute hours are
    model units. The model's scale (384 aisles, 25 pickers, 75 batches) is likewise a choice, not
    a claim about any real site: the levers act through mechanisms every building has — idle time
    pooling at the end of a batch, travel varying by slot — so the *direction and ranking* of
    results is the transferable part, and a pilot prices the magnitude for a given floor. The
    strongest evidence for that transfer so far is internal: the scheduler finding has now
    survived a complete change of catalogue ([Experiment 6](../experiment-6/index.md) →
    [Experiment 7](../experiment-7/index.md)) AND a change of demand stream (Experiment 7 → this
    run) — while the placement winners proved sensitive to the supply model, which is itself a
    transferable caution.

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

This sweep adds one more axis *outside* the loop: the same frozen inventory is run under two
**picker schedulers** — round-robin hand-out (cell `k1_off_rr`) and longest-task-first LPT
(cell `k1_off_lpt`). Everything inside the loop is byte-identical between the two cells, which is
what makes the scheduler comparison clean.

## 1. Generation

The synthetic SKU catalogue is produced once by
[`Warehouse/generation/generate_inventory.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/generation/generate_inventory.py)
and snapshotted to `params.json`. Each SKU gets size, weight, handling, a per-batch demand,
and — derived from that demand — an equilibrium quantity and a reorder point:

!!! abstract "Equilibrium / reorder model"
    {{ reorder_formula(inv0) }}

    Coverage is **{{ inv_params(inv0)['equilibrium_coverage_batches'] }}**
    batches of expected demand; the reorder point triggers replenishment `lead + safety`
    batches ahead of stock-out. The full creation plan — shares, dimension, weight, handling,
    and demand distributions — is on the [Inventory distributions](inventory.md) page,
    generated from the same snapshot.

The catalogue lists {{ '{:,}'.format(inv_params(inv0)['num_skus']) }} SKUs; the shared build
stocks the subset that fits its racking (the setup table's `n_skus` row below), which is why the
two counts differ — one is the catalogue, the other is what got shelved. **`n_skus` is a
build-level number, not a per-channel one**: both channels' `config.json` files report the same
value because they describe the same build. What is partitioned per channel is **bins** — every
bin belongs to exactly one channel's regime — and the run records those counts. It does not
record how many distinct SKUs land in each partition, so this site quotes no per-channel SKU
figure; "two warehouses" is a claim about disjoint bins and independent order streams, which the
data supports, and not a claim that the two channels hold disjoint SKU sets, which it does not
measure either way.

This experiment's inventory variants:
{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — lead time {{ inv_lead_time(key) }}.
{% endfor %}

## 2. Stock (initial layout)

Before batch 1 the whole catalogue is stocked once, into bins grouped by
`BinKey = (handling, category, storage_size, unit_type)`. Two initial layouts bracket the
starting point (see [strategy_runner.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/simdriver/strategy_runner.py) — the `stock_mode` branch):

- **`uni`** — `enqueue_all` → uniform-random placement (a deliberately poor start); the
  assignment function has to *climb* from there.
- **`opt`** — **policy-stocked**: the strategy's own reorder placement is built *first*, then
  the whole inventory is placed **through that same assignment function**, so each arm starts
  at **its own** ideal layout (not a generic optimum). The batch loop then perturbs it and the
  reorder rule must hold it. This is not a head start that decides the race — every rule gets
  the same treatment, FIFO included, and the `uni`/`opt` gap comes out tiny on the results
  pages: the restock rule, not the starting layout, drives the outcome.

The headline setup for a representative variant (the first inventory, `bell_lt0` — the same one
the results pages' Figures 1 and 3 use). A precision note on what these totals describe: the
simulation builds **one shared structure** whose totals appear below (384 aisles / 398,500 bins
across both channels — identical in every leaf's `config.json` because it is the same build),
and each channel operates its **own disjoint partition** of that racking — store picks only its
**149,800** store-regime bins, fulfillment only its **248,700** fulfillment-regime bins (counts
from the run's own runtime metrics). A bin belongs to exactly one channel, and each channel runs
its own independent order stream against its own bins — so the two channels never contend for a
slot, which is what "two warehouses" means operationally. One caveat for a real network: a DC
where store and fulfillment draw from the **same** physical slots has cross-channel contention
this design deliberately excludes.

{{ setup_table(inv0) }}

<small>**The table above is the shared build and the STORE channel's crew.** Fulfillment runs the
same racking with its own numbers, collected here so this page answers a headcount question
without sending you elsewhere: **20 pickers** (store: 25) and **248,700** bins in its own regime
partition (store: 149,800). Note what is *not* recorded per channel: `config.json`'s SKU total
(263,257) is the whole shared build's stocked catalogue, and the run does not separately record
how many distinct SKUs each channel's partition holds — bins are partitioned and counted, SKUs
are not, so no per-channel SKU figure is quoted anywhere on this site.

**How many tasks a wave actually contains.** A task is one aisle's worth of picking, and it is
the unit the scheduler hands out, so the ratio of tasks to pickers is what decides whether
longest-first has anything to rebalance. Per wave the store generates **103–139 tasks**
(median 137) for its **25** pickers — about five and a half aisles each — and fulfillment
**235** for its **20** — closer to twelve. Both are comfortably task-rich: a picker who finishes
early has more work to pull from the same wave, so the scheduling gain is genuine rebalancing
rather than an artifact of the crew running out of aisles. <small>Counts from the run's
per-batch `num_tasks` column.</small>

The two channels also differ in three pick-time constants — `pick_intercept`, `cart_swap_coef` and
`batch_mean_frac` — whose per-channel values are in each leaf's committed `config.json`; the
batch-size fraction is the one that shows up in the formulas above ($0.15$ store, $0.20$
fulfillment).</small>

## 3. Pick

**Batch → tasks.** Each batch first samples a set of SKUs to pick — the batch size is
$n \sim \mathcal{N}(\mu\,N,\ 0.05\,N)$ distinct SKUs, with the mean fraction $\mu$ set per
channel ($0.15$ store, $0.20$ fulfillment — identical across all arms *within* a channel, which
is the invariance the comparisons need), weighted by demand and by affinity for co-picked
partners. Those picks are then grouped into **tasks, one per aisle**: a task is the
ordered sweep through the bins a picker visits in a single aisle (forward/singleton bins drained
before reserve/pallet). The tasks are handed to the $K$ pickers by the cell's scheduler —
round-robin by aisle in `k1_off_rr`, longest-task-first in `k1_off_lpt`. Because a task IS an
aisle, **at most one picker works an aisle per batch** — aisle assignment is exclusive by
construction, so picker-vs-picker congestion inside an aisle cannot occur in the model. (Real
floors interleave more freely; blocked aisles, pallet drops, and passing delays are outside this
model's scope — a pilot inherits whatever congestion its floor already has, under either
scheduler equally.) Restock and picking never overlap either: each batch places all arrived
restock **first**, then picks — so restocker-in-the-aisle interference at the point of putaway
is likewise out of the model's scope, symmetric across every arm.

**Per stop**, the pick-time cost is the model below — *readable but skippable: if you want the
operational takeaways rather than the cost model, jump ahead to
"[4. Reorder](#4-reorder)" and lose nothing* — (this experiment's `calibrated` config; the
full form and all calibrations are on the [Formula reference](formula-reference.md#pick-time) —
stated ergonomic assumptions, not field-fitted coefficients; the formula page's opening note says
what that does and doesn't license):

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
orders don't stack.

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
$\ell(b)$ and demand/affinity. All **17 restock families** — their scoring objectives and the
winners' equations — are catalogued on the [Formula reference](formula-reference.md#the-families).
The winners highlighted in this experiment are
{% for w in experiment().winners %}`{{ w }}`{% if not loop.last %}, {% endif %}{% endfor %}.

## Invariants vs experiment terms

Everything a result page can vary, and everything it holds fixed so a comparison is clean.

**Invariants** — identical across every run on these pages:

| Held constant | Value | Source |
|---------------|-------|--------|
| Catalogue seed | {{ inv_params(inv0)['seed'] }} | `params.json` |
| SKUs (catalogue) | {{ '{:,}'.format(inv_params(inv0)['num_skus']) }} | `params.json` |
| Equilibrium coverage | {{ inv_params(inv0)['equilibrium_coverage_batches'] }} batches | `params.json` |
| Supply-CV ceiling | {{ inv_params(inv0)['supply_cv_max'] }} | `params.json` |
| Batches / pickers | see setup table above | `config.json` |
| Warehouse geometry, pick-time model | identical across variants | `config.json` / [run_simulation.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/run_simulation.py) |

**Experiment terms** — what this comparison sweeps:

| Varied | Levels |
|--------|--------|
| Picker scheduler | `k1_off_rr` (round-robin) · `k1_off_lpt` (LPT) — one cell each |
| Replenishment lead time | {% for key, inv in experiment().inventories.items() %}`{{ key }}`{% if not loop.last %} · {% endif %}{% endfor %} |
| Pick-time calibration | {% for c in experiment().configs %}`{{ c.name }}`{% if not loop.last %} · {% endif %}{% endfor %} |
| Placement strategy | initial layout {uni, opt} × 17 restock families = **34 arms** (see [Formula reference](formula-reference.md#the-families)) |

See the [Results](index.md) page for how each strategy performs on these catalogues.
