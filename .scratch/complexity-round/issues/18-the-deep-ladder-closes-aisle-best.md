# 18 - the deep ladder closes `_aisle_best`, refutes the cluster lead, and corroborates cmin

Type: finding
Status: resolved

## Context

R4: the full 8x span (10,000 -> 80,000 SKUs, 136 arms, 5 rungs, 1 h 45 m) on the post-fix code,
with the per-arm instrument added the same morning. It reported **29 of 34 arms accelerating**,
with the jump at the SAME rung for nearly all of them.

## The 29 were two defects, both mine

**A row collapse.** `runtime_metrics` is unique on (cell, pair, config, channel, arm), so 136 rows
hold 34 arm NAMES, four rows each. `per_arm_total_s` was a dict comprehension keyed on the name and
kept whichever row iterated last - three quarters of every run discarded in silence. The only
symptom was a printed 34 directly beneath a rollup saying 136.

**A save knee.** `save_s` ran 35.9% -> 48.6% of total with local exponents 1.27, 1.06, 0.97,
**2.30**. One shared I/O step at the top rung:

| series | flagged accelerating | median last local k |
|---|---|---|
| `total_s` | 33 of 34 | 1.68 |
| `total_s - save_s` | 22 of 34 | **1.09** |

Both fixed in `0427c91c`; the archived artifact was repaired from the run trees before they were
deleted, and now reconciles per-arm sums against `total_s_sum` - a cross-check the collapsed
version could never have passed. Flagged arm-totals: 29 -> **4**.

## Answer

**CLOSED - `_aisle_best`.** `_TravelBalancedPool` backs exactly `rank_labor` and `rank_cartlabor`.
Save excluded, both are linear at deep scale: k = 0.98 and 1.01. The `R x A` run-boundary rebuild
does not become a complexity problem at 80,000 SKUs. Ticket 07's measurement stands at 2.66%;
ticket 14's retraction of the attribution is now **permanent**. Do not re-open without a new
measurement.

**REFUTED - the cluster-family lead** (ticket 15's deep-scale half). Stated AS a lead because a max
over a migrating argmax is not any arm's growth curve, and it was not: most of the 36 -> 411 s was
the save knee plus the argmax wandering. `uni_cluster_map_norsl` is linear at deep scale (k = 1.04).

**CORROBORATED - the co-demand pair.** `cmin` and `cmax` are the only arms still superlinear once
the confound is gone, at k = 1.26-1.29 across both uni and opt variants. That agrees with the meso
cell's conviction of `score_of` at k = 1.98 - a different instrument, a different tier, a 10x larger
catalogue. Two measurements that could each have been wrong alone. This is the strongest evidence
the round produced, and it points at ticket 17's second half.

## Not claimed

The fused pass is -34% on the meso `cluster_map` cell and does **not** visibly move deep-scale arm
totals (71-145 s per rung either side). Two honest explanations - `save_s` is 40-48% of an arm's
total at deep scale, and the deep workload's aisle counts per placement differ from the meso
fixture's - and no way to separate them without another 1 h 45 m. The meso win is measured; the
deep number is neutral, not confirmatory.

## Left for someone

`save_s` is a real superlinearity with no owner: local k 2.30 at the top rung, on 48.6% of the run.
It is I/O rather than placement, so it is outside this round's brief, but it is the largest single
growing term in the deep tier.
