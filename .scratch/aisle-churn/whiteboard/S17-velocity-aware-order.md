# S17 — what a velocity-aware unloading order can win when puts stay FIFO

**Question (the user's framing, 2026-09-23).**  A real put runs in arrival order, plus or minus
a few placements.  "Send fast packs to the best bins" is therefore an UNLOADING-ORDER policy,
not a free placement rule.  How much can such an order win, and at what granularity?

**Model.**  Under a FIFO put the placement rule hands out each day's bins in its own order; the
unloading order only decides which pack meets which of them.  S09's rearrangement bound prices
that pairing on the fresh picks, per (day, class): pick weight w (handling × units later picked
from the pack) against height multiplier M.  Four levels (`assets/s17_order_prize.py`):

| level | freedom | forecast |
|---|---|---|
| oracle | any pack order | hindsight w |
| λ·h | any pack order | the SKU's handling rate |
| expected weight | any pack order | **h_s · q_pack · (1 − e^{−λ_s(H − t − ℓ)})**: handling × units × the chance a later line reaches the pack in time |
| trailers | whole trailers only, as FIFO loading packed them | trailers ranked by their mean expected weight |

**Measurement.**  fifo-placement arms, the realistic FIFO put.  Change in pick time T against
the order the run used:

| run | oracle | λ·h | expected weight | whole trailers |
|---|---|---|---|---|
| 40k, k = 1 | −1.04% | +0.03% | +0.01% | +0.03% |
| 40k, k = 10 | −3.94% | −0.12% | **−0.85%** | +0.02% |
| 40k, k = 20 | −4.31% | −0.01% | **−1.06%** | +0.02% |
| 40k, k = 30 | −4.24% | −0.04% | **−1.25%** | +0.02% |
| 400k, k = 1 | −1.20% | −0.08% | −0.12% | +0.00% |

**What it says.**

1. **The hindsight ceiling is large, 1–4% of pick time, but almost none of it is forecastable
   at the declared demand.**  At λ·H ≪ 1 whether a pack is picked again is luck; the oracle is
   paid for knowing it.
2. **What is forecastable grows with churn** exactly as the fresh-bin law says: 0.1% at 1×,
   0.85% at 10×, 1.25% at 30×.  The expected-weight forecast captures about a quarter to a third
   of the ceiling.
3. **The SKU's rate is the wrong forecast.**  λ·h captures nothing at any density: once packs
   ARE re-picked, what separates them is the pack's size, its SKU's handling, and how much of
   the window is left.
4. **Whole-trailer resequencing wins nothing, at any density.**  FIFO loading packs each
   trailer with the day's reorders in fire order, so a trailer is a mix, and its mean expected
   weight barely varies between trailers.  **Below the dock gate, an order over whole trailers
   cannot steer fast packs to good bins.**  The steering needs either:
   - sequencing INSIDE the trailer: the yard's local policy, pallet or pack order within a
     trailer (`YardTransit` `local_policy`), where the pack-level numbers apply up to the
     pallet granularity; or
   - velocity-sorted LOADING at the origin, so trailers differ.
5. **Above the dock gate the order has a second, larger lever** (S12, S16): it decides which DAY
   a pack reaches the shelf.  That is where lifo beat fifo by 2–3% on fulfillment.

**Next.**  The pallet-level version: the prize at the within-trailer local-order granularity,
which is what the simulator's local policy can actually do.  And the S16 400k confirmation of
the gate.
