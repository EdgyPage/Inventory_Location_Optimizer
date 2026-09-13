---
name: two-days-site-and-calendar
description: "\"Days\" is ambiguous in this repo by a factor of 3 — the site day is 28,800 s and the calendar day 86,400 s; the yard fee is in calendar days"
metadata: 
  node_type: memory
  type: project
  originSessionId: 49ce8dd0-d54b-48a4-a5d4-38e89ac24fcb
  modified: 2026-09-13T00:59:55.013Z
---

Two different days are both legitimate here and they differ **3x**. Nothing on the page warns
you, because they are declared in modules that do not name each other:

- **Site day = 28,800 s** — `Warehouse/kernel/timeline.py:DEFAULT_SHIFT_SECONDS` (`8 *
  SECONDS_PER_HOUR`). Under the era a BATCH is one site day, so run depth, measurement windows
  ("days 20-39") and anything denominated in batches are site days.
- **Calendar day = 86,400 s** — `Performance_Evaluations/common/units.py:SECONDS_PER_DAY`.
  Deliberate, and argued in its docstring: trailer detention accrues overnight and at weekends,
  so a labour bound would understate a standing trailer by whatever the site is closed.

**The yard fee proxy is in CALENDAR days.** `frames._ydf` divides by 86,400, and so does the
urgency gate (`Inbound/gain.py`), so `INBOUND_FEE_THRESHOLD_DAYS` / `PHASE2_THRESHOLD_DAYS` are
calendar days. At the pilot regime a trailer's whole detention is ~0.35-0.6 of one, so any
threshold above ~0.6 makes the fee axis **identically zero** — and it reports `0.00` everywhere
rather than raising, so a vacuous fee axis looks like a measured one.

**Why:** a fee sweep taken by hand divided by the site day, producing a knee of "~1.3 days" that
was really 0.433 calendar days. It was one commit from being written into the knob, where it
would have zeroed the fee axis and the `gain_gated` H grid derived from it — silently, since the
gate reads the threshold at simulation time and a never-firing gate still runs. Caught only by
running the same tool on the older run and finding it disagreed with that run's own recorded
numbers by exactly 3.00x.

**How to apply:** never write or read a span in "days" without naming which day. Take
day-denominated yard numbers through `frames._ydf` rather than by hand — that is what makes the
divisor the production one. When a threshold-style knob shows no effect, check the divisor
before concluding the effect is absent. Related: [[sim-time-unit-is-seconds-not-ms]] (the same
class of defect, 1000x, on the hours divisor — which is why `units.py` derives rather than
restates), [[one-clock-one-speed-one-config]], [[a-count-is-not-a-claim]].
