# Full results

The complete strategy suite — every one of the 16 [assignment functions](formula-reference.md)
under both initial layouts — for each run, laid out as a **pick-config × inventory-lead**
matrix. The per-run [write-ups](index.md) stay focused on the top-3 winners; this page is the
drill-down for readers who want the whole picture in one place.

!!! note "Notes"
    Read this page as the audit trail for the two write-ups. The
    [comparison pages](comparison-20260624.md) quote only the podium; the value of the full matrix
    is the **shape of the whole distribution** — how far the losing families fall, and how tightly
    the winners cluster. A 3-point spread across the top three arms and a 20-point spread across the
    suite are very different claims about how much placement matters, and only this page shows
    which one is true.

## The headline: most strategies don't beat FIFO

Across the suite, **only `rank_labor`, `map`, and `map_rank` beat the FIFO (first-in-first-out)
baseline on cumulative/total task time by a meaningful margin** — ≈ −1 to −4 % at the base
calibration, widening to ≈ −9 % under the steepest ergonomic penalties. Everything else clusters
near FIFO or lands *worse*:

- The **bracket controls are designed to lose** — `tmax` (travel), `cmin` (affinity), `expn`
  (co-demand), and `rank_maxlabor` deliberately place badly to bound each lever.
- Several genuine "optimizations" (e.g. cohesion- or co-demand-only families) still fail to
  beat FIFO on *cumulative* task time: a placement that helps one proxy can raise another. An
  optimization pointed at the wrong objective makes total task time **worse**, not better.

Read the box plots as *steady-state task duration per arm* (lower = better; the FIFO arms are
the reference) and the overlays as *production time per batch* (Opt = solid, Uni = dashed).

!!! tip "The winning *set* is stable; which family leads is not"
    Across the four pick-time calibrations (`calibrated`, `high_weight`, `high_height`,
    `high_weight_high_height`) and the two lead-time variants (`lt0`, `ltrand0-5`), the podium is
    drawn from the same three families every time — `rank_labor` / `map` / `map_rank` — and the
    bracket controls stay at the extremes. The collapsibles below are provided for inspection;
    expect them to look alike rather than to tell four different stories.

    **Which of the three leads does move, though.** On the 2026-06-24 run the `map` family takes
    first place in all four `ltrand0-5` calibrations, displacing `rank_labor`; on the 2026-06-27 run
    `rank_labor` leads everywhere. What is stable is the *membership* of the top three, not the
    order — see the two write-ups for the per-calibration numbers.

    What *does* shift monotonically is the **size** of the margin: steeper ergonomic penalties mean
    a larger win, from ≈ −1.2 % at the mildest to ≈ −9.5 % at the steepest.

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
