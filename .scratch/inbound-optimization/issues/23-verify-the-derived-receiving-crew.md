# Verify the derived receiving crew under arrivals

Type: task
Status: open
Blocked by: ../../department-calibration/issues/09-take-the-reference-run.md

AFK once unblocked. Cross-map gate: this ticket may not start until the department-calibration
map's [Take the reference run](../../department-calibration/issues/09-take-the-reference-run.md)
is resolved — the calibration record it commits is what this run derives its crew from.

## Question

Re-run the pilot gate under the calibrated era as a VERIFICATION, not a search. The original
pilot ([Run the pilot gate](22-run-the-pilot-gate.md)) searched for a receiving regime in which
10's two acceptance criteria hold and committed it as `--recv-crew-size 4 --recv-day-seconds
43200`. Under the era that regime is an ERROR (department-calibration
[Design the staffing record](../../department-calibration/issues/03-design-the-staffing-record.md),
decision 4): the receiving crew derives from the per-channel picker counts via ρ_recv = 0.85 and
f = 1.0, and receives the SITE's 28,800 s day. Nothing is searched for; the question is whether
the DERIVED crew lands the yard in band once arrivals are switched on.

Scope:
- Reshape the `inbound_pilot` spec: era on (drain-or-cap, one release per day, cut and rollover),
  NO crew flags, scheduler `lpt`, one cell, inbound on with the pilot's arrival regime, `fifo` +
  `tmin` (both in `FAITHFUL_GAIN_FAMILIES`), the reference window (40 site days, days 20–39
  measured).
- Read the two acceptance criteria (yard contention, binding-but-stable cuts) THROUGH the
  equilibrium REPORT of
  [Declare the equilibrium bands](../../department-calibration/issues/04-declare-the-equilibrium-bands.md):
  every day drained, `released_late` = 0, utilization in band per department per leaf, missed
  share not trending. A capped day on this cell is "declared throughput not delivered" and is
  reported, not judged.
- Record the outcome on this ticket: in band → phase 1 is launchable; out of band → the
  answer names which department and which leaf, and the campaign holds again (a scalar change
  is a department-calibration decision, not this map's).

Decided at department-calibration
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md)
(2026-09-05), decisions 3 and 5.
