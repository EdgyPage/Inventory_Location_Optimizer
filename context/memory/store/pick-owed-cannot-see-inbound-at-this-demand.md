---
name: pick-owed-cannot-see-inbound-at-this-demand
description: "Why the phase-2 era campaign's placement score spreads only ~0.1% across unload policies -- the score's arithmetic against a site whose window never re-asks for what inbound placed"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-22T21:48:39.909Z
---

Measured 2026-09-22 on the finished phase-2 campaign (`comparison_whatif_20260920_150203`,
11 cells, 44 units, done 2026-09-21), from the run's own tables, after the user asked
whether "something is wrong with the picking frequency". Since the same day the re-pick
shares are RECORDED by `run_unload_ranking` itself (`inbound_repick` per pair, cell and
channel, from the `pick_bins` named query joined onto `bin_placement`), so a future run
answers this without a one-off script.

**The score cannot move much, by construction against this demand.** `ss_pick_owed` is
`sum_s w_s * mean-bin-cost(s)`: a mean over each SKU's bins BY BIN COUNT, weighted by the
planned batches that ask for the SKU. Those weights are ~1 line for 92% of touched store
SKUs and 67% of fulfillment. One inbound pack lands among N = 4-8 existing bins (5-11
line-weighted) and moves its SKU's term by `w_s * (c_new - m) / (N+1)`. And most of what
inbound places is never asked for again inside the window:

| leaf | picked units served from inbound bins | inbound units placed, never picked | implied coverage |
|---|---|---|---|
| store | 8.7% | 91% | 477 d |
| fulfillment | 23.9% | 75% | 83 d |

90.6% of the store catalogue is never asked in 40 days; demand is near-uniform across the
touched set (fulfillment top decile 13% of lines vs the law's 21%,
[[sampler-affinity-flattens-the-fulfillment-line-rate]]). There is no head for a placement
policy to win on inside the window. The 0.143% fifo-vs-gforecast gap sits entirely in the
fulfillment leaf; the store leaf flips sign every few batches at +-0.05%, its noise. ~35%
of the gap was AVAILABILITY priced at zero (gforecast strands 170-250 more fulfillment
lines per batch unservable).

**Why:** [[pick-owed-s-replaces-flow-totals-for-unload-ranking]] explains why the FLOW total
tied exactly; this is why the STATE read nearly ties too. The metric is not broken -- the
site is under-demanded for the question, the same shape as
[[toy-fixture-cannot-discriminate-unload-policies]] one level up.

**How to apply:** do not tune the score's floor or basis to make the era run rank unload
policies (restricting the weight basis to inbound-touched SKUs was considered and dropped:
those SKUs still carry ~1 line and three quarters of the placed stock has none). The
question is re-asked as a fill trial (`.scratch/inbound-throughput/`, CONTEXT.md: Fill
trial), ranked on pick labour with realised future work as the diagnostic. The corrections
that DID land in `run_unload_ranking` (2026-09-22): the census is priced at the leaf's mean
priced line, the floor is measured per rule pair from the paired per-batch series (0.080% on
the winner pair, 0.017% on the rider, against the declared 0.1%), the rider is a control
(CONTEXT.md: Rider), and the metric is declared on the spec.
