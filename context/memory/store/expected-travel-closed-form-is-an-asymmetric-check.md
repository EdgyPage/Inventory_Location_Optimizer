---
name: expected-travel-closed-form-is-an-asymmetric-check
description: "Measured 2026-09-19: simconfig/expected_travel's closed form models one sweep per aisle, so its travel term is ~0.4% of the modelled day and it cannot see aisle choice; agreement with pick_owed_s is weak evidence, disagreement is the informative direction"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-20T04:24:47.860Z
---

`Optimization/simconfig/expected_travel.py`'s closed form, re-taken 2026-09-19 over a mid-run
placement, models a day as **one sweep per aisle**. Its travel term comes out to ~0.4% of the
modelled day, and by construction it cannot see AISLE CHOICE at all — moving a SKU between
aisles of the same height bracket is invisible to it. Its only placement signal is the height
bracket, which [[pick-owed-s-replaces-flow-totals-for-unload-ranking]]'s `pick_owed_s` also
carries.

**So agreement between the two is weak evidence, and disagreement is the informative
direction.** They share the one signal both can see (height bracket); agreement just confirms
neither is broken on that shared axis. A genuine aisle-choice effect can only show up as a gap
between them.

**A concrete degeneracy:** a placement that spreads hot SKUs one per aisle is priced BETWEEN the
two extremes by `pick_owed_s`, and is EXACTLY EQUAL to the fully packed placement under the
closed form — the closed form cannot rank the two at all.

**Why:** the closed form's day model was derived for a different question (staffing
sizing — see [[no-calibration-simulations]]), not for discriminating among placement policies
within a run; using it as an oracle for placement ranking will silently pass placements it
cannot distinguish.

**How to apply:** use `expected_travel` only as a sanity floor (does `pick_owed_s` move in the
same direction on the height-bracket axis), never as a placement-ranking oracle. A disagreement
between the two is worth investigating; an agreement is not confirmation of aisle-level
correctness.
