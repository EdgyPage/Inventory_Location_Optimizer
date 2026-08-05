# Comparison — 2026-06-24

Placement-strategy comparison across four pick-time calibrations, run against both
inventory variants (`lt0` and `ltrand0-5`).

!!! note "Summary"
    **Placement beats FIFO everywhere, but the size of the win is set by the cost model, and the
    winning family is set by the lead time.** Under immediate replenishment (`lt0`), `Rank_labor`
    wins all four calibrations, improving from **−1.7 %** total task time at base ergonomics to
    **−3.7 %** when both the weight and shelf-height penalties are steepened. Under randomised
    lead time (`ltrand0-5`) the **cluster-map family takes over** — `Map` / `Map_rank` win all four
    calibrations at **−1.2 % to −3.0 %**. Every result is significant at p < .001 (Wilcoxon,
    p ≈ 4e-18).
    <br><br>
    The pattern behind both rows: **the harder a pick is, the more a good slot is worth.** The FIFO
    baseline itself grows from ~2.98 M to ~4.36 M sim units as the penalties steepen, and the
    achievable saving grows with it.

## Setup

Warehouse and pick-time model are identical across both inventory variants; they differ
only in replenishment lead time — see [Inventory distributions](inventory.md).

**Pick-time cost model** (`calibrated` configuration):

{{ pick_time_formula('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

The four calibrations re-run the same catalogue with steeper weight and shelf-height ergonomic
penalties, to test how sensitive the strategy ranking is to the cost model — the exact
per-calibration coefficients used in this run:

{{ pick_calibration_table('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_lt0') }}

**Top-3 assignment functions** (the winning restock rules; full catalogue and symbols on the
[Formula reference](formula-reference.md)):

{{ assignment_formulas() }}

See the [simulation lifecycle](comparison-overview.md) for how these fit into the
generation → stock → pick → reorder → restock loop, and [Full results](full-results.md) for
every strategy arm (not just the top-3).

!!! note "Notes"
    The four calibrations are not four different warehouses — they are the *same* run scored
    through four cost models. So the FIFO baseline moves between them, and a percentage is only
    comparable **within** a calibration, never across. Each figure below states the baseline it was
    measured against in its own title.

## lt0 — immediate replenishment

{{ setup_table('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

{{ run_section('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_lt0') }}

!!! note "Notes"
    `Rank_labor` wins every calibration here, and its margin tracks the ergonomic penalty almost
    monotonically — quoted from each config's `top_vs_baseline_table.png`:

    | Calibration | Winner | vs FIFO | FIFO total task time |
    |---|---|---:|---:|
    | `calibrated` | `Rank_labor` | −1.7 % | ~2,984,877 |
    | `calibrated_high_weight` | `Rank_labor` | −2.0 % | ~3,810,639 |
    | `calibrated_high_height` | `Rank_labor` | −3.0 % | ~3,359,601 |
    | `calibrated_high_weight_high_height` | `Rank_labor` | −3.7 % | ~4,357,889 |

    Two things worth noticing. **Height costs more than weight**: the height penalty alone moves the
    win from −1.7 % to −3.0 %, while the weight penalty alone reaches only −2.0 %, even though the
    weight model raises the baseline *further* (3.81 M vs 3.36 M). And the top three arms are
    always within ~0.4 pp of each other — `Rank_labor` twice (the `uni` and `opt` initial layouts)
    plus a `Map` variant — so the **initial layout barely matters** next to the restock rule.

## ltrand0-5 — lead time 0–5 batches

{{ setup_table('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_ltrand0-5', 'calibrated') }}

{{ run_section('comparison_20260624_084609', 'mixed_20260624_083549__mixed_realistic_ltrand0-5') }}

!!! note "Notes"
    **The winner changes.** With replenishment delayed by 0–5 batches, the cluster-map family takes
    the top slot in all four calibrations and the margins shrink:

    | Calibration | Winner | vs FIFO | FIFO total task time |
    |---|---|---:|---:|
    | `calibrated` | `Map_rank` | −1.2 % | ~2,532,259 |
    | `calibrated_high_weight` | `Map` | −1.4 % | ~3,227,442 |
    | `calibrated_high_height` | `Map` | −2.4 % | ~2,852,236 |
    | `calibrated_high_weight_high_height` | `Map` | −3.0 % | ~3,687,683 |

    A plausible reading — not something this run tests directly — is that `Rank_labor` scores a slot
    against the *current* layout, so a delay between the reorder decision and the physical restock
    devalues that score, while `Map`'s cluster assignment is a property of the SKU rather than of
    the moment. `Rank_labor` does not vanish: it still places second at the steepest calibration
    (−2.6 %). The ergonomic trend from `lt0` survives intact — steeper penalties, larger win.

## Discussion

This run establishes the two axes every later experiment re-tests: **how much placement is worth**
and **what changes the answer**. The size of the prize is set by the cost model — a warehouse where
picks are cheap has little for a placement rule to recover — and the identity of the winner is set
by how much the layout has moved between the decision and the restock.

Both effects are small in absolute terms here (≈ 1–4 %), which is the honest headline: placement is
a real lever, not a transformative one, at this catalogue size and cost model. The
[2026-06-27 run](comparison-20260627.md) re-runs the same catalogue at a larger scale and finds the
same ranking with substantially larger margins.

The full strategy suite for this run — including the families that *lose* to FIFO — is on
[Full results](full-results.md).

!!! note "Notes"
    Open questions this run raises, each picked up later:

    - Does the lead-time flip (`Rank_labor` → `Map`) hold at other scales? The
      [2026-06-27 run](comparison-20260627.md) says **no** — `Rank_labor` wins both inventories
      there — so the flip is not a robust property of randomised lead time on its own.
    - Is ~4 % the ceiling? See [Experiment 2](../experiment-2/index.md), which splits the catalogue
      into independent store and fulfillment warehouses and finds two *different* champions.
    - Every arm here is one picker-scheduling policy (round-robin). That assumption is what
      [Experiment 5](../experiment-5/index.md) and [Experiment 6](../experiment-6/index.md)
      eventually relax — and it turns out to move throughput far more than placement does.
