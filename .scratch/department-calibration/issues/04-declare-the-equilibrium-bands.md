# Declare the equilibrium bands

Type: grilling
Status: open
Blocked by: 01

## Question

Turn "the crews have something to do, within bounds" into declared numbers: a target duty
cycle and an accepted band per department, plus the pre-registered check that a run is in
equilibrium at all.

The instruments already exist, all of them: `released_late` (seconds a batch lagged its
day slot — nonzero means the site is falling behind its own calendar), the drain-or-cap
close-out ledger (each day ends DRAINED or CAPPED, logged per day), `recv_cut` /
`put_queue` cut counters (labour the whistle stopped), and the pilot's strict contention
share (`yard_start > 0 AND free_doors_start == 0`) for the dock. What this ticket decides
is the TARGETS those instruments are read against, and where they are recorded (the
staffing record from [03](03-design-the-staffing-record.md) is the natural home, so a run
can assert its own equilibrium).

The headroom principle from the charter binds here: staff BELOW saturation, so
optimization decisions have room to move operations — the band's upper edge is
deliberate, not a safety margin.

**Recommendation:** target duty cycle ~0.80 with an accepted band of 0.70–0.90 for
picking and put-away; receiving pinned deliberately at the TOP of the band (0.85–0.95),
because the dock is where the inbound campaign needs scarcity to bind — the pilot's
passing config held strict contention at roughly that level. Equilibrium check,
pre-registered: over the run's back half, `released_late ≈ 0`, most days end drained
rather than capped, and missed share holds a stable level rather than trending. The exact
numbers are this ticket's to confirm against the calibration run's measurements; the
SHAPE of the declaration (target + band + check, recorded beside the staffing) is the
decision.

## Comments

2026-09-05, from resolving [Define the calibrated era](01-define-the-calibrated-era.md): the
bands are now the UTILIZATION TARGETS ρ per department that the derivation consumes (defaults
0.85 each, declared in 01's chain, replacing the ~0.80 sketched above — reconcile), plus the
accepted band and the pre-registered equilibrium check read from the instruments named here.
The read VERIFIES derived staffing; it no longer tunes it. The recommendation's shape (target +
band + check, recorded beside the staffing) stands.

2026-09-05, from resolving [Choose the calibration procedure](02-choose-the-calibration-procedure.md):
the pre-registered equilibrium check is also the reference run's WINDOW PRECONDITION — days 20–39
of each pass are measured only if the check holds over them, otherwise the window is discarded
and the next pass runs. So the check must be computable from `batch_stats` (`released_late`) and
the drain-or-cap close-out ledger over a day range, not only as a whole-run verdict.

2026-09-05, from resolving [Design the staffing record](03-design-the-staffing-record.md): the bands
are recorded beside the staffing INSIDE the `staffing` record, and the whole record is stamped onto
`sim_result` as one key, so the measured-vs-expected audit (still fog) reads the targets, bands and
expected throughput from there with no further seam work. This ticket's close is what graduates that
audit out of fog.
