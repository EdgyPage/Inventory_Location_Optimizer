# _RankedAssignPool.take scans every aisle on every placement

Type: task
Status: resolved

The same shape `bd29d2eb` already removed from `_TravelBalancedPool.take`, still present in its
sibling. `Assignment_Functions.py:1161`:

    best_aid = (min if self._minimize else max)(head_D, key=head_D.__getitem__)

**O(A) per placement -> O(U*A) per wave.** `_TravelBalancedPool` replaced exactly this with a
`(score, rank)` min-heap and proved it byte-identical; the argument, the tie-break device and the
no-lazy-deletion invariant all port. This ticket is deliberately written as the SECOND instance of
a solved problem, not a new one.

## Why a heap is correct here -- the invariant, stated as something to VERIFY

A heap is only equivalent to a rescan if **exactly one entry changes per placement**. In `take`:

* `dq.popleft()` advances ONLY the winning aisle's head, so `head_D[best_aid]` is the only value
  reassigned (or the key is deleted when the aisle drains);
* `_head_D` / `_head_bin` are written in exactly two places -- `__init__` (`:1124-1125`) and
  `take` (`:1176-1182`). Verified by grep; there is no third writer.

So the rescan re-reads A values of which A-1 cannot have moved. That is the definition of a
selection problem being solved by a scan.

**The condition that must be checked before landing, not assumed:** the heap FREEZES a
non-winner's key for the life of the wave; the scan RE-READS it. They agree only while nothing
outside `take` mutates the inputs mid-wave. For the plain `min`/`max` selector the only input is
`head_D`, which has no third writer -- settled. For the popularity selector below it is NOT
settled by grep alone, because that key also reads `aisle_demand_sum`, which is the manager's LIVE
dict.

## The tie-break, which is what makes it byte-identical rather than merely equivalent

`min(head_D, key=head_D.__getitem__)` returns the FIRST minimal element in dict iteration order.
`max` likewise returns the FIRST maximal one. Two properties make that reproducible:

* `head_D` is built from `by_aisle` in insertion order, and
* reassigning an existing key does NOT move it, while deleting one does not reorder the rest.

So the tie-break is **lowest original insertion index**, for both directions. `_rank = {aid: i for
i, aid in enumerate(by_aisle)}` -- the device `_TravelBalancedPool.__init__:1647` already uses --
captures it, and a min-heap over `(D, rank)` (minimise) or `(-D, rank)` (maximise) reproduces it
exactly. `-0.0 == 0.0` compares equal, so a zero-D tie still falls through to rank.

## Coverage: three of the four arms, not one

| arm | selector | heap-able |
|---|---|---|
| `tmin` / `tmax` | none (the `min`/`max` above) | **yes** |
| `rank_popularity` | `min(head_D, key=lambda aid: (aisle_demand_sum.get(aid,0.0), head_D[aid]))` | **yes, with the check below** -- `take` increments `_ads[best_aid]` for the WINNER only (and only when the SKU is new to that aisle), so again exactly one key moves. Heap key `(demand, D, rank)` |
| `rank_random` | `_rng.choice(list(bb.keys()))` | **no.** A uniform draw needs the live key sequence, and the `list(...)` order decides which aisle is picked, so any change must reproduce dict iteration order exactly. Out of scope; note that this one rebuilds an A-length list per placement for a single draw |

`rank_popularity`'s own docstring already records the property the heap needs: *"Its selector
reads the LIVE `aisle_demand_sum`, which `take` itself increments, so the aisle choice depends on
what this pool has already placed."* That is the same self-mutation `_TravelBalancedPool` handles.
What it does not say, and what must be established, is whether anything OTHER than `take` writes
that dict while a wave is in flight.

## Guards that already exist

`Tests/unit/test_ranked_assign_pool_equivalence.py` compares `_RankedAssignPool` against
`_ranked_assign_impl` as a LIVE oracle across four arms, with exact `==` on every mutated manager
sum (its docstring: exact because `aisle_demand_sum` is order-dependent). That is the fence. Add a
call-count guard in the shape of `Tests/unit/test_admit_held_is_linear.py` so the scan cannot come
back.

## Do NOT start here

Blocked on ticket 04 (the HEAD offender table). The archived ladders that would justify this
predate `bd29d2eb`, and this effort's own map says no archived number may be cited as a HEAD
number. Measure first, then pick -- the previous effort spent three of its four retractions on
exactly that ordering.


## Answer -- convicted at k = 1.963, and it is the highest exponent in the round

Ticket 04 could not see this arm at all: every config ran `DEFAULT_STRATEGY`, a travel-balanced
one. Two new ladder cells fixed that, and the measurement is unambiguous.

`--config ranked_popularity`, meso `skus` ladder, 500..8,000:

| skus | wall | selector-lambda calls | takes | **aisles scanned per take** |
|---|---|---|---|---|
| 500 | 0.36 s | 34,134 | 4,191 | **8.1** |
| 1,000 | 0.92 s | 178,200 | 10,891 | 16.4 |
| 2,000 | 2.11 s | 548,595 | 18,143 | 30.2 |
| 4,000 | 4.97 s | 2,167,841 | 37,911 | 57.2 |
| 8,000 | 12.02 s | **8,819,328** | 76,514 | **115.3** |

**k = 1.963** -- higher than the `R x A` rebuild's 1.912 and the largest exponent anywhere in this
round. The mechanism is visible without fitting anything: the scan width IS the live aisle count,
and it grows 14x across a 16x catalogue.

`_RankedAssignPool.take` itself is **k = 1.018** -- exactly linear, identical counts under both
cells. The pool is not the problem; the selector scanning every aisle inside it is.

### Priced

The selector's real shape (`(ads.get(aid, 0.0), head_D[aid])` over a live dict) measures
**0.0587 us per scanned aisle**:

    8,819,328 x 0.0587 us = 0.52 s of a 12.02 s rung = **4.3%**

and the share GROWS, because the scan width grows with the warehouse: wall k = 1.265 against
lambda k = 1.963, so the share rises as n^0.698 -- **~21% at 80,000 SKUs, ~66% at campaign scale**.

That is a bigger term than the `R x A` rebuild (2.66%) and it has a proven, low-risk fix, which
inverts this effort's refactor ordering. The plan ranked this candidate first; ticket 04 demoted
it for being unmeasured; measuring it properly puts it back on top **with a number**.

### The precondition, VERIFIED rather than assumed

This ticket said the heap is only equivalent to the rescan while nothing outside `take` mutates
the key's inputs mid-wave, and that for `rank_popularity` this was NOT settled by grep, because
the key reads the manager's live `aisle_demand_sum`. Three writers exist:

| writer | when it runs |
|---|---|
| `init_demand_state` | setup, before any wave |
| `_reclaim_empty_bins` | *"the drain currently happens once, at the top of the next batch"* -- its own docstring |
| `_drop_sku_from_aisle` via `requeue_bin` | the reloader's eviction phase |

And the batch loop orders them: `reloader.reload(...)` at `strategy_runner.py:2071`, then
`mgr.check_reorders(...)` at `:2079`, which is where placement happens. Sequential phases, not
interleaved. **Within a wave, `aisle_demand_sum` is written only by `take`, for the winner.** The
precondition holds.
