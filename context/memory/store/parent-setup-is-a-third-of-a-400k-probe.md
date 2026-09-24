---
name: parent-setup-is-a-third-of-a-400k-probe
description: "A single-cell 400k run spends ~35% of its wall in the parent's warehouse freeze (19-23 min); the fill stamp + 12-point curve is memoised since a515a34d (3.3x), the 17-step line-floor solve is not"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-24T22:22:36.026Z
---

Measured 2026-09-24 (S00/S06 of `.scratch/inbound-fullscale-perf/`).  Before the first work
unit starts, a 400k run's parent freezes the 2.5M-bin warehouse: ~1,130 s idle, and
1,385 s for the coverage step alone under load.  That is **35% of a single-cell research
run's wall**.  At campaign scale it is small (0.35 h of 15.9 h), and the campaign's last
launch ran at 1.03x its slowest-unit bound, so dispatch order has nothing to win there
either.

Where the freeze goes, per channel:
- The **line-floor solve**: 17 `fill_rate` evaluations, 216-246 s.  NOT memoised.  Each
  evaluation moves the shelves, so the group memo misses.  A per-SKU memo would rely on
  einsum's per-row reduction being independent of group composition, which is unproven.
- The **fill stamp + 12-point transit curve**: ~300 s before commit a515a34d, which
  memoises per (group digest, grid day).  The twelve points' 1,887 passes collapse to 534,
  358 -> 109 s per channel, bit-identical.

**How to apply:** when a 400k probe's wall matters, count ~15-20 min of parent setup
before the sim stage.  The next exact saving in setup is the line-floor solve.  Before
building it, prove the per-row einsum independence it would need.
