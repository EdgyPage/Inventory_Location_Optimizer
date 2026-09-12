---
name: fulfillment-fill-law-overpredicts
description: "Fulfillment realizes a 0.104 missed share against a stamped 0.025 and the LEAD is not the cause — the record's own curve prices that level at a ~9.2-day lead, which no leg of the pipeline reaches"
metadata: 
  node_type: memory
  type: project
  originSessionId: b17618e8-09d8-47fe-a878-b27a525299ca
  modified: 2026-09-12T11:50:40.365Z
---

Measured 2026-09-12 on the coupled pilot gate (inbound-optimization 26,
`comparison_20260912_055947`, 40 era days on the reference `lt0` pair, days 20-39, all four
arms agreeing to the third digit). The fulfillment leaf's supply clause fails at **0.104
against a stamped 0.025**; the store leaf, same site and same run, passes at 0.030 against
0.025.

**Why this is worth remembering:** the obvious diagnosis is wrong, and it is wrong in the
direction the previous investigation already went. Ticket 23 read the same clause out of band
and found the cause — the coverage record stamped no inbound lead
([[inbound-lead-is-not-in-the-coverage-record]]). That is FIXED and the fix works: both leaves
now realize the stamped 1.766 d (1.782 / 1.789 by Little's law over `in_transit_qty` /
`units_ordered`), so the explained level `1 - fill(realized)` is 0.025 and explains none of the
excess. Reaching for the lead a second time costs a run.

**The falsifier, and the cheapest way to re-establish it:** read the record's own
`staffing.calibration[<pair>].coverage.final.<ch>.fill.vs_transit` curve out of `run_spec.json`
and find where the realized missed share lands on it. 0.1044 sits at a transit of **~9.2 days**
(0.09353 at 8.166 d, 0.11986 at 10.721 d). Nothing in the run is within a factor of three:
transit is 1.78 d, the yard's detention is ALREADY inside `in_transit` (`TrailerTransit.
merchandise()` sums `_yard`), the dock floor is empty overnight (`recv_depth` max 0), and
double-counting the whole yard on top reaches only ~3 d, which the curve prices at 0.039.

**How to apply:**
- Do not attribute this gap to the lead, the yard, or the channel coupling. Store is the
  control that rules out coupling — same dock, same shared put pool, same realized lead — and
  coupling moved fulfillment the RIGHT way (0.148 -> 0.104) against the uncoupled reading.
- The open question is the coverage FORM for fulfillment, owned by department-calibration
  (ticket 38), not by the inbound maps.
- Leading hypothesis, not a finding: [[sampler-affinity-flattens-the-fulfillment-line-rate]] —
  a per-SKU transient priced at the line share under-covers the busy SKUs, and fulfillment's
  curve is where that bites (missed share 0.006 -> 0.120 across the transit range, against
  store's 0.021 -> 0.044).
- The whole inbound campaign holds behind it, and so does fixing `PHASE2_THRESHOLD_DAYS`
  ([[site-dock-is-shared-across-channels]]): closing the gap raises fulfillment's served units
  ~8%, ordered units follow, and the dock's detention distribution moves with them.
