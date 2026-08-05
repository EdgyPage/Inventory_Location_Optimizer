# Comparison — 2026-06-27

Placement-strategy comparison across four pick-time calibrations, run against both
inventory variants (`lt0` and `ltrand0-5`) with a cross-inventory aggregate.

!!! note "Summary"
    **`Rank_labor` wins both inventory variants here, and the advantage survives randomised lead
    time.** At base ergonomics it takes **−3.9 %** total task time on `lt0` and **−3.4 %** on
    `ltrand0-5`; under the steepest calibration those become **−8.1 %** and **−9.5 %** — the largest
    placement margins anywhere in Experiment 1. All significant at p < .001 (Wilcoxon, p ≈ 4e-18).
    <br><br>
    That is the notable result: the lead-time delay costs `Rank_labor` almost nothing here
    (−3.9 % → −3.4 %), whereas in the [2026-06-24 run](comparison-20260624.md) it lost the top slot
    to the cluster-map family entirely. **The flip was not a property of randomised lead time on its
    own.**

## Setup

Warehouse and pick-time model are identical across both inventory variants; they differ
only in replenishment lead time — see [Inventory distributions](inventory.md).

**Pick-time cost model** (`calibrated` configuration):

{{ pick_time_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

The four calibrations re-run the same catalogue with steeper weight and shelf-height ergonomic
penalties, to test how sensitive the strategy ranking is to the cost model — the exact
per-calibration coefficients used in this run:

{{ pick_calibration_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

**Top-3 assignment functions** (the winning restock rules; full catalogue and symbols on the
[Formula reference](formula-reference.md)):

{{ assignment_formulas() }}

See the [simulation lifecycle](comparison-overview.md) for how these fit into the
generation → stock → pick → reorder → restock loop, and [Full results](full-results.md) for
every strategy arm (not just the top-3).

!!! warning "Not a controlled A/B against the 2026-06-24 run"
    This run is **much larger**. On the same catalogue and the same `calibrated` cost model, the
    FIFO baseline is ~7.57 M sim units here against ~2.98 M on
    [2026-06-24](comparison-20260624.md) — and under the steepest calibration it reaches ~46.1 M
    against ~4.36 M. The percentages below are therefore comparable *within* this page, but the two
    runs differ in more than one variable and their absolute margins should not be diffed.

!!! note "Notes"
    The ergonomic penalties bite far harder in this run: steepening both weight and height
    multiplies the FIFO baseline by ~6× (7.57 M → 46.1 M), where the same change on 2026-06-24
    multiplied it by only ~1.5×. That is what makes the placement margins here roughly double —
    there is simply more avoidable cost in the baseline to recover.

## lt0 — immediate replenishment

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

!!! note "Notes"
    Quoted from the two `top_vs_baseline_table.png` endpoints (the two intermediate calibrations
    are in the collapsible blocks above and on [Full results](full-results.md)):

    | Calibration | Top 3 | vs FIFO | FIFO total task time |
    |---|---|---:|---:|
    | `calibrated` | `Rank_labor`, `Rank_labor`, `Map` | −3.9 %, −3.8 %, −3.0 % | ~7,569,190 |
    | `calibrated_high_weight_high_height` | `Rank_labor`, `Rank_labor`, `Map` | −8.1 %, −7.9 %, −6.7 % | ~46,055,956 |

    The podium composition is stable across the range — two `Rank_labor` arms (the `uni` and `opt`
    initial layouts) then a `Map` variant — and the two `Rank_labor` arms sit within 0.2 pp of each
    other at both ends. The **restock rule is doing the work; the initial layout is not.**

## ltrand0-5 — lead time 0–5 batches

{{ setup_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5', 'calibrated') }}

{{ run_section('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}

!!! note "Notes"
    | Calibration | Top 3 | vs FIFO | FIFO total task time |
    |---|---|---:|---:|
    | `calibrated` | `Rank_labor`, `Rank_labor`, `Map` | −3.4 %, −3.2 %, −3.0 % | ~6,401,700 |
    | `calibrated_high_weight_high_height` | `Rank_labor`, `Rank_labor`, `Rank_minlabor` | −9.5 %, −9.1 %, −8.3 % | ~38,881,151 |

    Randomised lead time costs `Rank_labor` about **0.5 pp** at base ergonomics and *nothing* at the
    steepest — where the `ltrand0-5` margin (−9.5 %) actually exceeds the `lt0` one (−8.1 %). At
    that end the whole podium turns labour-ranking: `Rank_minlabor` displaces `Map` for third.
    <br><br>
    Note the baseline moves the other way: `ltrand0-5` has a *lower* FIFO total task time than `lt0`
    at both calibrations. A delay between reorder and restock is not simply a penalty on the
    warehouse — it changes which slots are free when stock lands, and here that is mildly
    favourable.

## Discussion

Read together, the two runs of Experiment 1 say something more useful than either says alone.

**What replicates.** `Rank_labor` is the strongest single restock rule in six of the eight
(inventory × calibration) cells across both runs; the podium is consistently two `Rank_labor` arms
plus a `Map` variant; the initial layout (`uni` vs `opt`) is worth a fraction of a percentage point
against the restock rule; and in every cell the margin grows as the ergonomic penalties steepen.

**What does not.** The 2026-06-24 run lost `Rank_labor`'s top slot to the cluster-map family under
randomised lead time. This run does not reproduce that. Since the two runs also differ in scale,
the flip cannot be attributed to lead time alone — it is an artefact of that run's particular
configuration, and the honest conclusion is that **the effect is not established.**

**The ceiling.** −9.5 % is the largest placement margin in Experiment 1, and it needs the most
punitive cost model to get there. At the base calibration the answer stays ≈ −3 to −4 %. That
bounded, cost-model-dependent prize is what motivates the rest of the site: if placement is worth
single-digit percentages, what else is on the table? [Experiment 2](../experiment-2/index.md)
splits the catalogue into two independent warehouses;
[Experiments 4](../experiment-4/index.md)–[6](../experiment-6/index.md) move the layout and the
picker scheduler instead — and find a *larger* lever there than placement ever offered.

The full strategy suite for this run is on [Full results](full-results.md).

!!! note "Notes"
    A caveat that applies to every number on this page: total task time is **modeled** pick-time —
    the cost model's own output summed over tasks, not a wall-clock schedule and not a staffing
    estimate. It measures how much work the layout creates. How fast that work *clears* is a
    separate question, and one this experiment never asks: every arm here uses a single
    round-robin picker assignment. [Experiment 6](../experiment-6/index.md) separates the two
    explicitly.
