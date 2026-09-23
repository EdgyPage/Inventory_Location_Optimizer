---
name: fresh-bin-law
description: "An inbound decision can only reach picks that follow an earlier line of the same SKU by a lead -- ~8% of store picking, ~24% of fulfillment's at declared demand over 40 days; demand density is the lever (store ~69% at 30x)"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T15:35:11.567Z
---

Verified 2026-09-23 (aisle-churn S08, `.scratch/aisle-churn/whiteboard/S08-fresh-bin-law.md`).
On the floor every line fires a lot of exactly what it took, and the drain is smallest-on-hand
first (ADR-0003), so a SKU's NEXT line is served from the fresh packs while the declared bulk
waits. A pick is therefore served from an inbound-placed bin iff an earlier line of its SKU fell
at least one order-to-shelf lead (~2.77 batches) before it.

- Unconditional: E[served] = x - (1 - e^-x), x = lambda (H - l). Count-conditional (for a
  retrodiction on a known script): sum_j (1 - I_{l/H}(j-1, n-j+2)). Plugging a REALISED count in
  as a Poisson rate is biased 4x (convexity) -- the first read got 36-45% against 9-24%.
- Code: `Optimization/simconfig/models/churn.py` (FRESH, section_phi, served_given_count).
- Units measured run ~+1 point (store) / -3 points (fulfillment) against lines predicted.
- Use the SAMPLER's rates, never the line share: declared line share gives store 5.7% vs 8.2%
  realised, fulfillment 30% vs 24% (S01 affinity lift, [[sampler-affinity-flattens-the-fulfillment-line-rate]]).

**Why:** this is the ceiling on everything inbound can move; it is why the unloading-order
campaigns were null ([[pick-owed-cannot-see-inbound-at-this-demand]], [[inbound-needs-churn-before-unload-order-matters]]).
**How to apply:** before designing an inbound/placement experiment, compute phi at the planned
demand; below ~20% no inbound decision can move pick labour past the noise floor. See
[[breathing-room-frontier-law]].
