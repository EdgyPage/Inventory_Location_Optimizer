---
name: admit-held-was-quadratic
description: _admit_held walked the whole held list on every refill pass; the early exit cut route() calls 96x and only became reachable when staging was set
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T02:38:14.502Z
---

`Inventory_Manager._admit_held` had no early break once every put queue had refused. `_stock`'s
refill loop calls it once per pass and needs roughly `work / staging` passes, so passes and the
held list grew together — O(H·Q) per pass, quadratic overall.

Measured at 40 batches, `staging=4`, exact `PutQueueSet.route()` call counts:

| SKUs | 300 | 2,400 | exponent |
|---|---|---|---|
| before | 677,845 | 27,248,644 | k = 1.784 |
| after | 42,736 | 283,774 | k = 0.914 |

96x fewer calls; wall 41.7 s → 21.7 s. Fixed 2026-08-25; pinned by
`Tests/unit/test_admit_held_early_exit.py`, which asserts the **call count** (exact,
deterministic) and separately that the early exit matches an exhaustive walk item-for-item and
in order.

**Why it hid so long:** the defect is unreachable with `staging=None` — the held list is always
empty and the refill loop runs exactly one pass. It only became live when the split put-queue
configuration became selectable with real staging limits.

**How to apply:** a flag whose default is `None` can hide a whole class of behaviour. When
making one selectable, re-run the growth ladder in the *new* configuration — and on the
**skus** knob, per [[growth-ladder-use-the-skus-knob]].
