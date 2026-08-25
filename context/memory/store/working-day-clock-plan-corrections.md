---
name: working-day-clock-plan-corrections
description: "The approved four-commit working-day plan does not compose — nothing turns the cut on, the carry is defined twice, and two commits collide on an API neither ships; the corrected 7-step sequence lives in docs/design/WORKING_DAY_CLOCK.md"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T10:03:49.466Z
---

A read-only survey (2026-08-25, 12 agents) found the approved Phase-3 plan cannot be executed
as written. The full record, with evidence, is `docs/design/WORKING_DAY_CLOCK.md`. The four
findings that change what gets built:

1. **Nothing turns the feature on.** No commit touches the pick simulation's single production
   construction site, so `day_end` is never passed and the day cut ships unreachable. A wiring
   commit is missing from the plan.
2. **The carry is defined twice, incompatibly.** The cut commit builds a `carried` surface; the
   rollover commit derives the carry independently as the residual and never reads it. Either
   the cut is dead code or the day-cut units are double-counted.
3. **Release and cut cannot both be on** as designed: a schedule releasing batch i+1 at a fixed
   instant plus a cut stopping batch i's pickers lets one picker work two batches at once.
4. **The rollover and resume commits collide** on a name with incompatible types (an object with
   methods vs a dict from a checkpoint), and resume calls a schedule method the clock commit
   does not ship.

**Why:** the plan was written before the code was surveyed, and its commits were sized by topic
rather than by producer/consumer pairing. Two of them are only meaningful together.

**How to apply:** follow the 7-step sequence in the design doc, whose rule is that no step
leaves a producer without a consumer. Do not call the concept a "shift" — `timeline.shift_index`
contracts that shifts label and never schedule, restated in five other places including a
persisted DDL comment. Do not adopt the put-away budget parameter as the cut seam: it defers a
whole wave rather than truncating one, and has no production caller.
Related: [[empty-batch-clock-stall-is-a-contract]], [[lockstep-tests-compare-aggregates-only]],
[[placement-pools-and-the-audit-point]].
