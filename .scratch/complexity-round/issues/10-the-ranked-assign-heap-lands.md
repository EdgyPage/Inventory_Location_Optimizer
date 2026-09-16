# The ranked-assign heap lands: 73x fewer key evaluations, k 1.963 -> 1.111

Type: task
Status: resolved
Blocked by: 03

The refactor ticket 03 convicted, built against the template `bd29d2eb` established in the
sibling pool.

## What changed

`_RankedAssignPool.take` selects from a `(key, rank, aid)` min-heap instead of scanning every
live aisle. The selection is now expressed as a **key** rather than a scan: a new `aisle_key(aid,
head_D)` parameter sits beside the existing `aisle_selector`, and `rank_popularity` supplies the
ordering it used to scan for.

**No lazy deletion, and -- unlike `_TravelBalancedPool` -- no run-boundary rebuild at all.**
Neither key depends on the SKU (`head_D[aid]`, and `aisle_demand_sum[aid]` for popularity), and
`take` moves only the WINNER's, so one pop and at most one push per placement keeps the heap exact
for the whole wave. The sibling needs a per-SKU-run rebuild because its score carries `fq`/`var`;
this one does not.

`rank_random` deliberately keeps the scan: a uniform draw has no key to order by, and the
`list(...)` order is what decides the pick.

## The tie-break is what makes it byte-identical rather than merely equivalent

`min()`/`max()` return the FIRST extremal element in iteration order. `head_D` is built in
`by_aisle` order; reassigning an existing key does not move it and deleting one does not reorder
the rest -- so the scan's tie-break is "lowest original insertion index", for BOTH directions.
`_rank` captures it. The default key is negated when maximising, so one min-heap serves `tmin` and
`tmax` alike (`-0.0 == 0.0`, so a zero-D tie still falls through to `_rank`).

## The result, measured on the cell that convicted it

`--config ranked_popularity`, meso `skus` 500..8,000:

| | key evaluations at 8,000 SKUs | k |
|---|---|---|
| before (scan) | **8,819,328** | **1.963** |
| after (heap) | **120,149** | **1.111** |

**73x fewer, and the exponent collapses to essentially linear.** `_RankedAssignPool.take` is
unchanged at 76,514 (k = 1.018) and `placements` are identical at every rung -- same work, same
decisions, fewer evaluations.

Predicted saving from the two independently measured factors: 8,699,179 removed evaluations x
0.0587 us = **0.51 s**. Observed rung wall 12.02 -> 11.20 s (0.82 s). Same direction, same order
of magnitude. **The COUNT is the result; the wall is corroboration only** -- a single
cross-invocation pair is inside this instrument's stated noise for a sub-second effect, and this
package's own history is a series of walls read too confidently.

## THE TEST WAS VACUOUS, AND THAT IS THE FINDING WORTH KEEPING

`Tests/unit/test_ranked_assign_pool_equivalence.py` passed 42/42 immediately after the heap
landed -- while exercising a path production had stopped taking. It built `rank_popularity` with
`aisle_selector` (the scan), and production now builds it with `aisle_key` (the heap). The arm
under test and the arm that ships had diverged, and nothing said so.

That is the same failure `inbound-performance` ticket 05 records: *a sabotage test installing its
identity into a table production had stopped reading, so the sabotage reached nothing.* It failed
loudly there only because a refactor moved the seam; here it would not have failed at all.

The fix keeps **both** shapes as arms (`rank_popularity` = key, `rank_popularity_scan` = selector)
so the agreement is three-way -- `impl(scan) == pool(scan) == pool(heap)` -- rather than two
restatements of the new code. The reference side translates the key back into the scan it
replaced, because the frozen oracle `_ranked_assign_impl` has no key concept and must stay the
RETIRED algorithm.

**Proven non-vacuous**: inverting the tie-break rank fails 14 of 51. The whole placement
equivalence family is green at 138.
