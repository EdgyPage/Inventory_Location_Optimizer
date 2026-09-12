---
name: v2-defect-manufactured-the-fill-law-evidence
description: "the fulfillment fill-law gap's whole evidence base was the v2 sampler's duplicate draws — 335 SKUs drawn on half the days carried the repeat statistic; under v3 the concentration signature, the lag-1 suppression and the 3.74x under-pricing all vanish"
metadata: 
  node_type: memory
  type: project
  originSessionId: d00ae947-94f7-4253-8a31-a99472d9be15
  modified: 2026-09-12T18:27:32.715Z
---

Measured 2026-09-12 (dept-cal 45) by drawing the reference pair's batch script under v3 and
re-running 39's own asset against it. The v2 baseline was reproduced first, exactly (lag lifts
0.8461 / 1.8078 / 1.6569 and 0.8383 / 0.9772 / 0.9807, `m` 1.739 / 3.968, rebuild gate delta
0.000e+00), so every difference below is the sampler and nothing else.

Reproduce in two commands:

    REPO=. python .scratch/department-calibration/assets/draw_batches_under_sampler.py <dir> v3
    python .scratch/department-calibration/assets/measure_repeat_and_lead.py --run <run> \
        --pair <pair> --inventory <inventory.db> --batch-dir <dir>

**The artifact, in raw counts.** Over days 20-39 under v2, **335 fulfillment SKUs were drawn on
at least half the 20 days and one on 17 of 20**. Under v3 the maximum is **5 of 20 and none
reaches half**. Those were the Fenwick-boundary SKUs with `p_s` ~0.75-0.80
([[v2-sampler-redraws-selected-skus]]), and they carried the statistic: lag-1 co-occurring pairs
fall 4,815 -> 1,037, a 78% drop, on 9% MORE line-days.

| fulfillment, days 20-39 | v2 | v3 |
|---|---|---|
| SKUs touched vs share-law-true control | 34,683 (-25.2%) | 47,910 (**+3.3%**) |
| lag-1 lift | 0.838x | **1.014x** |
| P(prior line within K) vs record's Poisson 0.04140 | 0.14938 (3.74x) | **0.03485 (0.87x)** |
| fitted `m`, share of gap | 3.968, 71% | 1.000, **0%** |

Store: touched +4.0% -> +5.9%, prior-line 0.01052 (1.59x) -> **0.00721 (1.19x)**, `m` 1.739 ->
1.189 (52% -> 13%).

**What died.** dept-cal 38's affinity-concentration premise, and 39's "the record under-prices
its own prior-line event 3.74x, one multiplier recovers 71% / 52%"
([[fill-gap-is-the-line-count-shape]]). Neither survives.

**What it is now: the record is roughly right, and wrong in OPPOSITE directions per channel.**
It UNDER-prices the store by 19% (0.00607 against 0.00721) and OVER-prices fulfillment by 16%
(0.04140 against 0.03485). No single multiplicative correction can close both. Note that
`Binomial(K, p_s)` gives a strictly lower `P(N >= 1)` than a Poisson of the same mean, so at
equal mean it helps fulfillment and hurts the store.

**The realized missed shares were NOT re-measured and are still v2 outcomes** (store 0.0302,
fulfillment 0.1044). Any "share of the gap" scored against them now compares a v3 generator with
a v2 run and is meaningless. There is good reason to expect the defect inflated the realized miss
too -- those 335 SKUs were drawn 15-17 days of 20 against levels sized for ~1.5 lines per SKU, so
they would have been missing almost constantly -- but that is a PREDICTION, and dept-cal 46 is
the run that measures it.

**How to apply:** treat every fill-law, repeat-structure or per-SKU-inclusion number measured
before 2026-09-12 as drawn from a defective sampler, and re-measure rather than carry it.
Reproducing a v2 baseline before reporting a v2-to-v3 difference is what makes the difference
attributable -- do it. Related: [[v3-sampler-era]],
[[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[draw-probability-replaces-line-share]], [[a-count-is-not-a-claim]].
