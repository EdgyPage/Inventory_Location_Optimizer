# Comparison — 2026-06-27

Placement-strategy comparison across four pick-time calibrations, run against both
inventory variants (`lt0` and `ltrand0-5`) with a cross-inventory aggregate.

!!! note "Summary"
    Write the headline takeaway here — which initial-placement strategy won on cumulative
    production time, by how much versus FIFO (first-in-first-out), and whether the advantage held once
    replenishment lead time was randomised (`ltrand0-5`). Use **bold** for the headline
    numbers.

## Setup

Warehouse and pick-time model are identical across both inventory variants; they differ
only in replenishment lead time — see [Inventory distributions](inventory.md).

**Pick-time cost model** (`calibrated` configuration):

{{ pick_time_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

The `high_weight` / `high_height` configurations re-run the same catalogue with steeper
weight and shelf-height ergonomic penalties, to test how sensitive the strategy ranking is
to the cost model.

**Top-3 assignment functions** (the winning restock rules; symbols in the
[Glossary](glossary.md)):

{{ assignment_formulas() }}

See the [simulation lifecycle](comparison-overview.md) page for how these fit into the
generation → stock → pick → reorder → restock loop, and [Full results](full-results.md) for
every strategy arm (not just the top-3).

!!! note "Notes"
    <!-- paste commentary here -->

## lt0 — immediate replenishment

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

!!! note "Notes"
    <!-- paste commentary here -->

## ltrand0-5 — lead time 0–5 batches

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}

!!! note "Notes"
    <!-- paste commentary here -->

## Discussion

Interpretation, comparison against the [2026-06-24 run](comparison-20260624.md), and next
steps go here. The full strategy suite for this run is on [Full results](full-results.md).

!!! note "Notes"
    <!-- paste commentary here -->
