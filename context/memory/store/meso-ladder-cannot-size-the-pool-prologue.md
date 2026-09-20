---
name: meso-ladder-cannot-size-the-pool-prologue
description: "The meso calltree ladder's tier is 74 buckets against the campaign's 4,200 and its round has ~3 candidates against 25, so a pool-open optimisation reads there as a call-count REGRESSION"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-20T07:06:05.691Z
---

`calltree_growth.py --knob yard --config inbound_gain_pool` is the right instrument for the
gain evaluator's CALL SHAPE and the wrong one for the cost of a pool OPEN. Measured
2026-09-20 while landing the round-shared pool prologue:

| quantity | meso ladder | phase-2 campaign |
|---|---|---|
| buckets in a tier | 74 | 4,200 |
| live aisles | ~5 | ~1,400 |
| candidate trailers in a round (T) | ~3 | 25 (mean yard depth ~17, max 25) |

Both gaps push the same way. The prologue a template amortises is tiny there, and it is
amortised over ~3 opens instead of ~25, so the template is rebuilt almost as often as it is
read. The copy-on-write READS (a Python `__getitem__` per aisle, a generator per overlaid
aisle's brackets) are then the dominant term. The ladder's traced call count went UP by
169,343 for a change that is 1.4x FASTER at the campaign shape.

**So size a pool-open change on a synthetic tier of the measured shape, not on the ladder.**
1,400 aisles x 3 brackets x 6 bins, driving a real `_TravelBalancedPool` through open +
seat 12 units, is ~30 lines and answers in seconds. What it said:

- `aisle_buckets()` alone: 3.685 ms, of which the first-live walk and the sort are 33% and
  the 4,200 `_Cursor` constructions are 67%
- memoising only the SHAPE and rebuilding the cursors: 1.50x
- opening over a shared template and cloning on the write: 1537x on the prologue
- the WHOLE open: 13.35 ms -> 9.23 ms, 1.4x, because the prologue was only 27% of it

The ladder is still the instrument for call-count EXPONENTS and for
[[the-instrument-is-what-is-wrong]]'s class of error; it just cannot price an open. Same
warning as [[a-python-read-path-is-a-regression-against-a-c-dict]], one level up: the
scale you measure at decides the sign of the answer.
