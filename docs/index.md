# Inventory Location Optimizer

Simulation results and analysis for warehouse SKU (stock-keeping unit)
**placement (assignment) strategies** — how the choice of where to slot incoming stock affects
picker travel, batch duration, co-location quality, and inventory churn.

This site is where simulation runs are written up and their plots are published. Start with
the [Results overview](results/index.md).

!!! success "It's working"
    You're reading content rendered from `docs/index.md`. If this page looks styled and you
    can navigate the sidebar, the MkDocs build and GitHub Pages deploy are working
    end-to-end. **Sentinel:** `HOME-PAGE-LIVE-v1` — change this string and push to confirm a
    redeploy picked up your edit.

## What's compared

Each run simulates an inventory lifecycle (stock → reorder → pick, repeated over many
batches) under different **restock placement rules**, for example:

- **FIFO** (first-in-first-out) — uniform random placement (baseline).
- **TripMin / TripMax** — minimise / maximise predicted pick-trip cost.
- **MaxClu / MinClu** — cluster high-affinity SKUs together / apart.

…optionally combined with an initial layout (`uni` uniform-random vs. `opt` policy-stocked —
each strategy's own ideal) and a bounded per-batch re-slotting rule.

## How to add a results write-up

The maintainer workflow (generate → analyse → publish) lives in the repo at
`docs/authoring.md`, with `docs/results/example-run.md` as a page template. In short: run
`python Optimization/run_analysis.py <run-dir>`, copy the plots you want into
`docs/results/images/`, add a page under `docs/results/`, list it under `nav:` in
`mkdocs.yml`, and `git push` — the **Deploy docs** GitHub Action republishes the site.
