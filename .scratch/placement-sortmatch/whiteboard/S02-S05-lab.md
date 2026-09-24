# S00, S02-S05 -- the placement lab: which "two sorted lists" rule, and does it hold up

Registered in the approved plan (`~/.claude/plans/tender-roaming-dream.md`, 2026-09-24)
before any lab ran: S00 every arm re-derives its recorded bins (any mismatch stops the
study); `rank_minlabor` smallest gap to the exact optimum; S02 billing-consistent keys
halve tmin's height residual; S03 an exact per-group solve at < 1 ms per group, dropped if
it beats A1 by < 0.1%; S04 online quantile match at K = 16 within 1% of the offline
sort-match and better than `map`; S05 sort-match RAISES the busiest aisle's load.

Instruments (all read-only on a finished `_churn_probe` root; here the 40k k10 root
`comparison_whatif_20260923_104100`):

* `assets/lab.py` -- the STATIC lab: one drain = a tier's free bins at the batch-25
  keyframe plus the packs that arrived for it in the next batches; per-period billing
  cost C = alpha D + beta M (alpha = f, beta = f (I + E[q](p + v))); the exact optimum by
  scipy over the n cheapest bins of each height bracket (a proven-exact reduction).
* `assets/dyn.py` -- the DYNAMIC lab: the arm's recorded arrival stream over batches
  26-39, tier by tier, FIFO; every pack keeps its REAL lifetime (per-bin timelines built
  from `picks`, bounded by the next placement into the same bin) and is scored on its
  REAL pick lines and units at whatever bin the policy gives it:
  L D_b + (L I + Q (p + v)) M_b.  Policies see only lawful inputs (catalogue f and
  lambda, the tier's history before the keyframe).

## S00 -- anchors (the lab reproduces the run)

The plan's anchor (every arm re-derives its bins) needs the pools' full ledger state and
was replaced by two checks the lab CAN make:

1. **The static lab prices the recorded fifo placements where the uniform matcher lands:**
   +15.8% vs the optimum recorded against +15.5% for the matcher (3 batches, store).
2. **The dynamic lab's occupancy model agrees with the run.**  Replaying an arm's own
   recorded bins through it (`assets/s04_fidelity.py`): with the release rule "a bin
   picked out in batch t is free for batch t's drain", **0 of 25,817** recorded
   placements land in a bin the model holds occupied; with a one-batch lag, 8.6%.  The
   first version of the model (a per-placement pick clock) had 6-12% violations and
   HANDICAPPED every cheapest-bin policy against the recorded arms by ~3 points -- found
   because `recorded` beat every lab policy on its own stream, fixed, re-measured.
   After the fix, recorded fifo = **-0.07%** against the uniform draw on its own stream.

## S02 / S03 -- static: sort-match against the exact optimum

Store, one arm's drains (uni_fifo stream, 3 batches, 5,000+ packs over 30+ tiers):

| matcher | vs exact | prize kept | us/pack |
|---|---|---|---|
| uniform (fifo) | +15.5% | 0% | 1.7 |
| tmin (f x labor onto D; the merge rung) | +10.4% | 33% | 0.4 |
| sort-match, key f, bins D + hbar M | +3.0% | 80% | 0.4 |
| **sort-match, key f (Dbar + h Mbar)** ("highest expected pick cost first") | **+0.16%** | **99.0%** | 0.4 |
| bracket-aware heuristic | +0.26% | 98.3% | 0.5 |
| exact (scipy) | 0 | 100% | 1,433 |

On the `rank_cartlabor` arm's drains: that arm's RECORDED bins sit at +1.19% (90.5% of the
prize), sort-match-by-cost at +0.16%.

* S02: **confirmed and exceeded** -- the billing-consistent keys close 97% of tmin's gap,
  not half.  Most of it is the bin key: D alone ignores the height multiplier, which is
  the larger term in the store (M in 1.0-1.4 on an at-location cost ~15 s).
* S03: the exact solve is exact (by construction) but costs ~3,500x the sort per pack and
  buys 0.16 points; the solver-free bracket heuristic is WORSE than plain sort-match on
  this data.  By the registered rule A2 SURVIVES (it beats A1 by 0.16%, over the 0.1%
  bar).  It is **not wired** anyway, and that is a departure from the rule, stated: the
  0.16 points are in the static frame, which S04 shows ranks rules differently from
  realised work, at ~3,500x A1's cost per pack.  It stays the exact reference.

## S04 -- dynamic: the online question

Store, realised pick work against the uniform draw on the same stream (26k packs):

| policy | uni_fifo stream | uni_rank_cartlabor stream | opt_rank_cartlabor stream |
|---|---|---|---|
| recorded (the arm's own bins) | -0.07% | -6.93% | -7.06% |
| cheapest free bin by D + hbar M (velocity-blind) | -9.49% | -10.24% | -10.01% |
| batch sort-match, tmin's keys (f x labor onto D) | -6.77% | -7.38% | -7.90% |
| quantile of the FREE pool (A3a) | -1.41% | -1.31% | -1.21% |
| ideal slot + nearest free (the `map` idea) | -1.60% | -5.34% | -6.62% |
| **batch sort-match, key = lifetime visits x (Dbar + h Mbar), bins D + hbar M** | **-10.45%** | **-10.39%** | -10.05% |
| batch sort-match, key = per-period cost | -10.42% | -10.29% | -10.08% |
| online scaled quantile, W = batch/4, visits key | -9.87% | -10.58% | **-10.22%** |
| online scaled quantile, W = batch | -9.43% | -9.47% | -9.20% |

**S04 as registered is falsified** -- and informatively:
* Quantile-of-the-free-pool is nearly worthless (-1.2 to -1.4%).  In steady state the
  free pool is large; spreading packs over it by velocity puts a median pack in a median
  bin while cheap bins stand empty.  Cheap bins are not the scarce resource at this
  occupancy, so "reserve them for future hot packs" loses.
* The fix is to scale the shared index to the part of the cost order that actually turns
  over -- about a quarter of a batch's arrivals (`qscale`): the pack at quantile q takes
  the q*W-th cheapest free bin.  That online rule matches the batch sort-match on the
  store (-9.9 to -10.6%).
* The ranking of keys hardly matters (-10.29 vs -10.45); what matters is the BIN key.
  tmin's keys (D only) lose ~3 points to the same sort with the height term.
* Against the live rules: sort-match with the height-aware bin key beats the recorded
  `rank_cartlabor` by ~3.4 points of realised store pick work, and costs ~15 us per pack
  in Python here against the travel-balanced pool's per-boundary aisle scan.

**Fulfillment** (M = 1 everywhere, so the bin key is travel alone; 217k packs per
stream, `results/s04_dyn_k10.txt`):

| policy | uni_fifo stream | opt_rank_minlabor stream |
|---|---|---|
| recorded | -0.10% | **-10.92%** |
| cheapest (FIFO onto D) | **-22.79%** | -19.67% |
| batch sort-match, tmin's keys | -18.34% | -18.59% |
| batch sort-match, per-period cost key | -18.47% | -18.80% |
| **batch sort-match, lifetime visits key** | -20.93% | **-20.63%** |
| online scaled quantile, W = batch/4 | -21.51% | -18.50% |
| quantile of the free pool | +1.73% | +1.11% |

* Every travel-ordered policy roughly DOUBLES the recorded `rank_minlabor`'s reduction on
  the separable objective (-19 to -23% against -11%).  `rank_minlabor` spends travel on
  co-location (the Palm-probability term, aisle-churn S07), which this objective cannot
  credit -- the fulfillment question is exactly the one S07 must answer.
* The pack key matters more here than in the store: lifetime visits beats the per-period
  rate by ~2 points; and on the fifo stream plain FIFO-cheapest beats every velocity
  ordering.  Velocity-awareness is not reliably worth anything in fulfillment on this
  objective.

**Replicated on the second cell** (`k1_off_lifo`, the same grid, independent streams):
store sort-match (visits) against the recorded arm -10.01 vs -6.95 (opt_rank_cartlabor),
-9.00 vs -6.41 (uni_rank_cartlabor); fulfillment -18.91 vs -9.33 (opt_rank_minlabor),
-21.75 vs -11.17 (uni_rank_minlabor); recorded fifo -0.07% / -0.35% against the uniform
draw (the anchor holds).  Every ranking in the tables above repeats.

## S05 -- the aisle ceiling

Realised pick LINES per aisle over the window (`assets/s05_aisles.py`, store,
uni_rank_cartlabor stream):

| policy | busiest aisle | max / mean | top-5% share |
|---|---|---|---|
| recorded (rank_cartlabor) | 976 | 12.7 | 45.7% |
| uniform | 979 | 14.0 | 47.2% |
| cheapest | 922 | 12.8 | 43.3% |
| batch sort-match (visits) | **880** | 12.2 | 43.1% |

**S05 falsified in sort-match's favour**: it LOWERS the busiest aisle's load.  D orders
bins front-to-back WITHIN every aisle, so the cheapest bins are spread across all the
tier's aisles; the busiest aisle is set by tier structure (one-aisle tiers), not by the
rule.

## What the lab cannot see (why S07 exists)

The objective charges each line D_b + M_b h: travel as if every pick were its own trip.
The simulator routes pickers through aisles, so co-location and aisle-visit consolidation
(what `rank_cartlabor`'s balance and `rank_minlabor`'s affinity terms buy) are invisible
here, as are cart swaps, congestion and put labour.  The lab ranks rules on the
separable part of the cost; only the simulation can price the rest.

## Next

S06: wire the finalist -- batch sort-match with bins by D + hbar M and packs by lifetime
visits x (Dbar + h Mbar) -- as an additive arm, and run it against rank_cartlabor,
rank_minlabor, tmin and fifo in S07.
