# S02 — where a SKU's equilibrium units come from

**Question.**  What sets how many units of a SKU the warehouse holds, and does the closed form
reproduce the declaration exactly?

**Model.**  `Optimization/simconfig/models/levels.py` (`LEVELS`), every equation registered with
a mirror to `coverage.py` and held equal by `Tests/unit/test_closed_form.py`:

$$ \mathbb{E}[q_s] = \lambda_s + e^{-\lambda_s} \qquad d_s = n\,\pi_s\,\mathbb{E}[q_s] \qquad
L_s = \max\left(1, \left\lceil f\,\mathbb{E}[q_s] - \epsilon \right\rceil\right) $$

$$ \ell_s = \max(0,\ell_{\mathrm{sup}})\,u_d + \mathbb{E}\lceil T/D\rceil \qquad
P_s = \max\left(0, \operatorname{round}(d_s \ell_s)\right) $$

$$ Q_s = \max\left(L_s, \operatorname{round}(C d_s)\right) \qquad
r_s = \min\left(Q_s - 1, \max\left(L_s, \operatorname{round}(d_s(\ell_s + \sigma_{\mathrm{safe}}))\right)\right) $$

A SKU is **on the floor** when round(C·d_s) ≤ L_s: then Q = L, r = Q − 1 — base stock.

**Prediction (committed before measuring).**  Exact equality with the stamped levels on every
SKU (the model is the record's own arithmetic); essentially 100% on the floor (memory
`coverage-in-days-floors-the-store-section`).

**Measurement.**  `assets/s02_levels.py` on the 40k fill root and the 400k campaign root, with the
declaration's own recorded inputs (`run_spec.json` → `staffing.calibration.<pair>.coverage`).

| root | section | SKUs | Q, rp, P equal | on the floor | mean Q | days of cover Q/d: p10 / median / p90 |
|---|---|---|---|---|---|---|
| 40k | store | 23,880 | all three, every SKU | 100% | 12.85 | 279 / **637** / 3,921 |
| 40k | fulfillment | 16,120 | all three, every SKU | 100% | 16.24 | 45 / **108** / 224 |
| 400k | store | 239,938 | all three, every SKU | 100% | 12.86 | 279 / **635** / 3,954 |
| 400k | fulfillment | 160,062 | all three, every SKU | 100% | 16.22 | 45 / **109** / 226 |

**Residual.**  None: 800,000 SKUs, Q, r and P equal to the unit.

**The answer.**  **A SKU's equilibrium units are its line floor**, L = ⌈f·E[q]⌉ with f the solved
floor lines (1.31 store, 1.50 fulfillment): enough stock for about 1.3–1.5 average lines, sized so
the first pick of a day is served from the shelf with probability √0.95.  The declared coverage
(C = 10 days) never binds: the coverage term C·d is below the floor for every SKU, because a
SKU is asked for so rarely (S01: once in ~140 days at the store's top decile).  The stock is
therefore sized by **how big one line is**, not by how fast the SKU sells, and it lasts
**Q/d ≈ 640 days** on the store and **≈ 110** on fulfillment.

**What that means for churn (the study's question).**  A bin is freed only when its SKU sells
out of it.  With 640 days of cover, a 40-batch window frees a small fraction of the store's bins;
that is the physical content of "the bins have no breathing room".  The two grid levers act on
exactly this ratio:

- **demand density k** multiplies d, so cover falls as 1/k; a SKU LEAVES the floor only when
  round(C·k·d) > L, i.e. k > cover/C ≈ 64 for the median store SKU at C = 10.  Below that, Q
  stays at L and more demand simply means faster turnover of the same stock.
- **stock depth c** lowers the solved f and so L (and Q), shortening cover proportionally, at
  the price of first-pick fill below √0.95.

**Next.**  S03: when reorders fire, how often, and what lands — on the floor, every line
should reorder exactly what it took.
