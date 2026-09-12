---
name: v2-defect-manufactured-the-fill-law-evidence
description: "the fulfillment fill-law gap was a v2 sampler artifact, CONFIRMED on a run: under v3 the equilibrium instrument reads 12 arms, 0 failed, and the gap goes +0.0793 to +0.0033 — the duplicate draws were 95.5-97.1% of it"
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

**The prediction was MEASURED and it held** (dept-cal 46, `comparison_20260912_134002`, the
same shape at the v3 sampler). The equilibrium instrument reads **12 arms judged, 0 FAILED**
against v2's 4:

| supply clause | v2 | v3 | expected | tol |
|---|---|---|---|---|
| fulfillment (4 arms) | 0.1025 - 0.1044, all FAIL | **0.0274 - 0.0286, all PASS** | 0.0251 | 0.020 |
| store (4 arms) | 0.0300 - 0.0302, PASS | **0.0271 - 0.0285, PASS** | 0.0248 | 0.020 |

The fulfillment gap went **+0.0793 -> +0.0033**: the duplicate draws were **95.5-97.1%** of it
(store 30-58%). The realized lead was re-measured too (drawn K 1.6742, realized 1.78-1.84 over
310 trailers) and re-pricing at it moves fulfillment +0.0002. The LABOUR clause moved the other
way -- fulfillment 0.0099 -> 0.0339 against 0.0246 at tol 0.032, still passing -- because v3
delivers 9.5% more lines to the same crew.

Consequence: dept-cal 40 / 41 / 42 (the draw-probability form) were ruled OUT OF SCOPE. `p_s`
had one consumer and it no longer needs correcting. Recorded as ADR-0006
(`docs/adr/0006-the-fill-law-gap-was-a-sampler-artifact.md`), which carries the fitted multiplier
as the rejected alternative.

**How to apply:** treat every fill-law, repeat-structure or per-SKU-inclusion number measured
before 2026-09-12 as drawn from a defective sampler, and re-measure rather than carry it.
Reproducing a v2 baseline before reporting a v2-to-v3 difference is what makes the difference
attributable -- do it. Related: [[v3-sampler-era]],
[[sampler-affinity-flattens-the-fulfillment-line-rate]],
[[draw-probability-replaces-line-share]], [[a-count-is-not-a-claim]]. Also [[crew-denomination-decides-sampler-sensitivity]].
