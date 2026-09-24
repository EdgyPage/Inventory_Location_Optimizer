---
name: sortmatch-two-sorted-lists
description: "rank_sortmatch (bc5671fe): packs by lifetime work onto bins by D + hbar M; store 0.8-1.0% below rank_cartlabor at k10, ff ties rank_minlabor, put-away 2.2x faster; the bin key (height term) is most of the gain"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-24T19:56:45.217Z
---

Measured 2026-09-24 (`.scratch/placement-sortmatch/`, S00-S09).

- **The rule**: a drain group's packs sorted by lifetime pick work visits x (Dbar + h Mbar) onto
  bins sorted by D + hbar M (hbar = catalogue demand-weighted line cost), index for index. The
  pairing is fixed per group in `_SortMatchPool.prepare` (called from `IM._stock_ranked`), so the
  store pallet queue's `k_cap = 1` FIFO service keeps the match.
- **Sim, 40k k10**: store pick labour 0.8-1.0% below rank_cartlabor (CIs exclude 0), 2.2-3.2%
  below tmin; fulfillment ties rank_minlabor (+-0.1%). Put-away drain 2.2x faster than the winner
  pair, ranking work above FIFO's floor 10-15x cheaper, whole arm 26-28% shorter.
- **The lab overstates magnitudes, not rankings**: separable-cost lab gave -3.4 pts store and ~-10
  pts ff over the live rules; routing keeps ~25% of the store gain and rank_minlabor's co-location
  cancels the ff one. The HEIGHT term in the bin key is worth ~3 pts of the lab gain; the pack key
  0.1-0.9.
- **Quantile over the free pool fails** (-1.4%): cheap bins are not scarce in steady state.
- **Early stopping does not transfer** to `_TravelBalancedPool` / `_MinLaborPool`: an exact bound
  still visits 75-100% of aisles (water-filling / affinity keep every aisle competitive).
- Lab anchor lesson: a replay lab's bin release rule must be "free for the same batch's drain"
  (0 violations); a one-batch lag handicapped every cheapest-bin policy by ~3 pts.

**Why:** the placement rule decision (replace the phase-1 winners?) and any further "faster
placement" work start here. **How to apply:** replacing rank_cartlabor/rank_minlabor or retiring
`plan_order` is the user's call; the arm has no gain adapter yet. Related:
[[unloading-order-is-the-turnover-lever]], [[breathing-room-frontier-law]],
[[colocation-needs-the-palm-probability]], [[placement-pools-and-the-audit-point]].
