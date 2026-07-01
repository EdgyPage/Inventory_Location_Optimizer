# Inventory distributions

Every simulation run is populated from a synthetic SKU catalogue. The runs written up
here all use the **`mixed_realistic`** catalogue: 100,000 SKUs drawn from six product
categories, each with its own size, weight, handling, and demand distributions. The
tables below are generated directly from the committed `params.json` snapshots, so they
always match what the simulation actually used.

!!! note "The two variants differ only in replenishment lead time"
    `mixed_realistic_lt0` uses **{{ inv_lead_time('mixed_realistic_lt0') }}** replenishment,
    while `mixed_realistic_ltrand0-5` uses **{{ inv_lead_time('mixed_realistic_ltrand0-5') }}**.
    Everything else — seed 42, 100,000 SKUs, the six-category creation plan below,
    supply-CV ceiling, and the 10-batch equilibrium coverage — is **identical** between
    them. Any performance difference between the variants is therefore attributable to
    lead-time variability alone.

## Category creation plan

Shares, dimension distributions (inches), weight model, conveyable/non-conveyable
handling split, and per-SKU order **freq**uency and **qty** ranges:

{{ inv_distribution_table('mixed_realistic_lt0') }}

**Reading the specs:** `tri(a–b, mode m)` is a triangular distribution; `norm(μ, σ)` a
normal; `U(a–b)` a uniform draw; `mix(p·… + q·…)` a probabilistic mixture of components.
Weights are Poisson-distributed and scaled by item volume (`∝ volume`), optionally with a
category multiplier, except chemicals which use a fixed rate.

## How the catalogue is generated

The catalogue and its steady-state stock levels are produced by
[`Warehouse/generation/generate_inventory.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/generation/generate_inventory.py).
Each SKU is assigned an equilibrium quantity and a reorder point from its expected demand:

!!! abstract "Equilibrium / reorder model"
    {{ reorder_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5', 'calibrated') }}

    The warehouse is sized so every SKU can hold its equilibrium quantity; the reorder
    point triggers replenishment `lead + safety` batches ahead of stock-out. In the
    `lt0` variant orders arrive immediately; in `ltrand0-5` they arrive after a uniform
    0–5 batch delay, so stock can dip below the reorder point before it is refilled.

See the [results write-ups](../results/index.md) for how each strategy performs on these
catalogues.
