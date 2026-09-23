# S16 — the 400k confirmation at the dock gate, registered before running

**Approved by the user 2026-09-23** ("go for it when all available ducks are in a row").

**Question.**  Do the 40k laws hold at the reference scale?  In particular, does the unloading
order start to matter exactly where the door-utilisation gate says?

**The ducks.**

- **Where the gate is at 400k.**  The 400k campaign (`comparison_whatif_20260920_150203`, k = 1)
  already runs at ρ_door = 0.80 late in its window: 16.6 trailers/day × 1.55 h per trailer
  over 4 doors × 8 h.  Trailers scale with shipped volume, so the gate sits at k ≈ 1.25.
- **The run point.**  **k = 1.35 on both channels, c = 0.95**, where the prediction is
  ρ_door = 1.08: clearly past the gate, not extreme.
- **The below-gate anchor.**  The campaign itself: lifo vs fifo at ρ = 0.80 is **0 of 8**
  significant (measured today, `s11_measure.py` on the campaign root).
- **The aisle ceiling does not bind.**  At 400k k\* = 28,800 / 2,864 = **10.1** ≫ 1.35.
- **Cost and space.**  A k = 1 400k fifo/lifo unit took 13–33 min.  8 units at k = 1.35 is
  about an hour.  1.2 TB free.
- **Scope.**  Spec `_churn_probe` (fifo and lifo cells, the winner pair plus the fifo rider,
  both stock modes), the reference catalogue (`--profiles-dir` as the campaign's), 40 batches,
  from an immutable snapshot of HEAD.  The gain-evaluator policies are left out: the
  evaluator is being re-thought (memory `gain-evaluator-to-be-replaced-approximate`), and the
  gate question needs only a velocity-blind pair.

## Registered predictions (400k, k = 1.35, c = 0.95)

| # | quantity | predicted | band |
|---|---|---|---|
| Q1 | trailers/day, days 20–39 | (16.6 − ½) × 1.35 + ½ = **22.2** | ±10% |
| Q2 | ρ_door late | 22.2 × 1.55 / 32 = **1.08**: the yard queue is UNSTABLE | mean wait of trailers arriving in days 30–39 exceeds days 5–14 by ≥ 4 h |
| Q3 | lifo vs fifo | not exchangeable past the gate: **≥ 2 of 4 fulfillment arms significant, lifo cheaper** (the 40k sign) | sign and count |
| Q4 | fresh-bin share (lines; units measured) | **store 10.2%, fulfillment 33.2%** (7.8% / 26.5% at k = 1) | ±25% relative |
| Q5 | store placement gap, uni rank vs uni fifo | −0.80% × (10.22 / 7.76) × 0.847 = **−0.89%** | ratio 0.7–1.3 |
| Q6 | aisle ceiling | store day-cut carry per picked unit stays below 0.5 (stable) | — |
| Q7 | store free pool | flat within ±2% over the window | — |

A fail on Q2 or Q3 would move the gate or break the exchangeability argument at scale.  A fail
on Q4 or Q5 would say the 40k laws do not transfer.
