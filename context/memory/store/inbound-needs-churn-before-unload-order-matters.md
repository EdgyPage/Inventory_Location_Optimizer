---
name: inbound-needs-churn-before-unload-order-matters
description: "unloading order cannot move placement where aisles don't churn; measured twice (era run, 40k fill), and the user's 2026-09-23 re-think starts from churn"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T15:36:34.706Z
---

Two independent measurements say an unloading policy has nothing to decide at this site's demand:

- **Era campaign (2026-09-22):** one inbound pack lands among 4-8 existing bins and moves the
  placement score by 1/(N+1) of a line; 91% of the store's inbound units are never picked in the
  40-day window.
- **40k fill trial (2026-09-23, `comparison_whatif_20260923_023109`):** even lifo against fifo
  sits inside the measured noise floor on pick labour (-0.175% / +0.013% vs 0.24%), while yard
  overage separates 8 vs 68 trailer-days; only 7.7% of the store's placements are picked again
  within 40 batches.

The user closed the inbound-throughput map on 2026-09-23 and is re-thinking the evaluator from the
problem statement. Their direction: **the aisles must churn enough to give bins breathing room**,
so an inbound decision has contended placement to act on.

**Why:** a faster or more exact evaluator is worthless where the placement it chooses is never
read by demand; that was the dead end the map ran into.
**How to apply:** before building or ranking any unloading policy, check that the scenario
churns -- the share of placed units picked again inside the window, and bins freed per day --
not only yard depth. Related: [[pick-owed-cannot-see-inbound-at-this-demand]],
[[fill-trial-driver-mode]], [[gain-evaluator-to-be-replaced-approximate]].

**Follow-up (2026-09-23):** the aisle-churn campaign quantified this ceiling. See
[[fresh-bin-law]] (an inbound decision can only reach picks that follow an earlier line of the
same SKU by an order-to-shelf lead) and [[breathing-room-frontier-law]] (a velocity-blind ranked
rule converges to its own occupancy share; only a turnover differential beats it).
