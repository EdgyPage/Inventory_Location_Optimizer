# Declare the equilibrium bands

Type: grilling
Status: resolved
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

## Answer

Resolved 2026-09-05. Seven decisions were put to the user in one round and every recommendation
was confirmed. Four facts were established first and shaped the check; each is recorded because
it silently breaks the ticket's original sketch.

**Facts.** (1) The drain-or-cap ledger is LOG-ONLY — `strategy_runner` prints one `[shift] day N
ended …` line per day (near line 1570) and persists nothing, so no DB can answer "was day N
drained" today. (2) `released_late` watches the picker clock alone: release waits on `arm_clock`,
and the cut is a between-bins START gate, so a capped pick day leaves a tail of a few seconds of
lag, a drained day leaves exactly 0, and a put or receiving overrun leaves no trace in it. (3) The
reference run has no yard (02: minimal mechanics), so the pilot's strict contention share is a
campaign-phase instrument, never a precondition. (4) Crews are integers (`ceil`) and site totals,
and each channel leaf runs alone, so realized put/receiving utilization inside a leaf is that
channel's share of a rounded site crew — far below ρ by construction. A band drawn around ρ
itself would fail every honest run.

1. **Target: ρ = 0.85 for all three departments**, one `assumed` scalar each, as 01's derivation
   declared. The ticket's 0.80 / 0.90 sketch is withdrawn: dock scarcity for the inbound campaign
   comes from doors and lead spread, not from the unload crew's utilization, and ρ_recv is a flag
   the campaign can set when it runs.
2. **The quantity is UTILIZATION, not duty cycle.** Under the era every crew is granted the whole
   day, so *Duty cycle* (grant share) is 1.0 everywhere and says nothing; what the bands measure is
   worked ÷ granted. Glossary gains **Utilization**, **Headroom** (1 − target utilization, the
   declared scarcity) and **Equilibrium**; *Duty cycle* keeps its grant-share meaning.
3. **The band is drawn around EXPECTED utilization, not around ρ.** The derivation records, per
   department per channel leaf, `expected_utilization = load × s ÷ (crew × S)` after crew rounding
   and after the leaf's share of the site crew, in the `derived` block. The band is
   |realized − expected| ≤ `band_tol`, one absolute tolerance of **0.10** shared by all departments,
   an `inputs` scalar marked `assumed`. Realized utilization is a ratio of sums over the window,
   never a mean of per-day ratios; picking from `batch_stats.task_makespan`, put and receiving
   from `work_events` intervals joined to the day through `batch_id`.
4. **The pre-registered equilibrium check** is a function of (db, day_lo, day_hi) returning
   pass/fail with reasons, four clauses, all STRICT:
   - every day in the window DRAINED (from the persisted ledger, item 5) — not "most": with 15%
     headroom one capped day in twenty means the derivation is wrong, not unlucky;
   - `released_late` = 0 on every drained day, as a self-consistency assertion — a nonzero on a
     drained day is an instrument bug and RAISES rather than fails;
   - realized utilization inside the band (item 3) for every department;
   - missed share stable: mean of the window's second half minus mean of its first half within
     ±0.02 absolute (from `items_demanded` vs `total_items`). The LEVEL is recorded, never gated;
     no regression slope (memories `knees-hide-from-r-squared`,
     `per-batch-series-are-autocorrelated`).
5. **The ledger is persisted**: a new `shift_days` table (run_id, day, cap_end, end_s, drained,
   standing, last_finish) written as each close-out fires, riding the schema pipeline
   (`schema-maintainer`: a Family, `stamp_checked`), joined to `batch_stats.work_day`. Deriving
   drained/capped from `batch_stats` alone was rejected: it is blind to standing put queues and
   the dock floor, which are exactly the coupling this map reads. Scope rider on 08. Trap for the
   build: the close-out fires at the FIRST batch of the NEXT day, so the final day never closes,
   and its flush must not sit inside the checkpoint tail (memory
   `run-end-writers-miss-the-final-flush`).
6. **One pure function, two callers, the sim never judges itself.** The check lives beside the
   derivation (`Optimization/simconfig/equilibrium.py` or a sibling of `staffing.py`). The
   reference-run driver calls it to accept or discard a window and stamps the passing window and
   its readings into the CALIBRATION record; the throughput audit evaluation calls the same
   function per run and renders realized vs expected. Targets, `band_tol` and expected values ride
   the `staffing` record onto `sim_result` (03's sixth seam), so the audit needs no seam work. A
   run-end verdict written by the runner was rejected.
7. **Precondition for the reference run, REPORT for everyone else.** On a campaign arm a picking
   utilization below the band is the arm's travel saving — the effect being measured — and a
   capped day is flagged "declared throughput not delivered"; neither fails the run. Whether a
   capped campaign day is also a comparison caveat stays in the interaction-effects fog.

**Glossary:** `CONTEXT.md` gained **Utilization**, **Headroom** (Measurement) and
**Equilibrium** (Day-over-day), 2026-09-05. No ADR: nothing here is hard to reverse.

**Map consequences, applied this session:** the throughput audit graduated from fog into
[Build the equilibrium check and the throughput audit](10-build-the-equilibrium-check-and-audit.md)
(blocked by 08); [Take the reference run](09-take-the-reference-run.md) is now also blocked by 10,
since its window precondition is 10's function; [Build the derivation, the calibration record, and
the era wiring](08-build-the-derivation-and-era-wiring.md) gains the `shift_days` ledger, the
`expected_utilization` derived values and the `band_tol` input (comment).
