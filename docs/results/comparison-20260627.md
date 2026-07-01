# Comparison — 2026-06-27

Placement-strategy comparison across four pick-time calibrations, run against both
inventory variants (`lt0` and `ltrand0-5`) with a cross-inventory aggregate.

!!! note "Summary"
    Write the headline takeaway here — which initial-placement strategy won on cumulative
    production time, by how much versus FIFO, and whether the advantage held once
    replenishment lead time was randomised (`ltrand0-5`). Use **bold** for the headline
    numbers.

## Setup

Warehouse and pick-time model are identical across both inventory variants; they differ
only in replenishment lead time — see [Inventory distributions](../inventory/index.md).

**Pick-time cost model** (`calibrated` configuration):

{{ pick_time_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

The `high_weight` / `high_height` configurations re-run the same catalogue with steeper
weight and shelf-height ergonomic penalties, to test how sensitive the strategy ranking is
to the cost model.

## lt0 — immediate replenishment

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

## ltrand0-5 — lead time 0–5 batches

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}

## Discussion

Interpretation, comparison against the [2026-06-24 run](comparison-20260624.md), and next
steps go here.
