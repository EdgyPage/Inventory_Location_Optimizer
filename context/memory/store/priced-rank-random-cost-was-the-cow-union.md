---
name: priced-rank-random-cost-was-the-cow-union
description: "Ticket 02 (phase-2 campaign), measured 2026-09-18/19 at 200k SKUs: the gain evaluator's pool open for rank_random spent 70% of its construction (205 s of 293 s, 55% of the whole priced drain) materializing every aisle to build `set().union(*aisle_idx_sets.values())`; a lazy union over the ledger's counted inverse cut the drain 376 s -> 72 s (5.2x). rank_popularity NEVER built that union (its pool has an order key), so its priced multiple has a different, unmeasured cause. The digest gate for the evaluator's pool path is the `_toy_priced` spec, not `smoketest --profile tiny`."
metadata:
  type: project
---

**What was measured (cProfile, `uni_rank_random_norsl`, 200k SKUs of the 400k catalogue, 10
batches, uncoupled, `gain_myopic`):** 18 drains at yard depths 3-7, 402 `place_load`s, 7,384
pool opens (18.4 per place_load, 4,069 candidates each).

| | before | after |
|---|---|---|
| wall | 856 s | 437 s |
| drain (`plan_order`) | 376 s | 72 s |
| `_make_pool` | 287 s | 49 s |
| union term inside pool init | 205 s (`set.union` 110 + `_CowSets.values()` 96) | ~3 s |

**Why:** under the evaluator a pool opens over a copy-on-write view whose `values()`
materializes EVERY aisle (2,774 at campaign scale) so the caller cannot mutate the live set;
`_RankedAssignPool.__init__` built the placed-index union through it at every open. The fix
(commit `3f2dd94e`): `AisleLedger.partner_aisles` counts its placed keys (`_PartnerAisles.
n_placed`), `_CowSets.union()` returns a `_PlacedUnion` over that inverse (exact `len`, since
`_delta_lift_from_row` folds the shorter side), and `_placed_union` dispatches so the wave path
keeps its historical set byte for byte.

**How to apply:**
- `rank_popularity` was blamed in the same ticket and never built the union (`order_key` set);
  whatever makes IT slow under the evaluator is still unmeasured -- profile it before
  assuming. The remaining priced cost of `rank_random` is the tier (`_D_map`, bucketing), not
  the warehouse.
- A refactor of the evaluator's pool path is digested against the `_toy_priced` spec (two cells
  off phase 2's axis, `fifo` + `gmyopic`, over `fifo`/`rank_random`/`rank_popularity`,
  coupled, 8k SKUs, 6 batches, ~3 min): `smoketest --profile tiny` runs `scheduler_ab`, which
  has NO inbound axis, so the code under change never runs there. Verify the path RAN
  (instrument the pool kind) before believing IDENTICAL -- see
  [[toy-run-is-the-byte-identity-instrument]] and [[a-cache-needs-a-scope-object]].
- The lazy view iterates dict order; that can only reach a float when the placed set is smaller
  than a SKU's partner row, which a stocked warehouse never presents. The digest, not the
  argument, is the evidence.
