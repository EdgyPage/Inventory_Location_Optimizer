---
name: working-day-clock-plan-corrections
description: "The approved four-commit working-day plan does not compose — nothing turns the cut on, the carry is defined twice, and two commits collide on an API neither ships; the corrected 7-step sequence lives in docs/design/WORKING_DAY_CLOCK.md"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T14:01:51.170Z
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

**How to apply:** follow the sequence in the design doc, whose rule is that no step leaves a
producer without a consumer. Do not call the concept a "shift" — `timeline.shift_index`
contracts that shifts label and never schedule, restated in five other places including a
persisted DDL comment. Do not adopt the put-away budget parameter as the cut seam for
PICKING: it defers a whole wave rather than truncating one, and has no production caller.

**Status 2026-08-25: every step is done except 6b**, which is a decision and not code — does
the receiving dock have hours of its own, or is unloading inside the put crews' day? Design
doc §8 states the choice. Three things that stayed true and are easy to get backwards:

- **The PUT cut is a START gate, unlike the pick cut.** A pick path is long and divisible so
  it truncates mid-bin; a put is one unit into one bin. A completion gate would need the
  duration, known only after the bin is chosen, so it would mean choosing a placement and
  un-choosing it past `_execute_placement` — the single bin-mutation commit point. Overtime
  is bounded by one put per worker, deliberately.
- **`thr_batch` is not throughput-per-day.** It divides by the batch MAKESPAN, so under a
  paced schedule with slack it reports the rate the crew worked AT, not what the day
  delivered — 4x the truth at a 400 s slot with a 100 s wave. `throughput_elapsed` is the
  second number; both are real and a scheduling change moves them opposite ways.
- **Batch-level resume DROPS the carry**, because `_pending` lives in the worker's locals and
  is in no checkpoint. It now raises rather than warning. The general lesson is broader than
  this feature: worker-local state is invisible to a checkpoint, so any new per-arm
  accumulator has to be re-derived on resume or refused.

Related: [[empty-batch-clock-stall-is-a-contract]], [[lockstep-tests-compare-aggregates-only]],
[[placement-pools-and-the-audit-point]], [[config-knob-has-five-seams]],
[[putaway-seams-for-inbound]].
