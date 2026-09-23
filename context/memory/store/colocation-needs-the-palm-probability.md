---
name: colocation-needs-the-palm-probability
description: "Fulfillment's placement lever is co-locating co-drawn SKUs; the expected-travel chain's independent-visit tasks form INVERTS it (says rank_minlabor opens 3-13% more aisles; the script form says 17-20% fewer) -- price a location with the Palm probability P0_a(s)"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T18:06:40.501Z
---

Measured 2026-09-23 on the 40k fill root (aisle-churn S07, `.scratch/aisle-churn/whiteboard/S07-location-value.md`).

Every fulfillment bin is at M = 1 on a one-way lane, so placement can only change which aisles a day
opens. `expected_travel` counts tasks as sum_a (1 - exp(-Lambda_a)), i.e. lines independent. The
sampler draws affine SKUs TOGETHER, so a rule that co-locates partners opens fewer aisles than that
form can see -- and the independent form reads the concentration as MORE aisles:

| arm (keyframe 100/125) | independent | script-conditional | realised tasks |
|---|---|---|---|
| fifo | 94.2 / 105.1 | 93.6 / 104.8 | 156.4 / 156.1 |
| rank_minlabor | 106.4 / 108.8 | 77.4 / 84.3 | 144.2 / 147.4 |

Realised tasks fall about half the distinct-aisle reduction (cart-full returns scale with aisle
load); priced at ~93 s travel per task that bounds the gap at -2.6/-3.2% vs -2.2/-1.6% realised.

**Why:** any evaluator pricing a fulfillment placement through independent visit probabilities
(the gain evaluator, `put_site_pricer`, the record's s_pick) ranks co-locating rules wrongly.
**How to apply:** `Optimization/simconfig/models/pick.py` -- `tasks_script`, `palm_open`
(P0_a(s), conditioned on s being drawn), `colocation_delta`, `location_value` (Mecke form
g_b = lambda[h_b + P0 T_new + (1 - P0) E[dT | open]]). Related: [[fresh-bin-law]],
[[expected-travel-closed-form-is-an-asymmetric-check]], [[sampler-affinity-flattens-the-fulfillment-line-rate]].
