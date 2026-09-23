# S01 — how often the sampler asks for each SKU

**Question.**  Every per-SKU rate in the churn model (re-pick within a horizon, time a bin stays
occupied, lots per day) needs the probability that a day asks for SKU s.  The level record uses
the LINE SHARE, p_s = n·f_s/Σf.  Is that what the v3 sampler delivers?

**Hypothesis.**  Two effects could bend it.  (a) Without-replacement draws saturate popular SKUs
(successive PPS sampling: p_s = 1 − e^{−θ f_s}, θ solving Σp_s = n).  (b) The affinity lift
re-weights every draw after the first by the partners already drawn.  Memory
`sampler-affinity-flattens-the-fulfillment-line-rate` says fulfillment's per-SKU spread is 7.1×
predicted but 1.6× delivered.

**Measurement.**  `assets/s01_inclusion.py` over the 40k fill root's two batch scripts
(`comparison_whatif_20260923_023109`, 40 batches each), by frequency decile.
Results in `assets/results/S01_*.csv`.

| section | n (lines/batch) | top/bottom decile spread: realised | line share | successive |
|---|---|---|---|---|
| store (23,880 SKUs) | 61.5 | **48.7×** | 20.6× | 20.6× |
| fulfillment (16,120 SKUs) | 300.1 | **1.71×** | 7.27× | 7.14× |

Section sums agree to the line in all three (by construction: n is declared).

**Residual and diagnosis.**
- Successive sampling ≈ line share in both sections (sampling fractions 0.26% and 1.9%), so
  **without-replacement is NOT the effect** — it would matter only when p_s approaches 1.
- The whole departure is the **affinity lift**, and it goes OPPOSITE ways in the two sections:
  the store's lift concentrates demand on its popular SKUs (steeper than frequency), the
  fulfillment lift spreads it (the memory's flattening, reproduced: 1.71× vs 7.27×).
- The line share therefore mis-states per-SKU rates by up to ~2× at the store's ends and ~2.7×
  at fulfillment's — section totals are right, per-SKU churn is not.

**Revision.**  Per-SKU rates enter every model as the REALISED p_s of the sampler, measured by
drawing the script — the sampler is the simulator's own code and runs alone in seconds.  The
line share is kept beside it as the documented gap.  For the grid (S10), p_s is re-drawn at
each demand multiplier k rather than scaled: the lift acts on partners already drawn, so its
shape may change as k grows.

**What this says about churn already.**  At the store's top decile p ≈ 0.0071/day, a SKU is asked
for about once in 140 days; its bottom decile, once in ~6,700.  In a 40-day window the
probability of even one line is 25% at the top and 0.6% at the bottom.  Fulfillment's
p ≈ 0.015–0.025 gives 45–63%.  That is the arithmetic behind "7.7% of store placements re-picked
in 40 batches".

**Next.**  S02: the levels, and where a SKU's equilibrium units come from.
