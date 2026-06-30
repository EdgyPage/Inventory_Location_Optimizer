# GPU auction placement — measured assessment (prototype)

**Verdict: NO-GO for a custom GPU auction placement solver.** Prototype only; nothing is wired into the
simulation. Source: `Optimization/gpu_auction.py`, `Tests/test_gpu_auction.py`,
`Tests/bench_gpu_auction.py` (RTX A5500, structured placement cost
`cost[u,b] = f_s·(M_b·(intercept+v_s) + D_b)`).

## What we wanted

GPU gains for item placement by modelling each wave as a linear assignment (U units → B≥U bins) and
solving it with a parallel auction (prices resolve bin clashes without eviction cascades). The static
cost is the same bilinear form `inventory_optimal.py` already feeds to `scipy.linear_sum_assignment`;
the path-dependent affinity/centroid terms would be folded in by an iterated (QAP) loop.

## Measured

```
Small sizes — auction correctness, quality vs greedy, and the round-count speed killer:
   U     B   scipy s  npauc s  auc rounds   gpu s  greedy s  optimal?  quality(auc vs greedy)
  100   800     0.004     1.56     58,826    61.73    0.000     yes      +19.0% cheaper (9479 vs 11707)
  200  2000     0.041    24.39    195,898      —      0.001     yes      +15.1% cheaper (18093 vs 21307)

Scale sizes — scipy LAP vs greedy (auction skipped: single-eps impractical here):
   U      B   scipy s  greedy s   scipy quality vs greedy
  1000  10000    5.804    0.015    +15.0% cheaper (opt 92325 vs gr 108576)
  2000  40000   97.196    0.088    +10.7% cheaper (opt 146666 vs gr 164295)

Affinity fixed-point (auction_place_wave): objective 11499 → 11502 → 11466 → 11452 → 11482 → 11478;
  `changed` ≈ 120 every round, never → 0  (OSCILLATES — does not converge).
```

## Findings

1. **Optimal placement is worth ~10–19%** over the sequential greedy on the static cost (the auction
   matches scipy's optimum exactly). The quality headroom is real.
2. **GPU auction is catastrophic.** Single-eps needs ~10^5 bidding rounds on structured costs (many
   near-tied bins ⇒ tiny price increments), and on GPU each round is a kernel launch ⇒ **61 s at
   100×800**. GPU is decisively the wrong tool for the iterative auction.
3. **scipy LAP also collapses at scale on structured costs** — ~6 s at 1000×10000, **~97 s at
   2000×40000** (it is only fast at small U, or on uniform-random costs). So "just lift the `U≤1200`
   cap and use scipy" does **not** scale to large BinKeys/waves; the exact solvers all blow up on this
   cost structure.
4. **The greedy is fast at every scale** (~0.09 s at 2000×40000) — which is exactly why production uses
   it — at the cost of the 10–15% above.
5. **The affinity fixed-point oscillates**, it does not converge. The quadratic co-location term is not
   tamed by naive linear-solve iteration (would need damping or real QAP machinery).

## Recommendation

- **Do not build a GPU auction.** It is the worst option measured.
- **Do not rely on scipy LAP at scale** either — it is impractical on the realistic cost beyond small U.
- **Keep the fast greedy for the hot path.** Capturing the 10–15% at scale is an unsolved, expensive
  problem, not a quick GPU win.
- The only untested GPU-viable candidate is **Sinkhorn / entropic optimal transport** (a fixed number
  of dense matmul iterations, not 10^5 tiny rounds) — but it is approximate, needs a rounding step, and
  the affinity QAP problem remains. Treat it as a separate research spike, not a quick win.

The prototype's purpose was a grounded go/no-go; it delivered one and saved building a dead-end arm.
The dormant GPU broker (`Optimization/gpu_broker.py`) likewise stays dormant — placement is not its use.
