---
name: calendar-span-is-not-work-days
description: "an arm's calendar span and its work days differ by 86400/day_seconds, so dividing a crew's seconds by _arm_span_days over-reads utilization ~3x and prints an impossible share"
metadata:
  node_type: memory
  type: project
  originSessionId: f5001744-2698-4b33-8290-4383bbee159f
  modified: 2026-09-11T12:04:45.599Z
---

The sim clock only advances through WORKING hours. So an arm of 40 work days on an 8-hour day
spans **13.3 calendar days**, and the two denominators differ by exactly `86400 / day_seconds`
(3.0 at an 8-hour day).

`Optimization/Performance_Evaluations/yard/scorecard.py:_arm_span_days` returns the CALENDAR
span -- `(max(batch_start_time + duration) - min(batch_start_time)) / SECONDS_PER_DAY`. That is
the right denominator for **door utilization**, because a door span is calendar time: a trailer
occupies its door overnight. It is the WRONG denominator for any **crew** share, because a crew
is granted `crew x day_seconds` per WORK day and is not on the clock overnight.

Measured (2026-09-11, the door-team cap): the receiver busy share denominated on the calendar
span rendered **184%** on the reference pair. Over distinct `work_day` values it reads 61%
(store) and 16% (fulfillment) -- matching `throughput.audit`'s independently computed
`recv` realized of 0.608 and 0.165, which come from a different source column entirely
(`work_events` role='receive' vs `batch_stats.recv_seconds`).

**How to apply:** a crew share is `seconds / (crew x day_seconds x n_work_days)`, and
`n_work_days` is `batch_df['work_day'].nunique()` -- distinct days, not batches, so it stays
right if a day ever releases more than one batch. That is the same grant
`Optimization/simconfig/equilibrium.py:_utilization_clause` measures against, which is what
makes the two reports comparable rather than merely similar. The unit tests passed on the wrong
arithmetic; only rendering on a real run caught it, so **build the fixture so the two
denominators disagree** or the test proves nothing.

Related: [[one-clock-one-speed-one-config]] (the absolute clock this rests on),
[[sim-time-unit-is-seconds-not-ms]], [[a-count-is-not-a-claim]] (the same discipline: get the
denominator right before concluding), [[a-right-site-total-hides-two-wrong-shares]].
