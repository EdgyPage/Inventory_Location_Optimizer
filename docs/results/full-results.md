# Full results

The complete strategy suite — every one of the 16 [assignment functions](assignment-functions.md)
under both initial layouts — for each run, laid out as a **pick-config × inventory-lead**
matrix. The per-run [write-ups](index.md) stay focused on the top-3 winners; this page is the
drill-down for readers who want the whole picture in one place.

!!! note "Notes"
    <!-- paste commentary here -->

!!! info "Render check"
    This text comes from `docs/results/full-results.md`. **Sentinel:** `FULL-RESULTS-LIVE-v1`.

## The headline: most strategies don't beat FIFO

Across the suite, **only `rank_labor`, `map`, and `map_rank` beat the FIFO (first-in-first-out)
baseline on cumulative/total task time by a meaningful margin** (≈ −3–4%). Everything else
clusters near FIFO or lands *worse*:

- The **bracket controls are designed to lose** — `tmax` (travel), `cmin` (affinity), `expn`
  (co-demand), and `rank_maxlabor` deliberately place badly to bound each lever.
- Several genuine "optimizations" (e.g. cohesion- or co-demand-only families) still fail to
  beat FIFO on *cumulative* task time: a placement that helps one proxy can raise another. An
  optimization pointed at the wrong objective makes total task time **worse**, not better.

Read the box plots as *steady-state task duration per arm* (lower = better; the FIFO arms are
the reference) and the overlays as *production time per batch* (Opt = solid, Uni = dashed).

!!! tip "The ranking is stable across calibrations and lead time"
    The four pick-time calibrations (`calibrated`, `high_weight`, `high_height`,
    `high_weight_high_height`) and the two lead-time variants (`lt0`, `ltrand0-5`) **do not
    reorder the winners** — `rank_labor` / `map` / `map_rank` stay on top and the brackets stay
    at the extremes. The collapsibles below are provided for inspection; expect them to look
    alike rather than to tell four different stories.

## Run — 2026-06-24

### lt0 — immediate replenishment

{{ full_suite_section('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_lt0') }}

### ltrand0-5 — lead time 0–5 batches

{{ full_suite_section('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}

## Run — 2026-06-27

### lt0 — immediate replenishment

{{ full_suite_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

### ltrand0-5 — lead time 0–5 batches

{{ full_suite_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}
