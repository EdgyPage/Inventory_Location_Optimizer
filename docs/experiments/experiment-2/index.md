# Experiment 2 — overview

The second placement-strategy sweep. Where [Experiment 1](../experiment-1/index.md) ran a single
generic warehouse, Experiment 2 splits the operation into **two independent warehouses** — a
**store** replenishment channel and a **fulfillment** channel — and asks the same question of
each: *once restock arrives, does where you place it matter, and which assignment function wins?*
Both channels are stocked from **one** shared seed-42 catalogue
(`mixed_realistic`, 130,000 SKUs) and picked over 100 batches, and every restock rule is compared
against a first-in-first-out (FIFO) baseline. This page is the experiment's hub; everything below
belongs to Experiment 2 and reflects *this* sweep's setup.

!!! abstract "Headline"
    Two warehouses, two different champions. On total task time versus FIFO, the **store**
    warehouse is won by **Rank_labor** (≈ **−3.7%**) and the **fulfillment** warehouse by
    **Compact** (≈ **−3.4%** at base calibration). See the **[Highlights](highlights.md)** for
    the winning-function mathematics and the per-channel evidence.

## What's inside

- **[Simulation lifecycle & method](comparison-overview.md)** — how a run works end-to-end
  (generation → stock → pick → reorder → restock), the two channel cost models, and what this
  experiment holds constant vs. varies.
- **[Inventory baselines](inventory.md)** — the `mixed_realistic` catalogue as it was actually
  used: weight, volume, and relative-frequency distributions, plus the category creation plan and
  the equilibrium/reorder model.
- **[Formula reference](formula-reference.md)** — the full catalogue of the 17 placement families
  compared here, and the per-channel pick-time cost models.
- **[Highlights](highlights.md)** — the best warehouse for stores and for fulfillment, the winning
  assignment functions shown in mathematics, the productivity-change graphs, and the
  percent-versus-FIFO tables.
- **[Everything else](everything-else.md)** — the competition of **all** assignment functions
  (not just the top performers): the full-suite ranking, the per-function deltas, and the
  bracket controls that are designed to lose.
- **[Glossary](glossary.md)** — terms and symbols used across this experiment.

## The two warehouses

Each channel is its own warehouse with its own cart, crew, travel speeds, and pick-time cost
model; they share only the catalogue and the warehouse geometry. They are simulated and analysed
**independently** — a strategy that wins one channel is not assumed to win the other.

| Channel | Cart | Pickers | Speeds (cross / along, ft·s⁻¹) | Handling cost | Restock families run |
|---------|------|--------:|-------------------------------|---------------|----------------------|
| **Store** | StoreCart | 25 | 3 / 2 | weight $0.58\,w^{1.5}$ + volume $0.7\log_2 V$ | labor subset (FIFO, Rank_labor, Rank_cartlabor) |
| **Fulfillment** | FulfillmentCart | 20 | 2 / 4 | weight $0.7\log w$ + volume $0.09\log V$ | full 17-family suite |

The store channel only sweeps the labor-family subset it will actually deploy
(`STORE_RESTOCKS`); the fulfillment channel runs the complete assignment-function suite. That
asymmetry is why the **[Everything else](everything-else.md)** competition is anchored on the
fulfillment channel.

## What Experiment 2 sweeps

- **Warehouse channel** — `store` vs `fulfillment`; each is an independent warehouse with its own
  cart, crew, and pick-time model (above).
- **Pick-time calibration** — two per channel: store `store` / `store_high_weight` (a steeper
  $w^{2}$ weight penalty) and fulfillment `ful_calibrated` / `ful_calibrated_fast_walkers` (faster
  cross-aisle walkers). These probe how sensitive the ranking is to the cost model.
- **Inventory lead time** — `lt0` (immediate replenishment) vs. `ltrand0-5` (uniform 0–5 batch
  delay); identical catalogue otherwise.
- **Placement strategy** — initial layout {`uni`, `opt`} × restock family. Held constant: seed 42,
  the 130,000-SKU catalogue, warehouse geometry, and the picking model.

## Run

| Run | Date | Scope | Baseline |
|-----|------|-------|----------|
| `comparison_20260706_174353` | 2026-07-06 | store + fulfillment channels, 2 calibrations each, lt0 + ltrand0-5 | `uni_fifo_norsl` (FIFO) |

!!! note "Summary"
    Space intentionally left blank for user input
