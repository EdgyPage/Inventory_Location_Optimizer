# How a run works — the coupled site

How a run is built, what every result page holds constant, and what this experiment varies.
This is the **reference page** for the terms the results pages cite — every stage below names
the source code that implements it and reports this experiment's numbers straight from the run's
committed snapshots. Symbols are defined in the [Glossary](glossary.md).

{% set inv0 = (experiment().inventories.keys() | list) | first %}

## Contents

- [Lifecycle at a glance](#lifecycle-at-a-glance)
- [1. Generation](#1-generation) — the synthetic SKU catalogue
- [2. Stock](#2-stock-initial-layout) — the initial warehouse layout
- [3. Pick](#3-pick) — batch → tasks, the pick-time cost, and task labor
- [4. Reorder, dispatch, and the yard](#4-reorder-dispatch-and-the-yard) — what is new in this experiment
- [5. Unload and put away](#5-unload-and-put-away) — the dock rule, then the placement rule
- [Invariants vs experiment terms](#invariants-vs-experiment-terms)

All the equations referenced below are collected on the
[Formula reference](formula-reference.md) page.

!!! note "Why a synthetic site, and what that buys"
    The catalogue is synthetic and the pick-time model is stated, not fitted to a particular real
    building — deliberately. A real site cannot run the same forty days eleven times; the
    simulation replays the **identical** order stream and the identical trailer arrivals under
    every dock rule, so a gap between two cells is attributable to the one decision that changed,
    with no seasonality, staffing, or demand noise in the way. That control is the product; the
    price is that absolute hours and trailer-days are model units. The direction and ranking of
    results is the transferable part — and on this run the finding is that the ranking barely
    exists, which is itself the transferable part: a mechanism argument, stated on the
    [results page](index.md), about which sites could see one.

## Lifecycle at a glance

A run is one synthetic inventory, stocked once, then picked over forty site days. What is new
against every earlier experiment is the loop on the right: replenishment no longer appears on
the shelf when its lead time ends — it arrives on a **trailer**, waits in a **yard**, is
**unloaded** at one of four **doors** by a receiving crew, and only then is put away.

```text
                    ┌──────────────────────────── repeat × 40 site days ────────────────────────────┐
                    │                                                                               │
  generate ─► stock ─► release wave ─► pick / deplete ─► check_reorders ─► DISPATCH a trailer      │
   catalogue  (opt|uni)  (demand +      (t_pick per        │                (lead ≈ one day,         │
                          affinity)      task, W)          │                 wide spread)            │
                                                           │                      ▼                  │
                                                           │                   YARD (standing        │
                                                           │                   trailers)             │
                                                           │                      ▼                  │
                                                           │        ◄── the cell's DOCK RULE picks   │
                                                           │            the next trailer for a free  │
                                                           │            door; the receiving crew     │
                                                           │            empties it                   │
                                                           │                      ▼                  │
                                                           └──── put-away via the placement rule ────┘
```

Only the **dock rule** differs between cells. Picking, demand, the trailers' contents and their
arrival times, the crews, and the placement rule are identical in every cell, so any gap between
cells is attributable to the order the dock worked the yard in.

**Coupled, not two warehouses.** Earlier experiments ran the store and fulfillment channels as
two independent buildings over one catalogue. Here they are one **site**: one dock, one yard,
one receiving crew, one pool of putters serve both channels, and a trailer can carry both
channels' stock. Each channel still picks its own disjoint racking with its own crew and its own
order stream — the pick side is as separate as before; the inbound side is shared. (`k1_off_inb_off`
is the exception on purpose: no yard at all, the pre-dock model, kept as the pole every dock rule
is measured from.)

## 1. Generation

The synthetic SKU catalogue is produced once by
[`Warehouse/generation/generate_inventory.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/generation/generate_inventory.py)
and snapshotted to `params.json`. Each SKU gets size, weight, handling, a per-batch demand,
and — derived from that demand under the run's stock declaration — an order-up-to quantity and a
reorder point:

!!! abstract "Equilibrium / reorder model"
    {{ reorder_formula(inv0) }}

    The full creation plan — shares, dimension, weight, handling, and demand distributions —
    is on the [Inventory distributions](inventory.md) page, generated from the same snapshot.

### What this sweep varied, and what it held still { #held-fixed }

Derived from the run's own tree rather than written down: a factor counts as *varied* when
more than one value of it appears in this run, and *fixed* when exactly one does — so the
fixed list cannot be quietly incomplete. The third category is the one worth reading: knobs a
reader will ask about that are **not parameters of this model at all**.

{{ held_fixed_table() }}

### The inventory model, as the run actually stocked it { #the-inventory-model }

{{ inventory_model(inv0) }}

**Stock is declared, not sampled.** Every SKU's order-up-to quantity and reorder point are
written together at setup by one method and recorded in the run's own planned inventory; the
warehouse's bin count is derived from those levels. Under this era both channels sit on a
**line floor**: a SKU holds about one of its own mean order lines, and every pick reorders what
it took — replenishment is a trickle in lockstep with picks, not a wave. That declaration is
what makes the yard stand: reorders dispatch continuously, and trailers arrive continuously.

The catalogue lists {{ '{:,}'.format(inv_params(inv0)['num_skus']) }} SKUs. **Bins are
partitioned per channel, SKUs are not counted per channel** — see Experiment 8's
[lifecycle page](../experiment-8/comparison-overview.md#the-inventory-model) for the precision
note, which holds unchanged.

This experiment's inventory:
{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — lead time {{ inv_lead_time(key) }} between order and trailer dispatch;
  the trailer's own transit is the arrival lead in §4.
{% endfor %}

## 2. Stock (initial layout)

Before day 1 the whole declared inventory is stocked once, into bins grouped by
`BinKey = (handling, category, storage_size, unit_type)`. Two initial layouts bracket the
starting point ([strategy_runner.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/simdriver/strategy_runner.py),
the `stock_mode` branch):

- **`uni`** — uniform-random placement (a deliberately poor start).
- **`opt`** — **policy-stocked**: the whole inventory is placed through the arm's own placement
  rule, so each arm starts at its own ideal layout.

Both starts run in every cell, for both placement pairs — six arms per channel per cell. The
initial stock is placed at setup, **not through the yard**: the dock only ever sees replenishment.
(The successor study inverts exactly this, filling an empty warehouse through the yard.)

The headline setup for the reference cell's store channel:

{{ setup_table(inv0) }}

<small>**The table above is the shared build and the STORE channel's crew.** Fulfillment runs
its own disjoint partition of the same racking with its own crew — **23 pickers** against the
store's **31** — sized by the simulator from each channel's declared demand and its first-time
completion target, not chosen. The two channels also differ in three pick-time constants
(`pick_intercept`, `cart_swap_coef`, `batch_mean_frac`), whose per-channel values are in each
leaf's committed `config.json` under `images/`: the store samples a wave at **0.245 %** of its
SKU pool with a 15 s pick intercept and a 300 s cart swap; fulfillment at **1.81 %** with 10 s and
240 s. The wave fractions are an order of magnitude below Experiment 8's because the era
derives them from the declared demand rather than authoring them.</small>

## 3. Pick

**Batch → tasks.** Each site day releases one wave per channel: a set of SKUs sampled by demand
and by affinity for co-picked partners, grouped into **tasks, one per aisle** — the ordered sweep
through the bins a picker visits in a single aisle. The tasks are handed to the channel's pickers
longest-first (the `lpt` scheduler, Experiment 8's winner, fixed in every cell). At most one
picker works an aisle per wave. A pick the day cut short, or that found an empty shelf, is
**re-offered** the next day — nothing is lost, and the day's labour counts what it did.

**Per stop**, the pick-time cost is the model below (this experiment's `store` config; the full
form and both channels' calibrations are on the [Formula reference](formula-reference.md#pick-time)):

{{ pick_time_formula(inv0) }}

Clearing an aisle costs the realised **task labor** $W = H + T + C$ — handling, travel, cart
swaps — defined on the [Formula reference](formula-reference.md#task-labor). Summed over a wave's
tasks, $W$ is the hands-on labour every labour figure on this site is stated in.

Picking a SKU decrements its on-hand quantity; once its inventory position falls to the reorder
point, it is flagged for replenishment — which, on the line floor, is after nearly every pick.

## 4. Reorder, dispatch, and the yard

Once per day, `check_reorders`
([Warehouse/inventory/inventory_reorder.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/inventory/inventory_reorder.py))
issues an order-up-to quantity for every flagged SKU. Under the inbound era the order does not
enter a shelf-side queue; it is **packed** onto a trailer under the packing the warehouse was
sized from, and the trailer is **dispatched**:

```text
per day:   flagged SKUs ─► pack into loads ─► dispatch trailer(s)
           each trailer's transit lead ~ lognormal(median 480 min = one 8-hour working day,
                                                     sigma 0.7)   — a WORLD fact, identical in every arm
           on arrival: the trailer joins the YARD and waits for a door
```

The **yard** is where the experiment lives. Four doors; a trailer that arrives to a free door
is staged at once, otherwise it stands. Each working day the dock runs one **drain**: while a
door is free and trailers stand, the **cell's rule** picks which trailer takes it, and the
receiving crew empties it at the door. Time a trailer spends from arrival past the **free
threshold** (0.40 days) is **overage**, the physical basis of a carrier's detention bill. The
crew's day can end with trailers still staged — a **binding cut** — and a drain can start with
trailers standing and no free door — **contention**. The [yard page](comparison.md) reads these
off the run's figures.

<small>The lead law and door count are the run's own declarations in
[`data/held_fixed.json`](data/held_fixed.json); the free threshold each cell's overage was
folded against (0.40 days, set per cell by the phase-2 axis) is recorded per cell in
[`data/unload_ranking.json`](data/unload_ranking.json) (`metric.tie_break.threshold_days`) --
the 2.0 days the factor register prints is the run-level default those cells override. **On
the receiving crew:** the register's `recv_crew_size: 0` means *no authored crew*; under this
era the receiving crew is derived at setup from the declared demand, like the pickers, and
`inbound_door_team: 10` caps how many receivers work one trailer. The derived crews, copied
from the run's staffing record into the ranking artifact (`run.site_crews` in
[`data/unload_ranking.json`](data/unload_ranking.json)): a **receiving crew of 23** across the
four doors and a **put-away crew of 64**, both on an eight-hour day, both serving the two
channels. *What "derived from demand" targets:* every crew is sized so that a declared
first-time completion confidence holds — a pick is reached on the day it was released and
filled from the shelf it was sent to at a stated expected share, and the receiving and put
crews are sized so the yard and the put queue clear at a stated utilisation under the
declared arrival rate (the site's decision record ADR-0004: demand declared, crew derived).
The crew's realised utilisation and busy share are read off the yard scorecard figures.</small>

## 5. Unload and put away

**The dock rule** is the only thing the eleven cells differ on. `fifo` takes the longest-standing
trailer. The **gain** rules score each standing trailer by running the site's placement rule
*virtually* on its contents against the empty slots — what its stock would cost the pickers if it
landed now, against what the other trailers would leave standing — and unload the trailer with
the largest gain first; the gated variants force any trailer past a fraction of the free
threshold to the front in arrival order; the foresight variants let the gain read orders not yet
released. The [formula reference](formula-reference.md#how-a-gain-rule-prices-a-trailer) states
the arithmetic; the [results page](index.md#the-eleven-rules-in-plain-terms) the plain-terms
table.

**Put-away** then places the emptied trailer's units through the cell's placement pair, exactly
as Experiment 8's restock step did: `{{ experiment().baseline }}` drops a unit into a
uniform-random free bin; `rank_cartlabor` (store) and `rank_minlabor` (fulfillment) rank free
bins by the labour primitive and demand. Put-away is timed — a put crew, a put-away walk and
per-item handling — and its labour is recorded beside the pick labour, unlike in every earlier
experiment. All the families are catalogued on the
[Formula reference](formula-reference.md#the-families); this run swept only the pair named
above and the rider.

## Invariants vs experiment terms

**Invariants** — identical across every cell:

| Held constant | Value | Source |
|---------------|-------|--------|
| Catalogue seed | {{ inv_params(inv0)['seed'] }} | `params.json` |
| SKUs (catalogue) | {{ '{:,}'.format(inv_params(inv0)['num_skus']) }} | `params.json` |
| Site days / crews | 40; store 31 pickers, fulfillment 23, receiving derived | `config.json`, [`data/held_fixed.json`](data/held_fixed.json) |
| Dock | 4 doors, free threshold 0.40 d, transit lead median 480 min | run layout, [`data/held_fixed.json`](data/held_fixed.json) |
| Placement pair and scheduler | `rank_cartlabor` / `rank_minlabor` + the FIFO rider; `lpt` | the run spec |
| Warehouse geometry, pick-time model | identical across cells | `config.json` |

**Experiment terms** — what this comparison sweeps:

| Varied | Levels |
|--------|--------|
| Dock rule (the cell) | 10 yard rules + the no-yard pole — 11 cells |
| Starting layout | `uni`, `opt` — both, in every cell |
| Placement pair | the winner pair and the rider — both, in every cell, the rider as a control |

See the [Results](index.md) page for what the dock rule moved, and the
[full-results page](full-results.md) for every cell.
