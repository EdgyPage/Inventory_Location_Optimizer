---
name: empty-batch-clock-stall-is-a-contract
description: "An empty batch not advancing arm_clock is a TESTED decision, not a bug; the test asserts it by source-index comparison, and the put-away drain leaks on the same guard"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T10:03:12.358Z
---

`strategy_runner`'s batch loop `continue`s past the clock advance when a batch yields no tasks.
This is **not** a deadlock and **not** an oversight:

- Nothing hangs. The loop keeps iterating and the arm terminates. The absolute axis simply
  under-counts by the wall time an empty batch should have consumed, **cumulatively**.
- `Tests/unit/test_arm_clock.py::test_a_skipped_batch_cannot_advance_the_clock` asserts it by
  comparing SOURCE INDICES — the skip guard must precede the advance. The runner also states it
  in prose, giving the reason: the epoch cannot then be recovered downstream by a cumsum over
  `batch_stats` rows.

A second, undocumented consequence of the same guard: the repo's only
`drain_putaway_records()` call sits below it, so a skipped batch never drains its put records
even though `check_reorders()` already produced them above the guard. They get stamped against a
later batch's epoch.

**Why:** the working-day clock has to make an empty batch advance time. That commit is
*reversing a stated decision* and must delete and rewrite that test — treating it as an
unguarded bug is how a day gets lost.

**How to apply:** say "clock stall", not "deadlock", and never repeat the claim that "every
later batch releases at the same instant" — a single empty batch shifts only the offset, not
every subsequent release. Fix the put-away drain leak in the same commit.
Related: [[one-clock-one-speed-one-config]], [[working-day-clock-plan-corrections]].
