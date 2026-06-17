# Inventory Location Optimizer

Simulation results and analysis for warehouse SKU **placement (assignment) strategies** —
how the choice of where to slot incoming stock affects picker travel, batch duration,
co-location quality, and inventory churn.

This site is where simulation runs are written up and their plots are published. Start with
the [Results overview](results/index.md).

## What's compared

Each run simulates an inventory lifecycle (stock → reorder → pick, repeated over many
batches) under different **restock placement rules**, for example:

- **FIFO** — uniform random placement (baseline).
- **TripMin / TripMax** — minimise / maximise predicted pick-trip cost.
- **MaxClu / MinClu** — cluster high-affinity SKUs together / apart.

…optionally combined with an initial layout (uniform vs. demand-optimal) and a bounded
per-batch re-slotting rule.

## How to add a results write-up

1. Run the analysis locally on a completed simulation:
   ```bash
   python Optimization/run_analysis.py <path-to-run-output>
   ```
2. Copy the plots you want to show into `docs/results/images/`.
3. Add a Markdown page under `docs/results/` (copy
   [`example-run.md`](results/example-run.md) as a starting point) and list it under `nav:`
   in `mkdocs.yml`.
4. `git push` — the **Deploy docs** GitHub Action rebuilds and republishes the site.

!!! note "Preview locally before pushing"
    ```bash
    pip install -r requirements-docs.txt
    mkdocs serve          # open http://127.0.0.1:8000
    ```
