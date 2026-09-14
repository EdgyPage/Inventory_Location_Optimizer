# The `live` rebuild scanned 8.1 million elements and removed none of them

Type: task
Status: resolved

The second refactor, and the cheapest one this effort will find: one guarded line, 8.9% of the
receive drain, byte-identical.

## The defect

`Inbound/gain.py` `_place_pool`, per tier per placement:

```python
bins, used = slot
live = [x for x in bins if id(x) not in used]
```

`used` is only ever added to by a `take` further down the same loop, so it is non-empty exactly
when a LATER BinKey group spills into a tier this same placement already drew from.

**That never happens.** Measured on the real driver (`run_fullfid`, `uni_rank_labor_norsl`, 2,000
SKUs, 25 batches, `gain_forecast` on both knobs): 7,426 pool opens, **8,130,323 candidate bins
scanned, zero removed.**

The first probe written to measure this shadowed the tier cache and counted **zero iterations** --
which looked like a broken probe and is in fact the cleanest statement of the finding. Each
`place_load` builds a FRESH `avail_cache`, and within one placement each BinKey group is visited
once, so `cache.get(('pool', key))` returns `None` every time, the slot is built fresh with
`used = set()`, and the comprehension filters a list against an empty set. The cache never serves
a second read at all.

## The fix

```python
live = bins if not used else [x for x in bins if id(x) not in used]
```

Byte-identical, and safe rather than merely cheap: `live` is truthiness-tested and then handed to
`_make_pool`, which does `pool_factory(list(cands), state, wp)` and copies. Nothing mutates it, so
the fast path and the comprehension yield the same elements in the same order.

The guard is kept rather than deleting the filter outright, because "never happens" is a
measurement of today's spill behaviour, not an invariant the code declares. A future family with a
longer spill chain would re-enter a tier, and then the filter is needed and correct.

## Proof

All four pool adapters digest **identically to the values committed in ticket 07**, which is a
stronger check than a fresh A/B: those digests were recorded before this change existed, over
counters + every occupied bin + the LIVE aisle dicts.

| arm | digest |
|---|---|
| `rank_labor` | `d9296431f3cfa36ea34fd0a799d027c858c2c5e80dcf9775f46318599b658a4e` |
| `rank_minlabor` | `5c30526e421c59a4fab9c220baf613f032a0dd4f810c5bdbf04d246f12bd8483` |
| `rank_cartlabor` | `aa00c1d126b0f57ea58614199ec8f72da73d56b45262e8bb587eda4990861216` |
| `rank_random` | `2651f7736d775ec3681e06b9e99617c3be70d9d6045e8b82799380e96f89b272` |

`Tests/unit` 2,443 passed / 1 skipped.

## Where this sits in the ranked queue

The receive-drain wall split, measured over two runs reproducible to 0.08 percentage points and
invariant to door count:

| | share | what it is |
|---|---|---|
| pool `__init__` over the tier | **59.0%** | 462 ns/candidate, 8,130,323 candidates |
| `cands` build (the `excluded` filter) | 12.0% | 93 ns/elem, 8,234,764 scanned, 1.27% removed |
| **`live` rebuild** | **8.9%** | 70 ns/elem, 8,130,323 scanned, **0% removed** <- THIS |
| take loop (the actual work) | 8.9% | 94,274 units |
| `pool.order` (LPT sort) | 0.68% | |
| `AISLE_VIEWS` (ticket 07's fix) | 0.26% | finished |
| `SpaceTimeline.freeze` | 0.8% | 2.28M copies, but C-level `tuple()` at 21 ns |

**The remaining 80% is three passes over an oversized candidate set**: the pool is opened over
1,094.8 candidate bins to place 12.70 units, an 86.2x oversize. That is the next ticket, and it is
the one that needs a real correctness argument rather than a guarded line.

## Two corrections to this effort's own decomposition, both measured

1. **"12.59 pool opens per `place_load`, driven by the SPILL CHAIN" was a mis-attribution.**
   `tier iterations == _place_pool calls == _make_pool calls == 7,426` exactly, with zero unseated
   units. 12.59 is **BinKey GROUPS** per `place_load` (7,426/590). The spill chain averages 2.84
   tiers and only its first entry is ever used.
2. **"a group is ~160 units" was wrong.** 159.8 is units per `place_load`, spread over 12.59
   groups -- a GROUP is **12.70 units**. This is what makes `_place_merge`'s per-placement sort
   trivial rather than significant, and it was ruled out on that basis.

## Ruled out by measurement, recorded so nobody re-convicts them

`_place_merge`'s sort, `place_load`'s `groups`/`avail_cache` rebuild, the `expect_heads` branch,
`_window_rates`, and -- the surprise -- `SpaceTimeline.freeze`, whose 2.28 million element copies
are C-level `tuple()` at 21 ns and cost 0.8% of the drain.

**The lesson, which corrects this effort's own ticket 03:** element counts must be weighted by cost
class before they are ranked. The pool's elements cost 6.6x the `excluded` filter's and 22x
freeze's. Ticket 03 ranked `_all_idx` by raw element count and would have over-ranked it on the
same mistake.
