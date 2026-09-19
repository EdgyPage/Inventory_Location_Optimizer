---
name: pool-open-was-a-tier-rebuild
description: "Ticket 03 (phase-2 campaign, 2026-09-19): under the gain evaluator every pool open rebuilt a whole tier's ranked structure (~4,100 bins) to seat ~12 units, 45-54 s of a 65-72 s drain at campaign scale, though the space view is frozen per drain and D is geometry. Fixed as a per-drain FrozenTier + per-open TierSlice overlay (Warehouse/placement/frozen_tier.py), byte-identical because the pools' aisle/bracket ORDERS are tie-breaks recomputed from the FILTERED first appearance. The live aisle index was deliberately NOT reused: its equal-D order is insertion time, not the snapshot's appearance."
metadata:
  type: project
---

**The shape:** the evaluator's `_place_pool` handed each pool a freshly filtered candidate
list and the pool built its D map, per-aisle (per-bracket) stable D-sorted buckets and heads
from scratch -- O(bins log bins) per open, ~7,400 opens per priced drain, ~12 units seated per
open. Measured (200k SKUs, `gain_myopic`, uncoupled, cProfile): `_TravelBalancedPool.__init__`
45 s of a 65 s drain (`rank_cartlabor`), `_RankedAssignPool.__init__` 54 s of 72 s
(`rank_random`). The inputs never change inside a drain (frozen `SpaceView`), only the taken set.

**The fix (commit `a58f67c2`):** `FrozenTier` per (key, predicted) owned by the `_Evaluator`
(the drain-scoped object, see [[a-cache-needs-a-scope-object]]), built through the bundle's
`freeze_tier` (driver-injected like `wp_of`, since `Inbound` may not import placement);
`TierSlice` per open with lazy-skipping `_Cursor` buckets speaking the pools' deque/bucket
protocol. All three pool classes accept a slice or a list.

**Why it is byte-identical, and the trap:** filtering a stable-sorted list equals stable-sorting
the filtered list, so a cursor yields the eager sequence. But the pools' dict ORDER of aisles
(and of brackets within an aisle) is a real tie-break (`_rank`, `next(iter(head_bin))`, strict
`<` over brackets) and it is the first appearance in the FILTERED list: excluding an aisle's
first bin can move that aisle behind another. The slice recomputes it per open from each
list's first non-excluded appearance index. The user's "arm the live aisle index" was NOT
taken for the same reason: `_aisle_index` orders equal-D bins by insertion time, and a pool
opened over it would differ on ties. Python's `reverse=True` sort is stable too (tmax): a
descending stable sort is not the reversed ascending one.

**How to apply:** the gate is `Tests/unit/test_frozen_tier.py` (54 exact cases, in the
CLAUDE.md gate) + the `_toy_priced` digest, and at campaign scale `run_digest.py --cell`
against the stopped campaign's finished cells via the `_probe_unload_ref` spec (two cells,
not one: a one-cell run samples fresh instead of freezing). Prove the path RAN (count opens
that received a slice) before believing IDENTICAL -- [[priced-rank-random-cost-was-the-cow-union]]
and [[toy-run-is-the-byte-identity-instrument]].

**CORRECTION, same day.** The open was the dominant term of the UNCOUPLED store-only profile
and NOT of the campaign's coupled priced unit: the probe's priced cell paced no faster after
the overlay, and a COUPLED profile (`run_fullfid(coupled=True)`) put the drain in `take` --
per-SKU-run work across every live aisle, 29 M `_aisle_best` evaluations for the travel-balanced
pool, and for the min-labor pool the partner row rebuilt per unit, `per_pick` per aisle per
bracket, and the affinity delta folded per aisle. Cut 2 (heads as attributes, `per_pick`
memoised, score inlined): cartlabor drain 213 -> 137 s. Cut 3 (row once per run, deltas folded
once through the ledger's inverse, `_partner_deltas`): minlabor drain 634 -> 346 s. The wave
path benefits from cut 3 too. Lesson: **profile the coupled unit** -- the fulfillment leaf places
18k units a day against the store's 2.8k, so a store-only profile mis-ranks the terms.
