# Copy-on-write aisle views: the eager copy was ~40x larger than the pool needs

Type: task
Status: resolved

The first refactor of the effort, and the first one measured BEFORE it was built rather than
justified after.

## Why this, and not the quadratic

Ticket 06 retracted the queue's ordering: production never stands a yard (T = 1.26 at 1,000 SKUs,
3.04 at 2,000), so `plan_order`'s O(T^2) candidate loop costs six calls, not 733. But `_make_pool`
still fired **7,426 times** in 25 batches on one leaf, because the opens are driven by the TIER
loop, not the candidate loop:

```
_make_pool = 48 entries x 12.3 place_loads x 12.59 pools
tiers per placement : 12.59      units per placement : 159.8
candidates per entry : 2.8   <- the only term T touches, and nearly flat
```

## The measurement that authorised the build

Taken first, on the real driver at 2,000 SKUs / 20 batches / one leaf, 5,534 pool opens:

| | |
|---|---|
| live aisles in `aisle_sku_sets` | 46 |
| aisles a pool can TOUCH | **mean 1.22, max 3** (1:4726 2:412 3:396) |
| set entries copied per open | mean 1,683 |
| total set entries copied | **9,313,598** |
| eager / needed aisle ratio | **37.8x** |

A pool's candidates are the free bins of ONE BinKey, which live in one or two aisles. The eager
copy walked all forty-six.

## What was built

`AISLE_VIEWS` beside `AISLE_COPIERS` — same six names, same shapes, same purity rule — with three
view classes in `Inbound/gain.py`, and `_make_pool` opening over views instead of copies. The two
tables are asserted to cover each other **at import**, because a name with a copier and no view
would silently fall back to the 40x path.

The three shapes are genuinely different problems:

* **`_CowFloats` copies NOTHING.** A float is immutable, so a read falls straight through to the
  live dict and only `__setitem__` needs an overlay. `_ads[aid] += fq` is a read then a write, and
  only the write lands in the overlay. Confirmed safe by grep: nothing anywhere sums or iterates
  these dicts, so no summation order can move.
* **`_CowSets` materializes one aisle per access**, because the value is mutable and
  `__getitem__` cannot tell `d[aid].add(sku)` from the `sku in d[aid]` two lines above it.
  Bounded by touched aisles (1.22), not by the warehouse (46).
* **`_CowListsByKey`** does the same two levels down — the shape whose docstring already recorded
  that a shallow copy hands the pool the live inner list, with no error and no symptom.

**Where the laziness deliberately stops:** `values()` and `items()` materialize every aisle,
because a lazy version would hand out the LIVE container for a caller to mutate — exactly the
failure the eager copy existed to prevent. `_RankedAssignPool.__init__` builds
`set().union(*aisle_idx_sets.values())`, so `rank_random` gets correctness and no win. Every other
ranked family reads by key and gets both. That is a stated trade, not an oversight.

## The result: byte-identical, and 64-117x less copying

Equivalence, one arm per view shape, digesting counters + every occupied bin + the LIVE aisle
dicts (so purity is inside the digest, not a separate claim):

| arm | view shapes | digest |
|---|---|---|
| `rank_labor` | sets + floats | `d9296431...` both modes |
| `rank_minlabor` | dict-of-dict-of-list | `5c30526e...` both modes |
| `rank_cartlabor` | five dicts incl. `aisle_vol_sum` | `aa00c1d1...` both modes |
| `rank_random` | the `values()` materialization path | `2651f773...` both modes |

Four different digests across the arms, so the comparison is not vacuous — the digest can tell
arms apart, and still cannot tell a view from a copy.

Elements copied, counted rather than timed (a count is exact where a stopwatch under contention
is not — the `test_admit_held_is_linear` precedent):

| arm | opens | aisle-entries eager -> cow | elements eager -> cow | reduction |
|---|---|---|---|---|
| `rank_labor` | 164 | 146,288 -> 652 | 1,379,360 -> **11,780** | **117x** |
| `rank_minlabor` | 168 | 149,856 -> 992 | 1,890,588 -> **29,616** | **64x** |

## The guard, and the one it broke

New: `Tests/unit/test_gain_cow_equivalence.py`, five tests in 3.5 s — equivalence, purity, a
SABOTAGE proving the purity test would catch a sharing view, and a SAVING assertion that fails if
someone quietly points `AISLE_VIEWS` back at `AISLE_COPIERS`.

**Broken and repaired:** `test_the_identity_copier_lets_the_virtual_placement_reach_the_warehouse`
(4 parametrizations) failed immediately. It is the existing Tier-1 sabotage for this seam: install
an identity copier and assert the live dict then DOES move. Moving `_make_pool` to `AISLE_VIEWS`
meant the identity was being installed in a table nothing consulted any more — so the sabotage
reached nothing. It failed loudly rather than passing vacuously, which is the only reason it was
noticed; repaired by pointing it at the table production reads, not by weakening it.

## Verification

* `Tests/unit` — **2,443 passed, 1 skipped, 0 failed** (was 4 failed mid-change).
* `Tests/unit/test_gain_bundle_labor_families.py` — 17 passed with the sabotage live again.
* `Tests/unit/test_gain_cow_equivalence.py` — 5 passed.

## And the count IS a wall: -45% to -78% on the receive drain

Measured after the fact, because a count is not a wall until someone converts it. PAIRED within one
process, alternating eager and view so host drift cancels -- the only kind of wall comparison this
repo trusts, since an identical command has run 3.4x apart on two occasions
(`inbound-optimization` ticket 31 section 5). Timing `SiteReceiving.receive` only, so pick and
put-away work cannot dilute the signal.

| arm | eager drain | view drain | paired median delta | n |
|---|---|---|---|---|
| `rank_labor` | 0.069 s | 0.042 s | **-45.5%** | 4 |
| `rank_cartlabor` | 0.058 s | 0.027 s | **-49.3%** | 5 |
| `rank_minlabor` | 0.353 s | 0.090 s | **-77.7%** | 5 |

Every individual pair was negative, not just the medians.

**`rank_minlabor` is the headline, and it is the arm that matters most.** Its eager drain was FIVE
TIMES the other two -- `_copy_of_lists_by_key` walks a dict of dicts of lists, the most expensive
of the three shapes -- and after the fix it sits alongside them. It is also fulfillment's **#1**
arm and store's **#2** in `PHASE2_PAIRS`, so the campaign runs it on both channels.

Read the -45% and -49% as FLOORS rather than estimates: the timed span includes
`SpaceTimeline.freeze` and `compose_site_view`, which this change does not touch, so the
evaluator's own share improved by more than the drain did.

## What this STILL does not claim

* **Scale.** 600 SKUs, 8 batches, meso fixture. The absolute drains are tens of milliseconds, near
  the edge of timing resolution, and the eager spread is wide (0.050-0.078 s on `rank_labor`). The
  direction and rough magnitude are established; the production-scale number is not.
* **The campaign multiplier.** The 1.6-1.9x priced/unpriced ratio is not re-measured. That needs a
  same-day priced-vs-unpriced control at campaign shape, and it is the number that would actually
  restate phase 2's 8.6-9.7 h sizing.
* **`rank_random`** gets correctness and no win, by construction -- it unions
  `aisle_idx_sets.values()`, which materializes. Unchanged and expected.