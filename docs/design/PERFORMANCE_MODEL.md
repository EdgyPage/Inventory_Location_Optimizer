# Performance Dynamics Model

A first‑order analytical model of how warehouse **layout quality** evolves over
batches, why each assignment strategy converges to the limit it does, and how to
**predict** steady‑state performance without running the full horizon.

Derived from and validated against the `comparison_20260608_012912` set
(12 demand profiles × 3 pick configs × 6 strategies = 216 simulation runs).

---

## 1. State variable: layout quality

The cost model rewards putting high pick‑frequency SKUs in low within‑aisle
travel‑cost bins. Summarise the whole layout by **demand‑weighted within‑aisle
travel**

$$\Sigma fD(t) \;=\; \sum_{\text{occupied bins } b} f_{\,\mathrm{sku}(b)}\cdot D(b),
\qquad D(b)=x_{\text{speed}}\,x_{\text{phys}}(b)+y_{\text{speed}}\,y_{\text{phys}}(b),$$

where $f$ is pick frequency. Define the normalized **efficiency**

$$E_t \;=\; \frac{\Sigma fD_{\text{opt}}}{\Sigma fD(t)} \in (0,1], \qquad E=1 \text{ is optimal,}$$

with $\Sigma fD_{\text{opt}}$ the pure‑global‑D optimum (hottest SKUs → lowest‑D bins
per BinKey class; rearrangement‑inequality optimum). $E_t$ is the curve in
`plot10_sigma_fd`.

---

## 2. Dynamics: a mixing process

Each batch, three mechanisms rewrite "which SKU sits in which bin":

| mechanism | demand‑weighted fraction of bins rewritten / batch | deposits at quality |
|---|---|---|
| **reorders** (replenish depleted SKUs into new bins) | $\alpha_r$ | $e_r$ — the reorder rule's characteristic placement quality |
| **re‑slot** (bounded local moves toward optimal) | $\alpha_s$ | $e_s \approx 1$ |
| untouched bins | $1-\alpha_r-\alpha_s$ | retain $E_t$ |

This gives a first‑order linear recurrence:

$$\boxed{\,E_{t+1} = (1-\alpha_r-\alpha_s)\,E_t + \alpha_r e_r + \alpha_s e_s\,}$$

with closed‑form solution

$$E_t = E^\* + (E_0 - E^\*)\,(1-\alpha)^t,
\qquad \alpha = \alpha_r+\alpha_s,
\qquad E^\* = \frac{\alpha_r e_r + \alpha_s e_s}{\alpha_r + \alpha_s}.$$

**Consequence.** Every strategy approaches the *same* fixed point $E^\*$
geometrically — from below (uniform start) or above (optimal start) — with time
constant $\tau = 1/\alpha$. Exactly the observed behaviour: uniform climbs,
optimal decays, both converge.

---

## 3. Parameters — all measurable

| symbol | meaning | how to obtain | value (this set) |
|---|---|---|---|
| $\alpha_r$ | reorder turnover / batch | cumulative reorder placements ÷ bins ÷ batches | ≈ 0.11 |
| $\alpha_s$ | re‑slot turnover / batch | cumulative re‑slot moves ÷ bins ÷ batches | ≈ 0.0008 |
| $e_r$ | reorder rule's myopic placement quality | one‑batch measurement, or analytic (§3.1) | rule‑specific |
| $\tau = 1/\alpha$ | convergence time | — | ≈ 9 batches |

Because $\alpha_r \gg \alpha_s$, the fixed point is dominated by the reorder rule:
$E^\* \approx e_r$. And $e_r$ equals the *quality of a single placement* by that
rule — which is exactly each strategy's observed plateau:

| reorder rule | predicted $E^\* \approx e_r$ | observed final $E$ |
|---|---|---|
| uniform (random bin) | ~0.62 | **60–62 %** |
| min‑D + rank (Uniform+Rank / Trip‑Min) | ~0.82 | **82–83 %** |
| trip‑max (highest‑D) | ~0.48 | **47.9 %** |

The model reproduces all three plateaus from a single per‑rule constant.

### 3.1 Deriving $e_r$ a priori

For **uniform** reorder (random available bin), $e_r$ is a rearrangement ratio of
the demand and bin‑D distributions:

$$e_r^{\text{unif}} = \frac{\big(\sum_i f_i\big)\,\overline{D}}{\sum_i f_{(i)}D_{(i)}},$$

where $f_{(i)}, D_{(i)}$ are frequencies/bin‑costs sorted into the optimal pairing
(hot↔low‑D) and $\overline{D}$ is the mean bin cost. Computable from distributions
alone (≈ 0.62 here).

For **min‑D** reorder the same ratio applies but over the *free*-bin D‑distribution
at the current fill $\psi$ (a hot SKU lands at the low‑D quantile of whatever is
currently open). Harder to write in closed form, trivial to measure from one batch.

---

## 4. Why re‑slot barely moves the limit — quantified

$$E^\*_{\text{with reslot}} - E^\*_{\text{without}}
\;\approx\; \frac{\alpha_s}{\alpha_r}\,(1-e_r)
\;\approx\; \frac{0.0008}{0.11}\,(0.18) \;\approx\; 0.0013,$$

i.e. **+0.1 pp** — matching the measured negligible effect. The reorder churn
(~1100 % of bins over 100 batches) overwhelms the re‑slot labor budget (~8 %).

**Budget requirement (a prediction, not a fit).** To lift the steady state by
$\Delta$ requires

$$\frac{\alpha_s}{\alpha_r} \;\approx\; \frac{\Delta}{1-e_r},$$

i.e. re‑slot turnover comparable to reorder turnover — here ~140× the current cap,
which is not labor‑feasible. Re‑slotting cannot win against a churning shared
layout; the **reorder rule** is the lever.

---

## 5. The durable optimal‑start edge = the frozen tail

A single $\alpha$ is a mean‑field average. Refine by splitting bins into a
**churning** mass $(1-\varphi)$ and a **frozen low‑demand tail** $\varphi$ whose
SKUs never deplete within the horizon (so are never rewritten):

$$E^\* = \varphi\,E_0^{\text{tail}} + (1-\varphi)\,e_r.$$

- **Optimal start** keeps its tail at quality 1 → $E^\* = \varphi\cdot 1 + (1-\varphi)e_r$.
- **Uniform start** keeps its tail at uniform quality (~0.62).

The gap, weighted by the tail's (small) frequency share, is the persistent
**~2 pp** advantage of Optimal+Reslot (84.4 %) over Trip‑Min (82.8 %). So the
permanent value of a perfect start equals exactly the frequency‑weight of the SKUs
that never turn over — obtainable from the demand reorder‑interval distribution
versus the run horizon.

---

## 6. Mapping layout quality → performance

Within this cost model, batch makespan is dominated by total travel
$\Sigma fD(t) = \Sigma fD_{\text{opt}} / E_t$, plus task overhead and a concurrency
term. Empirically duration is near‑affine in $\Sigma fD$:

$$\text{duration}(t) \approx a + b\,\frac{\Sigma fD_{\text{opt}}}{E_t},
\qquad
\text{throughput}(t) \approx \frac{\text{items per batch}}{\text{duration}(t)}.$$

Fit $a,b$ by regressing duration on $\Sigma fD$ across batches (or use a saturating
M/G/k queueing form for the concurrency curvature). The full performance
trajectory then follows from $E_t$.

---

## 7. Using the model

- **Cheap extrapolation.** Fit $\alpha$, $e_r$, $\varphi$ from a short (~15‑batch)
  run and project the closed form to $t = 100, 500, \infty$ — no need to simulate
  the whole horizon.
- **A priori.** Compute $e_r$ and $\alpha$ from the demand distribution + bin‑D
  distribution + fill, and predict a rule's steady state *before* running it.

---

## 8. Validation summary

| quantity | model | observed |
|---|---|---|
| uniform‑reorder plateau | $e_r \approx 0.62$ | 60–62 % |
| min‑D‑reorder plateau | $e_r \approx 0.82$ | 82–83 % |
| trip‑max plateau | $e_r \approx 0.48$ | 47.9 % |
| convergence time | $\tau = 1/\alpha \approx 9$ batches | uniform/optimal converge well within 100 |
| re‑slot lift on $E^\*$ | +0.1 pp | negligible |
| optimal‑start durable edge | $\varphi$·(tail gap), small | +~2 pp (84.4 vs 82.8) |

---

## 9. Caveats (where the math is approximate)

- **No cross‑aisle distance.** $\Sigma fD$ is the right objective only under the
  per‑aisle, entrance‑anchored travel model. Adding a routing term introduces a
  task‑count component into §6.
- **Concurrency nonlinearity.** The affine duration map ($a,b$) is the weakest
  link at high picker load; a saturating M/G/k term is more faithful.
- **Fill drift.** $e_r$ for min‑D shifts slightly with fill $\psi$ (the free‑bin
  distribution changes), so $E^\*$ is mildly time‑varying near the start.

---

*Companion: `plot10_sigma_fd.png` (efficiency $E_t$) and `plot11_churn.png`
(turnover $\alpha_r,\alpha_s$) per config under each run directory.*
