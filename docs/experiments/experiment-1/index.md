# Experiment 1 — overview

The first placement-strategy sweep. One synthetic catalogue (**`mixed_realistic`** — 100,000
SKUs, six product categories) is stocked into a warehouse and picked over 100 batches, and the
full suite of restock-placement rules is compared against a first-in-first-out (FIFO) baseline.
This page is the experiment's hub; everything below — its definitions, glossary, and results —
belongs to Experiment 1 and reflects *this* sweep's setup.

## What's inside

- **[Simulation lifecycle & method](comparison-overview.md)** — how a run works end-to-end
  (generation → stock → pick → reorder → restock), the pick-time cost model, the top-3
  assignment-function equations, and what this experiment holds constant vs. varies.
- **[Inventory distributions](inventory.md)** — the `mixed_realistic` catalogue: category
  shares, demand, and the equilibrium/reorder model.
- **[Assignment functions](assignment-functions.md)** — the full catalogue of the 16 placement
  families compared here.
- **Comparison write-ups** — the headline findings, focused on the top-3 winners (below).
- **[Full results](full-results.md)** — every strategy for every run, as a
  pick-config × inventory-lead matrix.
- **[Glossary](glossary.md)** — terms and symbols used across this experiment.

## What Experiment 1 sweeps

- **Inventory lead time** — `lt0` (immediate replenishment) vs. `ltrand0-5` (uniform 0–5 batch
  delay); identical catalogue otherwise.
- **Pick-time calibration** — four cost models (`calibrated`, `high_weight`, `high_height`,
  `high_weight_high_height`) to test how sensitive the ranking is to ergonomic penalties.
- **Placement strategy** — initial layout {`uni`, `opt`} × 16 restock families. Held constant:
  seed 42, 100,000 SKUs, warehouse geometry, and the picking model.

## Runs

Each write-up focuses on the top-3 winners vs. the FIFO baseline; the
[Full results](full-results.md) page shows every strategy for every run.

| Run | Date | Scope | Notes |
|-----|------|-------|-------|
| [Comparison — 2026-06-24](comparison-20260624.md) | 2026-06-24 | lt0 + ltrand0-5, 4 calibrations | Placement strategies vs FIFO. |
| [Comparison — 2026-06-27](comparison-20260627.md) | 2026-06-27 | lt0 + ltrand0-5, 4 calibrations | Placement strategies vs FIFO; adds randomised lead time. |

<!-- Add a row per run above, in run order (oldest first). -->
