# S08 — the fresh-bin law: how much of the picking an inbound decision can touch

**Question.**  An inbound decision — which bin a pack takes, in which order trailers are
unloaded — can only change the cost of picks served from the bins it filled.  How much of the
picking is that, in closed form?

**Hypothesis (from S02, S03 and ADR-0003).**
1. Every SKU sits on its floor, so every line fires a lot of exactly what it took (S03): a lot is
   ONE LINE's worth, and it lands as small fresh packs.
2. The simulator drains a SKU's bins **smallest on hand first** (ADR-0003), so the SKU's next line
   is served from those small fresh packs, while the declared bulk stock waits untouched.
3. Hence a pick is served from an inbound-placed bin iff an earlier line of the same SKU fell at
   least one order-to-shelf lead ℓ before it (ℓ ≈ 1 batch to fire + 1.77 days of transit).

**Derivation.**  Lines of SKU s at Poisson rate λ_s over a window of H days.  UNCONDITIONAL — for
a prediction, where the rate is known:

$$ \mathbb{E}[\text{served}_s] = \int_{\ell}^{H} \lambda_s\left(1 - e^{-\lambda_s (t-\ell)}\right)dt
 = \lambda_s (H-\ell) - \left(1 - e^{-\lambda_s (H-\ell)}\right),
 \qquad \varphi(H) = \frac{\sum_s \mathbb{E}[\text{served}_s]}{\sum_s \lambda_s H} $$

CONDITIONAL on the window holding n lines of the SKU — for a retrodiction, where the counts are
known: the lines are n uniform points on [0, H]; the j-th is served iff its gap to the first,
H·Beta(j−1, n−j+2), is at least ℓ:

$$ \mathbb{E}[\text{served}_s \mid n] = \sum_{j=2}^{n}\left(1 - I_{\ell/H}(j-1,\ n-j+2)\right) $$

**Prediction.**  The conditional form within ±2 points of the script-exact count, and both within a
few points of the measured unit share.

**Measurement.**  `assets/s08_churn.py` on the 400k campaign root, cell `k1_off_fifo`, batches
0–39, ℓ = 2.77 batches; measured by `run_unload_ranking.inbound_repick` (units picked from bins
that received a reorder in the window, over units picked).

| section | lines | measured (units) | exact from the script (lines) | closed form, conditional | re-picked share of placed units |
|---|---|---|---|---|---|
| store | 24,725 | 8.61% – 8.66% | 7.83% | **7.68%** | 9.2% – 9.4% |
| fulfillment | 119,223 | 21.8% – 23.9% | 27.2% | **26.5%** | 22.7% – 24.9% |

**Residuals and revision.**
- First read, the unconditional form with each SKU's realised count as its rate: 35.9% / 44.6% —
  wrong by 4×.  Diagnosis: conditioning — a SKU that appeared once has no served line, and the
  rate's effect is convex, so a realised rate plugged into the unconditional form is biased up.
  Revision: the conditional (Beta) form for retrodictions; the unconditional form, fed the
  SAMPLER's rates, for predictions.
- The law holds within 0.2 points of the exact count.  The remaining gap to the measured UNIT share
  (store +0.9, fulfillment −3.4 points) is lines vs units: a fresh pack holds the earlier line's
  quantity, so a later, larger line is served partly from bulk (fulfillment), and a smaller one
  entirely from the fresh pack (store).  Named, accepted.

**What it says.**  An inbound decision reaches **about 8% of the store's picking and about 25% of
fulfillment's** over a 40-day window — and only through the picks that follow a repeat line.
Everything else is picked from the declared bulk stock the decision never touched.  With
λ_s·H small, φ(H) ≈ λ̄H/2 − ℓ-correction: it grows **linearly with demand density and with the
horizon**, which is what makes the (k, c) grid's time–density collapse testable.  Stock depth
barely enters: under smallest-first draining a fresh pack is consumed next whatever the bulk
holds, so depth moves when the BULK's bins free, not how much picking reaches fresh bins.

**Next.**  S09: the bound on what a placement rule and an unloading order can move, and the
retrodiction of the fill-trial and campaign nulls.
