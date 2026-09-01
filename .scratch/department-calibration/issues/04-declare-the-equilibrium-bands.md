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
