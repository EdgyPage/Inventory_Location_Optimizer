---
name: lockstep-tests-compare-aggregates-only
description: "The three PickSimulation/fast_pick lockstep tests all compare aggregates, so two loops could stamp identical totals on differently-shaped event streams and pass; and the travel identity needs the handling term"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T10:03:25.149Z
---

Three lockstep guards exist between the two picker loops:

| test | compares |
|---|---|
| `test_travel_decomposition.py::test_pick_fastpick_decomposition_lockstep` | travel breakdown + axes |
| `test_scheduler.py::test_pick_fastpick_lockstep_under_lpt` | `max(event time)` |
| `test_picker_clock_carry.py` | done-time and event count, 4 start times, both schedulers |

**Every one is an AGGREGATE.** Two loops could emit differently-shaped event streams with
identical totals and pass all three. No test compares them event by event — type, time, aisle,
sku, quantity and all five travel fields, in order.

The travel identity also needs five terms, not three:

    pick + nonpick + cart + handling + other == duration

Handling is a fourth clock advance with **no decomposition field**, deliberately. So
`pick + nonpick + cart == duration` is false and always was — worked in
`test_travel_decomposition.py`'s own fixture as `53.125 + 10.940 = 64.065 s`.

**Why:** a day-end cut is exactly the change most likely to reshape an event stream while
leaving its totals intact, so the existing guards would not see it. And a verification written
as the three-term identity cannot pass.

**How to apply:** before changing either loop's event emission, add the event-by-event
comparison. Note that neither `Pick.py` nor `fast_pick.py` is in `SHAPE_SOURCES`, so a commit
confined to them does NOT fire the preflight canary pair — unlike one touching
`strategy_runner.py`. Related: [[fulfillment-travel-rework-plan]], [[working-day-clock-plan-corrections]].
