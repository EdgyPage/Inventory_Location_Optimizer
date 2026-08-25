---
name: placement-pools-and-the-audit-point
description: Ranked placement functions were inverted from waves into pools so the drain owns put-away order; a033aff is the LAST byte-identical point and everything after re-derives published comparative claims
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T09:10:39.379Z
---

Until 2026-08-24 a ranked assignment function returned `[(unit, bin)]` **in its own priority
order**, so 14 of the 17 restock rules chose put-away ORDER as well as the bin. Put-away is
supposed to be FIFO with a little tolerance; instead the queue was re-sorted by whatever score
maximised the assignment function.

Inverted into pools (commits 524af01..b354b7c):

    pool = placement.open_pool(candidates, rep)   # one snapshot per BinKey group
    for unit in mgr._serve_order(pool, units, k, put_key):
        bin_, score = pool.take(unit)             # consumes; returns the score it chose on

A pool states precedence as `sort_key(unit)` (higher first, `None` = no opinion) and it is a
REQUEST — `Inventory_Manager._serve_order` is the single place the order is decided.

**Why:** the snapshot was never order-dependent (every unit in a group shares a BinKey), so
the window costs nothing extra; and the score has to be captured as the pool decides, because
there is no "score this bin for this unit" step to call afterwards. That is why
`bin_placement` now has `score`/`score_rank`/`policy`.

**How to apply:** `a033aff` is the audit point — the LAST commit with 959/959 table digests
identical to the pre-branch baseline. Anything after it re-derives every published comparative
claim, because a finite window changes which unit gets which bin. When comparing a new run to
the archive, check which side of a033aff it was produced on.
`Tests/integration/test_placement_pool_audit.py` holds the classification as a ratchet
(14 pooled / 3 per-unit / 0 wave); `place_wave` survives only to drive the frozen oracles.
Related: [[pickers-over-picked-until-planned]], [[k-oldest-bounds-lookahead-not-staleness]].
