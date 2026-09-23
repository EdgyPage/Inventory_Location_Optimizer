# S15 — the rule supplement, measured against its registered predictions

Three runs of `_churn_rules` (rank_popularity, rank_minlabor and map on both channels, fifo rider,
fifo/lifo unloading, both stock modes; 40k; c = 0.95) from snapshot `aefb5b73`.  Per-point JSON:
`assets/results/S15_r_k{1,3,10}.json`; ground shares by `assets/s15_ground.py`.

## Scorecard (store placement gap, uni rule vs uni fifo, mean of the two unloading cells)

| rule | k = 1 | k = 3 | k = 10 | prediction | verdict |
|---|---|---|---|---|---|
| rank_minlabor | −1.28% | −1.50% | −2.04% | ≈ cartlabor (−1.07 / −1.33 / −1.88) | **P7 ✓**: ratios 1.19, 1.13, 1.08 |
| rank_popularity | −0.55% * | −0.51% * | −0.64% | −0.25 / −0.31 / −0.43, not significant at k = 1 | **P8 ✗** on size: ~2× and significant; the sign is right |
| map | −0.38% * | −0.83% * | −1.42% * | at least cartlabor's magnitude at k = 10 | **P9b ✗**: weaker than cartlabor |

Fulfillment, every rule, every k: |gap| ≤ 0.17%, and none significantly negative for
rank_popularity.  **P11 ✓**: at 40 batches too little of the stock is rule-placed for
co-location to show (S07 needed the 100-batch fill).

**P10 ✓**: lifo vs fifo, 48 readings, **1** excludes zero (k = 1, fulfillment rank_minlabor,
−0.18%), inside the registered tolerance of one.  Exchangeability holds for every rule below
the dock gate.

## P9a — map's ground share against its occupancy

Ground share of fresh store placements by 10-day block, with σ_G at keyframe 25:

| rule | k | days 0–9 | 10–19 | 20–29 | 30–39 | σ_G |
|---|---|---|---|---|---|---|
| fifo | 1 | 21.7% | 19.0% | 21.5% | 21.2% | 20.7% |
| map | 1 | 33.3% | 23.4% | 29.6% | **24.6%** | 20.9% |
| map | 3 | 30.2% | 26.2% | 29.4% | **27.3%** | 21.4% |
| map | 10 | 27.0% | 24.0% | 23.3% | **20.0%** | 21.6% |
| rank_minlabor | 1 | 99.4% | 70.2% | 58.6% | 42.8% | 22.7% |
| rank_minlabor | 10 | 43.7% | 29.1% | 26.9% | 24.5% | 23.6% |

- At k = 1 and 3, map holds a ground share 4–6 points above σ_G.  It does not decay the way the
  velocity-blind rules do, which is the turnover signature.
- At k = 10 it falls to σ_G.  **Partial.**
- rank_minlabor's decay toward σ_G is the S09 frontier, compressed k-fold: at k = 10 it is
  within a point of σ_G by days 30–39.

## Diagnoses

- **P8, rank_popularity.**  The travel-only key still buys height, because a low row is near in
  y as well.  It also buys TRAVEL directly: nearest-D bins in the least-popular aisle.  The height
  chain prices neither the D saving nor the demand dispersal.  Revision: the rule's gap is height
  (φ·η·ΔM̄ at its own ground share, 47% → 28%) plus a travel term.  Named, not composed.
- **P9, map.**  Map's target is set ONCE, offline, from the declared demand
  (`build_optimal_map`), and each unit goes to the free bin whose preference score is closest to
  its target, not to the best free bin.  It is velocity-aware in WHICH SKUs get prime targets,
  but score matching spends good bins only on the SKUs whose target is good.  So it sits above
  σ_G without spending the transient the greedy rules spend.  The greedy height rules win the
  40-day window; map's advantage is its layout.  Its opt-vs-uni initial-layout value is −4.9% at
  k = 1, the largest of any rule, and it shrinks with churn: −3.1% at k = 3, −1.6% at k = 10.
  That is P5's (1 − φ) law.
- The turnover-aware rule the S09 frontier asks for is therefore not `map` as built: map is
  velocity-aware in its targets, not in its free-bin choice.  The S13 suggestion stands as a
  NEW design.
