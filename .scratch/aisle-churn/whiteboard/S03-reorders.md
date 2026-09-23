# S03 — when reorders fire, how often, and what lands

**Question.**  When does a SKU reorder, how many fires does a day's demand cause, and how many
units come back?

**Derivation.**  `Optimization/simconfig/models/reorders.py` (`REORDERS`).  The simulator
(`inventory_reorder.py:246-274, 501-569`) flags a SKU when, after a pick, its position (on hand +
queued + deferred) is at or below r, and fires at the start of the NEXT batch for
`ideal = S − position`, S = Q + P, noised `max(1, round(N(ideal, ideal·κ)))`.  On the floor
r = Q − 1 (S02: every SKU), so from a full position a fire needs the units taken since the last
fire to reach P + 1:

$$ \mathbb{E}[N_s] = \sum_{j=0}^{P_s} U(j), \qquad U(0) = 1,\ \ U(j) = \sum_{k=1}^{j} p_k\,U(j-k) $$

the renewal count of the line-quantity sum over P + 1 (p_k the line law).  Supply noise lands a
fire ε units off, so the next threshold is P + 1 + ε:

$$ \mathbb{E}[N_s] = \sum_{\epsilon} \Pr(\epsilon)\,\operatorname{U}_{\Sigma}\left(\max(1, P_s + 1 + \epsilon)\right),
\qquad \epsilon = \operatorname{round}\,\mathcal{N}\left(0, (\mathbb{E}[\mathrm{lot}]\,\kappa_s)^2\right) $$

and, by Wald, fires/day φ_s = p_s / E[N_s], lot = E[q]·E[N], units ordered/day = p_s·E[q].

**The answer to "when".**  With P = 0 — 92% of SKUs on the 40k catalogue, all of the store —
**every line fires**, at the start of the next day, for exactly what it took (± supply noise).
SKUs with P ≥ 1 (fulfillment's faster movers, P = round(d·1.77 d)) skip lines smaller than
P + 1.  The lot is in transit for the trailer lead (median 480 min, lognormal σ 0.7: E⌈T/D⌉ =
1.77 days), is unloaded when a door and a receiver free, and is put away that day or after.

**Prediction.**  Fires within ±2% of Σ lines_s / E[N_s] over the realised lines; units ordered
within ±2% of units picked.

**Measurement.**  `assets/s03_reorders.py` (picks in [lo, hi−1) against fires in [lo+1, hi)).

| root | section | fires vs model | units ordered vs picked + pipeline fill |
|---|---|---|---|
| 40k fill, pick stage | fulfillment | −0.48% to −0.53% | +0.06% to +0.07% |
| 40k fill, pick stage | store | −0.65% to −0.72% | −0.17% to −0.19% |
| 400k campaign, batches 1–39 | fulfillment | −1.4% to −1.7% (noise-free model) | +1.57% (before the fill term) |
| 400k campaign | store | −0.93% (noise-free) | +0.03% |

**Residuals and revisions.**
1. **First read (noise-free, no fill):** fulfillment units +1.66%, fires −0.7%.  Diagnosis: a run
   starts every SKU at Q with nothing on order, so a P ≥ 1 SKU's first fire orders P extra
   units — the **pipeline fill**, a one-time transient (1,985 units = the whole +1.66%).  Revision:
   counted separately; units now +0.06%.
2. **Supply noise** shifts the threshold (above).  Revision: `renewal_lines_noisy`; a replica of the
   position rule (`Tests/unit/test_models_levels_reorders.py`, 200,000 lines per case) agrees within
   1%.  It closes about a quarter of the fire gap.
3. **Named residual, accepted:** the simulator fires 0.5–0.7% fewer than the model, on the store (no
   pipeline) as much as on fulfillment.  The replica says the rule as modelled is right, so the
   gap is something outside it (candidates: lines picked short of q, a flag cleared by a same-day
   arrival).  Within the ±2% flow tolerance; not chased.

**Next.**  S04/S05: what those fires cost to carry, unload and put away.
