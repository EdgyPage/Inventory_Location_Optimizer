---
name: unloading-order-is-the-turnover-lever
description: "User framing 2026-09-23 -- real puts run in FIFO order (+- a few placements), so \"fast packs to the best bins\" can only be steered by the UNLOADING ORDER; multi-picker aisles are a non-issue for now"
metadata:
  node_type: memory
  type: user
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T19:57:28.504Z
---

Decisions and framing the user gave on 2026-09-23, answering the aisle-churn study's proposals:

- **The turnover-aware placement rule IS the unloading-order question.** In a real building put-away
  operates on FIFO order, plus or minus a few placements (cf. [[k-oldest-bounds-lookahead-not-staleness]]),
  so a placement rule cannot freely send fast packs to prime bins; the only lever that can is the
  order trailers/pallets are unloaded in, feeding the put queue when good bins are free, "to
  maximally utilize space". Frame any velocity-aware placement idea as an unloading-order policy
  under a FIFO+-K put, not as a free-choice placement rule.
- **Multi-picker aisles / smaller store aisles: not simulated now, "perhaps in the distant future",
  a non-issue.** Do not propose lifting the k* ~ 12.8 aisle ceiling ([[aisle-ceiling-and-dock-gate]]);
  keep demand-density experiments below it.
- **400k confirmation runs: approved "when all available ducks are in a row"** -- i.e. register
  predictions, check the gates/ceilings/disk/time, snapshot, then launch without asking again.

**How to apply:** the closed-form prize of such an order is the rearrangement bound
(`models/churn.rearrangement`: velocity-blind orders exchangeable, E = 0) plus any turnover lift of
the good bins (`churn.STEADY`: s* = n nu / sum n nu); see [[breathing-room-frontier-law]].
