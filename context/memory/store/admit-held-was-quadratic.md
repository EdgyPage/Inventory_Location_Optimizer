---
name: admit-held-was-quadratic
description: two failed attempts then a fix -- an exit counting EVERY queue is unreachable when one is idle; partitioning _held per queue took k 1.84 -> 0.94
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T05:20:07.750Z
---

`Inventory_Manager._admit_held` had no early break once every put queue had refused. `_stock`'s
refill loop calls it once per pass and needs roughly `work / staging` passes, so passes and the
held list grew together.

**What `68bf962` fixed, and it is real:** the work per touch. Exact `PutQueueSet.route()` counts
at 40 batches, `staging=4` — 677,845 → 42,736 at 300 SKUs, 27,248,644 → 283,774 at 2,400
(**96x at 2,400, but only 15.9x at 300** — the ratio grows with the catalogue). Wall 41.7 s →
21.7 s. Pinned by `Tests/unit/test_admit_held_is_linear.py`, which asserts the call count and,
separately, item-for-item equivalence against an exhaustive walk.

**CORRECTION (2026-08-26) — the growth term is NOT closed.** Two things were still wrong:

1. The original fix then did `still.extend(rest)` into a fresh deque, so every call still
   copied the whole held list. Fixed in `c6dd60f` — the deque is now mutated in place.
2. **The exit is unreachable in the split configuration.** It fires on
   `len(blocked) >= len(self.put_queues)` — *every* queue in the set. A store-only catalogue
   routes to `store_cart` and `store_pallet` only; `fulfillment` never receives anything, so
   `blocked` tops out at 2 of 3. Measured: 3 queues, 2 ever routed to, ~347 items examined per
   call, touches k = **1.81** against 1.82 before the exit existed.

**Blast radius is zero** until the split is enabled — `PUT_QUEUE_SPLIT = False`, all
`PUT_*_STAGING = None`, and no archived run has executed the path.

**CLOSED 2026-08-26, third attempt.** `_held` is now PARTITIONED per queue (`HeldItems` in
`put_queue.py`), so a full queue is skipped in O(1) and there is no exit condition left to get
wrong. Retry touches over 300/600/1,200/2,400 SKUs: 7,659 / 14,262 / 28,138 / 53,178,
**k = 0.937** against 1.840 — and the reduction grows with the catalogue (38x → 246x), which is
what distinguishes removing a growth term from removing a constant.

**How to apply:** a parallel per-queue census was tried between attempts 2 and 3 and
**rejected** — derived state that drifts from the deque, and a stale one makes the retry stop
instantly and livelock, worse than the slowness. Prefer a structure where the invariant is
intrinsic over one that must be kept in sync. And note the general shape: an exit condition
that asks about *every* configured resource is wrong when a resource can be permanently idle;
ask about the ones actually holding work. Also: a flag defaulting to `None` can hide a whole class of behaviour, so re-run the
ladder in the *new* configuration and on the **skus** knob
([[growth-ladder-use-the-skus-knob]]). Detail in `docs/design/STRESS_TEST_FINDINGS.md`.
