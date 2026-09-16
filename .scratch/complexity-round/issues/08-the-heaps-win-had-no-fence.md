# The `take` heap's win had no regression fence

Type: task
Status: resolved

`bd29d2eb` replaced `_TravelBalancedPool.take`'s per-placement argmin over every aisle with a
`(score, rank)` heap. Its own comment records what the scan cost:

> 4,852,858 takes x 224 aisles = **1.09 BILLION iterations, 71.2% of the receive drain**

**Nothing guarded it.** `Tests/unit/test_travel_balanced_equivalence.py` proves the pool's RESULT
is byte-identical to a frozen oracle -- and a "simplification" back to a linear scan would keep
every one of those results identical, pass the entire file, and restore the billion iterations.

That is precisely the shape of `_admit_held`, which survived every release because the only thing
anyone ever checked was that it produced the right answer. The effort's map lists making the
guards permanent as a destination clause for exactly this reason.

## What landed

`Tests/unit/test_placement_selection_is_not_a_scan.py`, in the style
`test_admit_held_is_linear.py` established -- *"a wall-clock assertion would be flaky; a call
count is exact and deterministic."* Three invariants, one per measured fact from ticket 07:

| pinned | why |
|---|---|
| the winner refresh is **exactly one** `_aisle_best` per take, at 3 aisles and at 24 | the O(1) half; ticket 07 measured `refresh == takes` at every rung |
| per-take work does **not** grow when the warehouse goes 3 -> 24 aisles | THE regression `bd29d2eb` removed. Measured outside run boundaries, because a boundary legitimately costs O(A) and would swamp the signal |
| the rebuild is **exactly** `sum(live aisles at each boundary)` | the O(A) half, pinned so that changing it is a DECISION. This is the k = 1.912 term and the round's top candidate -- a lazy-bound refactor should make this assertion fail with a SMALLER number, not pass in silence |

## Two things that had to be got right

**The counter wrapped itself.** Two `_drive` calls in one test both monkeypatch the class, and the
first version captured `Pool.take` at install time -- so the second wrapper wrapped the FIRST
wrapper and the first counter also counted the second run's takes. It reported 20 takes against
10 and the comparison test failed on its own premise check, which is the only reason it was
noticed. Fixed by capturing the pristine functions once at import; the sabotage now passes its
implementation in through `base_take` rather than patching around the counter.

**Non-vacuity is the load-bearing test here.** Every other assertion is a bound satisfied by doing
LESS work, so an undercounting counter -- or a driver that places nothing -- passes all three.
`test_the_counter_can_tell_a_scan_from_a_heap` drives a deliberate O(aisles) selection and asserts
the counter reports the growth. Without it this file would be three green checks over a fence that
does not exist.
