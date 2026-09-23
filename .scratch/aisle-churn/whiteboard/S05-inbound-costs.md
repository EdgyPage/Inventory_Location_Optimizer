# S05 — what receiving and put-away cost, per pack and per unit

**Question.**  S03 fixed the RATES (fires, units ordered).  What does each unit cost to unload
and to put away, and which part of that cost depends on the placement rule?

**Derivation.**  The per-event laws are the ones the cost classes carry, held equal to the
simulator by `Tests/unit/test_cost_laws.py`:

$$ t_{\mathrm{unload}} = p_r + \left(I_r + q\,v_s\right) \quad\text{per pack (no height, no travel)} $$

$$ t_{\mathrm{put}} = \frac{x_b}{12\,v_x} + \frac{y_b}{12\,v_y} + M(y_b)\left(I_p + q\,p_p + q\,v_s\right) $$

with I_p = ½·I, p_p = 0.2·p, I_r = I_p, p_r = p_p (`PutawayCost.from_pick`,
`UnloadCost.from_putaway`).  Taken in expectation over the script's lots, packed by the
simulator's own packer, the staffing derivation (`staffing.implied_reorders`) prices both; for
put-away it takes the destination `(x_b, y_b)` in expectation over a **class-uniform** free bin
(`expected_travel.put_site_pricer`) — which is exactly the fifo placement rule's steady state.
Receiving is placement-free; put-away is not.

**Prediction.**  Receiving per pack and units per pack within ±2% of the record; put-away per unit
within ±2% under the fifo placement rule; a ranked rule CHEAPER than the uniform price.

**Measurement.**  `assets/s05_inbound_costs.py` on the 40k fill root, pick stage (batches
100–139), `work_events` per leaf.

| section | placement rule | recv s/pack (vs record) | units/pack | put s/unit (vs record) |
|---|---|---|---|---|
| fulfillment | fifo | 8.30 (+0.0%) | 2.08 (exact) | 28.26–28.37 (−0.2% to −0.6%) |
| fulfillment | rank_minlabor | 8.30 (+0.0%) | 2.08 | 26.18–26.92 (**−5.3% to −7.9%**) |
| store | fifo | 211.2–211.7 (−2.6% to −2.9%) | 3.46 vs 3.42 | 92.9–93.1 (−3.3% to −3.6%) |
| store | rank_cartlabor | 210.8–211.7 (−2.6% to −3.0%) | 3.46 | 87.8–88.6 (**−8.1% to −8.8%**) |

**Residuals.**
- Fulfillment receiving and packing are exact; fulfillment put-away under fifo placement is within
  0.6%: the class-uniform destination law is right for the rule that is uniform.
- **Store, both departments, −3%: window mix.**  The window's lots carry slightly bigger packs
  (3.46 vs 3.42 units) and a different SKU mix than the script average the record prices; the
  store's handling term is steep in weight (pow 1.5), so the mix moves the per-pack cost.
  Memory `window-mix-before-model-error` names the same effect.  Accepted as a named residual.

**The decision-model finding.**  **Placement moves put-away cost immediately**: a ranked rule puts
5–9% cheaper per unit than uniform placement, on the very day it places — no churn, no waiting
for a pick.  That is the put-side term of the churn model (M6e): ΔPut/day = units put/day ×
(E_uniform[t_put] − E_rule[t_put]).  The same rules move PICK labour by 2–4% at best, and only
after the stock is picked; S06–S09 price that side.

**Not modelled here (deferred).**  The yard's depth and dwell as a queue: measured already
(ticket 10: depth 16.6 at four doors; memory `inbound-yard-is-a-stable-queue-under-the-era`,
ρ_recv 0.836), and it does not enter labour cost — it enters WHEN a lot lands, which S08 needs
only as the order-to-shelf lead.

**Next.**  S06: pick labour, the closed form against the simulator, and the drain-order correction.
