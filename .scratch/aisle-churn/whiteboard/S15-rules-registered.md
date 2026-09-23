# S15 — the plan's other placement rules, registered before running

**Question.**  The plan's grid named fifo, rank_popularity, rank_minlabor and map; S11 ran only the
winner pair (store rank_cartlabor, fulfillment rank_minlabor) and the fifo rider.  Do the S09
mechanisms carry to the other rules?  Spec `_churn_rules`: the three rules on both channels plus
the fifo rider, fifo and lifo unloading, both stock modes, k ∈ {1, 3, 10} at c = 0.95 — below
the dock gate (ρ_door ≤ 0.44) and the aisle ceiling (k* = 12.8).

**What each rule's key says** (read off the code, not fitted).

| rule | how it picks a bin | what S09 predicts |
|---|---|---|
| rank_minlabor | the height-aware bracket key M·h + D (`_aisle_best_cost`), with its own aisle choice | the same frontier as cartlabor |
| rank_popularity | the nearest-D bin in the least-popular aisle: travel only, no M·h | the frontier with a D-only key: ① in S09, whose mean ΔM̄ over 40 days was about 0.23 of cartlabor's |
| map | the free bin whose preference score D + M·(t₀ + h̄) is nearest the SKU's offline target | **velocity-aware**: fast SKUs get prime targets, and their fresh packs are re-picked soonest, so the good bins turn over faster than their occupancy share |

## Registered predictions

| # | quantity | predicted | band |
|---|---|---|---|
| P7 | store gap, rank_minlabor vs fifo (uni) | ≈ cartlabor's measured gap: −1.07% / −1.34% / −1.88% at k = 1/3/10 | ratio 0.7–1.3 |
| P8 | store gap, rank_popularity vs fifo | ≈ 0.23 × cartlabor's: −0.25% / −0.31% / −0.43% | not significant at k = 1 |
| P9a | store, map: ground share of fresh placements in days 30–39 | above the occupied ground share σ_G at the window's second keyframe | s > σ_G |
| P9b | store gap, map vs fifo at k = 10 | at least cartlabor's magnitude | ≤ −1.9% |
| P10 | lifo vs fifo, every rule and stock mode at every point | 0 intervals excluding 0 | at most 1 of 48 |
| P11 | fulfillment gap, rank_popularity vs fifo | ≥ 0: it disperses co-drawn SKUs, so the script form's aisles-opened rises (S07) | not significantly negative |

A failed P9a would refute the turnover argument for velocity-aware rules.  A failed P8 would say
the travel-only key buys height anyway.
