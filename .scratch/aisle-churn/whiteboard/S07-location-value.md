# S07 — the value of a location, and fulfillment's co-location lever

**Question.**  S06's closed form priced the store's placement gap to within half a point but
missed fulfillment's (≈ 0 predicted, −1.6% to −2.2% realised on the 40k fill root).  Every
fulfillment bin sits at M = 1 and its lanes are one-way, so the lever can only be WHICH AISLES
a day opens.  What is a location worth, in closed form?

**Derivation (the Mecke / Palm form; `models/pick.py`, `LOCATION`).**

$$ g_b = \lambda_s\left[\,h_b + P^0_a(s)\,T_{\mathrm{new}}(b) + \left(1-P^0_a(s)\right)\mathbb{E}[\Delta T_b \mid a\ \mathrm{open}]\,\right] $$

| term | meaning |
|---|---|
| h_b | the at-location handling (`PickConfig.closed_form`) |
| P⁰_a(s) | the **Palm** probability that no other line of a day that draws s lands in aisle a.  It is conditioned on s being drawn, which is where the sampler's affinity enters. |
| T_new | the cost of a task opened only for b |
| E[ΔT \| open] | 0 on a one-way lane; twice the overshoot past the farthest column on a two-way lane |

The aisles a day opens come in two forms:
- **independent** (the chain's): Σ_a (1 − e^{−Λ_a});
- **script-conditional**: the mean over days of the distinct aisles occupied by that day's SKUs.

Moving s from aisle a′ to a changes the script form by exactly λ_s·(P⁰_a − P⁰_a′).
`colocation_delta` computes that, and the test holds it equal to a recount.

**Measurement** (`assets/s07_colocation.py`; 40k fill root, pick stage, each SKU at its
first-drained bin, keyframes 100 and 125).

| arm | K | independent | script-conditional | realised tasks |
|---|---|---|---|---|
| fifo | 100 | 94.2 | 93.6 | 156.4 |
| fifo | 125 | 105.1 | 104.8 | 156.1 |
| rank_minlabor | 100 | **106.4** | **77.4** | 144.2 |
| rank_minlabor | 125 | **108.8** | **84.3** | 147.4 |

- **The independent form gets the sign wrong.**  It says the ranked rule opens 3–13% MORE
  aisles.
- **The script form says 17–20% fewer**, because the rule co-locates SKUs the sampler draws
  together.
- **Realised tasks fall 6–8%.**  A task is an aisle visit, including a return when the cart
  fills, and those returns scale with the aisle's load.  So consolidating aisles saves about
  half its distinct-aisle reduction in tasks.

At fulfillment's ~93 s of travel per task (S06 term split), the distinct-aisle reduction prices
at −2.6% / −3.2% of pick time as an upper bound.  The realised gaps are −2.2% / −1.6%.

**Residual, named.**  Repeat visits per aisle (cart-full returns) are not in the script form.
The labour conversion is therefore bounded above by the distinct-aisle count and below by the
realised task gap.

**What it says.**  Fulfillment's placement lever is co-location of co-drawn SKUs.  The
expected-travel chain's independent-visit assumption makes it invisible, and in fact inverted.
Any evaluator that prices a fulfillment placement through independent visit probabilities will
rank co-locating rules wrongly.  The Palm probability P⁰_a(s) is the quantity to use.  On this
run fulfillment sees the effect only once most of its stock is rule-placed (the fill root, 100
batches of placement).  In the 40-batch grid it appears only at the saturated k30_c95 point
(−0.9% to −1.1%).
