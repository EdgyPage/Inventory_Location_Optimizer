# Results

Write-ups of individual simulation runs. Each entry records the run parameters, the
headline numbers, and the analysis plots.

New here? Start with the [Simulation lifecycle &amp; method](comparison-overview.md) page —
it walks the generation → stock → pick → reorder → restock loop, shows the top-3
assignment-function equations, and lists what each run holds constant vs varies. The full
catalogue of placement rules is on [Assignment functions](assignment-functions.md); the whole
strategy suite across every run is on [Full results](full-results.md). Symbol definitions live
in the [Glossary](glossary.md).

!!! info "Render check"
    This text comes from `docs/results/index.md`. **Sentinel:** `RESULTS-INDEX-LIVE-v1`.

## Runs

Each write-up focuses on the top-3 winners vs the FIFO (first-in-first-out) baseline; the
[Full results](full-results.md) page shows every strategy for every run.

| Run | Date | Scope | Notes |
|-----|------|-------|-------|
| [Comparison — 2026-06-24](comparison-20260624.md) | 2026-06-24 | lt0 + ltrand0-5, 4 calibrations | Placement strategies vs FIFO. |
| [Comparison — 2026-06-27](comparison-20260627.md) | 2026-06-27 | lt0 + ltrand0-5, 4 calibrations | Placement strategies vs FIFO; adds randomised lead time. |

<!-- Add a row per run above, in run order (oldest first). -->

## Conventions

- **Plots** live in `docs/results/images/`. Reference them with relative paths, e.g.
  `![Batch duration](images/grid_batch_duration.png)`.
- Keep only the curated plots you want to show — the raw SQLite run DBs are large and are
  **not** committed (they are covered by `.gitignore`).
- Click any image to zoom (lightbox).
