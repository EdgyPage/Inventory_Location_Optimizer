# S14 — why the free pool grows off the floor (a partial law)

**Question.**  S11 found the fulfillment free pool GROWING 13–25% once most SKUs leave the one-line
floor (k = 10).  The store stays flat, and fulfillment shrinks 3% at k = 3.  In closed form?

**Hypothesis.**  A run starts every SKU at Q on hand with nothing on order.  It settles to the
mean on-hand of its order-up-to cycle.  The position runs over (rp, S = Q + P] and the pipeline
holds d·ℓ, so

$$ \mathbb{E}[OH] \approx \frac{rp + 1 + Q + P}{2} - d\,\ell,
\qquad \frac{\Delta F}{F_0} \approx \frac{\text{bins}_0}{\sum Q}\cdot\frac{\sum_s (Q_s - \mathbb{E}[OH]_s)}{F_0} $$

- On the floor (rp = Q − 1, P = 0) the drawdown is only d·ℓ.
- Off the floor, Q = C·d sits far above the cycle mean.

**Measurement** (`assets/s14_drawdown.py`; the record and catalogue give the prediction, the
`free_index` series gives the measurement).

| point | fulfillment on floor | predicted ΔF/F₀ | measured |
|---|---|---|---|
| k1_c95 | 100% | +1.0% | −0.7% |
| k3_c95 | 100% | +2.2% | −2.6% |
| k10_c95 | 68% | +13.3% | +1.8% |
| k20_c95 | 68% | +11.9% | +5.3% |
| k25_c95 | 68% | +11.6% | +7.9% |
| k30_c95 | 68% | +11.1% | +16.5% |
| k10_c80 | 36% | +25.7% | +17.5% |
| k30_c80 | 36% | +24.7% | +14.7% |

The store is on the floor throughout: predicted +0.2% to +2%, measured −1% to +0.3%, with k30_c95
at +4.9%.

**Residuals, named. The law is partial.**

1. **Fragmentation.**  Replenishment arrives as one-line lots, and each takes a whole bin.  The
   units-to-bins ratio of fresh stock is far below the initial bulk's, so occupied bins can rise
   while on-hand units fall.  This is why the floor points shrink (the law says flat or
   slightly growing), and why k10_c95 grows far less than predicted.  The repo already models
   this in `fragmentation.section_fragmentation`; composing it here is the revision.
2. **The dock is coupled to the pool.**  One fulfillment declaration (the ff side is identical
   at k10–k30 c95) drifts +1.8% → +5.3% → +7.9% → +16.5% as STORE demand rises.  The shared
   site dock (S04) delays fulfillment's inbound, so on-hand drains below the cycle mean, and the
   realised ℓ exceeds the record's.

**What it says.**  The sign and the scale off the floor are right: breathing room is stock the
declaration holds above its own reorder cycle.  The law is not accepted as quantitative until the
fragmentation chain is composed in and the realised lead replaces the declared one.  **Open.**
