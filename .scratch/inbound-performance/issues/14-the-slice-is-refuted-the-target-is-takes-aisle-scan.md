# The candidate slice is refuted at campaign scale

> **PARTLY RETRACTED 2026-09-14 by ticket 15.** The slice refutation below STANDS --
> every number in it was re-measured and holds. What is retracted is this ticket's
> own replacement claim: that the remaining 71.2% **is** the aisle scan, "one billion
> iterations". It is not. Building the heap and measuring it removed the scan
> entirely and the drain fell **6.3%**, not 71%. The scan was ~9.9% of the
> non-construction drain.
>
> **Two errors produced that claim, and the second is the one worth remembering.**
> First, the arithmetic used BUCKETS (224 per open) as the scan width when `take`
> iterates AISLES (measured: 159). Second, and worse, "4,852,858 x 224 x 0.63 us =
> 685 s" was a DIVISION presented as an attribution -- the per-iteration cost was
> derived by dividing the very total it then claimed to explain, so it closed to
> three digits no matter where the time actually went. This ticket names that exact
> trap two sections down ("That identity proves NOTHING -- it is the same total
> divided two ways") and then commits it with a different pair of factors.
>
> The corrected attribution closes for the RIGHT reason, because both factors are
> measured independently of the total: 4,852,858 takes x 159 aisles = 771.6M
> iterations, and the measured saving from removing them is 61.5 s, i.e. **0.080 us**
> per iteration -- two dict lookups and a float compare. 0.63 us was 8x too high.
>
> See ticket 15 for what the drain is actually made of, and for the run-boundary
> rebuild, which does **45x more score computations than there are placements**.

Type: research
Status: resolved (replacement claim retracted -- see ticket 15)

Ticket 13 said the slice's rejection "should be revisited at campaign scale" and that the version
without a tuned constant was a scope decision worth taking to the owner. It was authorised, and the
answer is **do not build it**. Three of ticket 10's premises do not survive the measurement, and one
of them makes its correctness argument unsound rather than merely optimistic.

## Premise 1: "`__init__` is 59% of the receive drain" — it is 28.8%

Timed inside `_make_pool`, against the drain it sits in:

| skus | init_s | **init%** |
|---|---|---|
| 3,000 | 0.03 | **57.2%** |
| 100,000 | 7.93 | **31.3%** |
| 400,000 | 277.01 | **28.8%** |

The 3,000-SKU figure reproduces ticket 10's 59%, which is the check that says the timer measures
what ticket 10 measured. It simply does not carry: construction is a shrinking share, because the
cost it competes with grows faster than it does.

**So the slice's ceiling is 28.8% of the drain, whatever it does inside that.**

## Premise 2: "median k = 3" — the pool seats 195 units per open

`k` is the number of units the pool actually pops, and it was never 3. Ticket 10's median 3 is the
median GROUP size, a different quantity. Counted on the pool class itself:

| skus | takes per open |
|---|---|
| 3,000 | 21.85 |
| 100,000 | 94.71 |
| 400,000 | **194.80** |

**This makes ticket 10's soundness argument false, not just its arithmetic optimistic.** That
argument is: "A pool asked to seat `k` units performs at most `k` pops IN TOTAL, across all buckets
-- so the `k+1`-th cheapest entry of any bucket is unreachable." True, and the reason a slice can be
byte-identical. But with k = 195, entries 4 through 195 of every bucket ARE reachable. A slice built
to keep 3 per bucket would have been byte-DIFFERENT, and the four recorded digests would have caught
it -- after the build.

## Premise 3: "18.8x fewer candidates" — 1.5x at campaign scale

The slice keeps `min(k, bucket_size)` per bucket, so its reduction is a function of the bucket size
DISTRIBUTION against k. Both move with the catalogue, and they move toward each other:

| skus | cands/open | buckets/open | per bucket | k (takes/open) | **slice x @ real k** |
|---|---|---|---|---|---|
| 3,000 | 864 | 4 | 216 | 21.85 | **9.4** |
| 100,000 | 3,166 | 46 | 69 | 94.71 | **2.3** |
| 400,000 | 13,621 | 224 | 61 | 194.80 | **1.5** |

At 400,000 a bucket holds 61 candidates and the pool pops 195, so `min(k, size) = size` for nearly
every bucket: **the slice keeps almost everything it was built to discard.** The 1.5x that remains
comes only from the few buckets deeper than 195.

## The two ceilings compound

The slice touches only construction (28.8%), and saves only 1.5x within it (33%):

```
best case = 277 s * (1 - 1/1.5) = 92 s of 962 s = 9.6% of the drain
RUN x 3.24 -> 3.05
```

Against a build that must reproduce three load-bearing orderings exactly. **Not worth it**, and this
is the plan's own stop condition: report rather than land when the restructure's measured gain does
not justify it.

## What the 71.2% actually is, and it closes arithmetically

`_TravelBalancedPool.take` scans every aisle in the pool on every placement -- `for aid in by_aisle:
# original order => original tie-breaks` -- plus a full `O(aisles x mults)` rebuild of both caches
at every SKU-run boundary.

```
takes at 400k        4,852,858      (194.80 per open x 24,912 opens)
x buckets per open         224
= inner iterations   1,087,040,102
drain - init              685 s
per iteration            0.630 us   <- two dict reads and a compare, in Python
```

**One billion iterations of a linear scan.** That is the drain, and the slice cannot reach any of
it: the slice trims bins WITHIN a bucket and keeps every bucket, so the scan width -- the bucket
count -- is exactly unchanged.

## The real target, and it is out of this effort's scope

`take` solves a SELECTION problem (the min-scoring aisle) by SCANNING. The structural fix is a heap
keyed on the cached score, with the run-boundary rebuild as an O(n) heapify and the single-winner
update as a lazy-deleted push -- the same shape as the `_admit_held` quadratic this repo already
fixed (k 1.84 -> 0.94).

It is also squarely inside `Warehouse/placement/Assignment_Functions.py`, on the restock path this
effort kept out of scope, and `take`'s aisle iteration order is load-bearing for tie-breaks in three
documented places. So it is a real scope decision with a measured 71.2% behind it -- a much better
brief than the slice ever had, and the thing to put in front of the owner INSTEAD of the slice.

## What this ticket costs the reader to ignore

Ticket 10 is not wrong about what it measured. Every number in it is correct at 600-6,000 SKUs. The
failure is that all three of its load-bearing premises are SCALE-DEPENDENT and none of them says so,
so the ticket reads as a standing technical conclusion when it is a measurement at one point. This
is the same shape as ticket 11's retraction and the catalogue ceiling of ticket 12: **the finding
was not wrong, the range was.**
