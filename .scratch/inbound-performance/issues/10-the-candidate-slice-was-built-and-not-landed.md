# The candidate slice: built, proven byte-identical, and NOT landed

Type: task
Status: resolved

The measured #1 refactor -- the pool opens over 1,094.8 candidate bins to place 12.70 units, and
its `__init__` is 59% of the receive drain. It was built, it works, it is byte-identical, and it is
reverted. This ticket exists so nobody builds it again without reading why.

## What was built

Two additions to `_Evaluator` in `Inbound/gain.py`:

* **`_tier_buckets(key, xk, yk, predicted, brackets)`** -- the frozen tier bucketed exactly the way
  the ranked pools bucket it: `[(aisle, mult, [(D, idx, bin), ...]), ...]` in first-appearance
  order of `(aisle, mult)`, each bucket ascending by `(D, idx)`. Memoizable because every input is
  frozen for the evaluator's life -- `space.empties[key]` is a tuple the drain froze, a bin's bay
  coordinates are fixed for the run, and the paces and brackets are one owner's resolved profile.
* **`_pool_candidates(...)`** -- at most `k` per bucket, `excluded` applied while selecting rather
  than as a separate pre-pass, replacing the `cands` build entirely.

## Why the slice is sound, and why it is byte-identical

**Sound:** `_aisle_best` reads only `dq[0]`, the head of each bucket, and `take` does exactly one
`heappop`. A pool asked to seat `k` units performs at most `k` pops IN TOTAL, across all buckets --
so the `k+1`-th cheapest entry of any bucket is unreachable. No caller reads `len(pool)` for a
decision (checked).

**Byte-identical:** three orderings inside the pool are load-bearing and all three survive.

1. Within a bucket the heap orders by `(D, seq)` where `seq` is the append index. The kept bins are
   emitted in ascending original index, so the new `seq` induces the same order.
2. `by_aisle` iteration order decides ties between equally scored aisles --
   `Assignment_Functions.py:1723` says so outright: `for aid in by_aisle:  # original order =>
   original tie-breaks`. Emitting bucket-by-bucket in the tier's first-appearance order of
   `(aisle, mult)` reproduces it even when an aisle's earliest bin is sliced away.
3. `_aisle_best` scans `by_aisle[aid].items()` with a strict `<`, so ties go to the first bracket
   seen; the same emission order preserves the per-aisle bracket order.

**Verified, not merely argued:** all four pool adapters digested IDENTICALLY to the values
committed in ticket 07 -- `d9296431` / `5c30526e` / `aa00c1d1` / `2651f773` -- over counters, every
occupied bin, and the live aisle dicts.

**And it does what it claims:** candidate bins handed to the pool fell from **8,130,323 to
432,177** over 7,426 opens, 1,094.8 -> 58.2 per open. An 18.8x reduction.

## Why it was not landed

It is a REGRESSION in the middle of the range, and the regression could not be removed without
tuning a constant per fixture.

Paired and interleaved in one process (the "before" arm monkeypatches `_pool_candidates` back to
the pre-slice body verbatim, so there is no cross-process or cross-day drift):

| | unguarded | gate at 4 opens | gate at 32 opens |
|---|---|---|---|
| 600 SKUs | **+128.3%** | -1.0% | -- |
| 2,000 SKUs | **+20.5%** | **+32.2%** | **+14.2%** |
| 6,000 SKUs | -48.1% | **-42.7%** | **-17.2%** |

Raising the gate improves 2,000 and degrades 6,000 by almost exactly as much. That is sliding along
a trade-off curve, not removing a defect -- and a constant tuned that way would need re-tuning for
every catalogue shape. This repo's history is unkind to exactly that.

## The mechanism, because it is the transferable part

**The win is downstream, the price is local.** The slice does not make `_pool_candidates` cheaper --
it makes it MORE expensive, because the code it replaces is a C-level list comprehension over the
tier and the selection is a Python loop with an `id()` and a membership test per entry. The saving
is in the POOL's `__init__`, which then buckets and heapifies 58 candidates instead of 1,370.

**So the trade is pure amortization**, and the separating variable is OPENS PER TIER, not tier size
or the oversize ratio. Those barely move across the range while the outcome flips sign:

| skus | opens | tier (med) | k (med) | buckets (med) | unguarded |
|---|---|---|---|---|---|
| 600 | 108 | 329 | 3 | 13 | +128.3% |
| 2,000 | 754 | 1,102 | 3 | 49 | +20.5% |
| 6,000 | 5,878 | 1,370 | 4 | 78 | -48.1% |

Tier size and ratio are nearly flat between the last two rungs; opens rise 8x and the sign flips.

**And the reason the memo needs so many opens to pay back is one line:** `_tier_buckets` does a
`sort()` per bucket, O(n log n), where the pool's `heapify` is O(n). The memo therefore costs
strictly more than one pool open, so it needs several before it is ahead. A version storing
unsorted buckets and calling `nsmallest(k, ...)` per open was considered and rejected: that is
O(bucket) per open, which is no better than the comprehension it replaces.

## What a future attempt should do differently

Not a tuned threshold. The promising shape is **no memo at all**: bucket and select in ONE pass per
open, replacing `C_comprehension(tier) + Python_poolinit(tier)` with
`Python_bucket(tier) + Python_poolinit(58)`. Since `Python_bucket` and `Python_poolinit` cost about
the same per element, that trades away the pool's full-tier Python pass and pays only the
comprehension's C pass -- a win at every scale, with no constant to tune. It needs the pool to
accept pre-bucketed input, which is a signature change in `Assignment_Functions` and therefore
touches the restock path that this whole effort has kept out of scope.

**k is small and that is the real headroom:** median 3, not the 12.70 the earlier decomposition
suggested (12.70 is the MEAN units per group; the median group is far smaller). A pool opened over
1,370 candidates to seat 3 units is a 450x oversize, so the ceiling here is large and still unclaimed.

## What stands

Nothing in this ticket touches the two landed refactors. Ticket 07's copy-on-write views (64-117x
fewer element copies, -45% to -78% on the drain) and ticket 09's `live` guard (8,130,323 scans
removed) are committed, byte-identical, and unaffected -- the slice was built on top of both and
reverted off the top.
