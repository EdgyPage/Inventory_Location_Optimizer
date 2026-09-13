# Re-size the funnel in site days

Type: task
Status: open
Blocked by: ../../department-calibration/issues/13-derive-the-expected-travel-closed-form.md

AFK once unblocked. Cross-map gate: waits for the department-calibration map's
[Take the reference run](../../department-calibration/issues/09-take-the-reference-run.md),
which reports the wall cost of one site day under the era — the number the arithmetic below needs.
Must be resolved BEFORE phase 1 launches; it does not wait for
[Verify the derived receiving crew under arrivals](23-verify-the-derived-receiving-crew.md).

## Question

Phase 1 (136 work units) and phase 2 (480 units, ~13 h, ~1.1 TB) were sized in BATCHES at
published depth. Under the era's one-release-per-day pacing a batch IS a site day, and the funnel
inherits the reference run's window: 40 site days with days 20–39 measured (department-calibration
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md),
decision 4). Re-estimate both phases against that window from the reference run's measured wall
and RSS per day, and record the new sizing on the campaign entry (archive-as-you-go stays
mandatory; the grids remain the trimming lever).

Two build items ride here, both in the selection writer / phase-2 launcher this map owns:
- **Pin the calibration record across phases.** `run_restock_selection` stamps the staffing
  record's identity into `restock_selection.json`, and `inbound_policies` REFUSES to start when
  the current derivation's identity differs — the era's twin of the existing `PHASE2_ARMS`
  refusal. Without it a `calibration_stale` re-derivation between phases makes the `inb_off`
  anchor's byte-identity check compare across derivations silently (decision 7).
- **The window is declared once.** Both specs (`inbound_select`, `inbound_policies`) take the
  40-day depth and the 20–39 measurement range from one place, so no campaign run reports
  utilization against a warm-up the constants never saw.

## Comments

2026-09-10, from department-calibration
[Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md),
decision 11: **phase 1 runs with the standing yard on under `fifo`.** The record now derives
each SKU's lead as its supplier lead plus the trailer's day-quantized transit (1.766 site days
at the pilot regime), and the floor is solved AT that lead -- so an inbound-off phase 1 and an
inbound-on phase 2 would field different floors, levels and warehouses, and the calibration pin
this ticket builds would refuse phase 2 as designed. The yard is slack under the derived crew
(23), so phase 1 with it on costs little, and both phases then share one record and one
warehouse. Two things for this ticket's build: `inbound_select` takes the same
`PILOT_RUN_DEFAULTS` arrival regime as `inbound_policies`; and the `inb_off` anchor's role needs
restating, since phase 1 is no longer an inbound-off run -- what its byte-identity check is
against is this ticket's to record. The regime itself (doors, median, spread) is 25's and must
be fixed before phase 1 launches, because median and spread now move the record.

2026-09-10, from resolving
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md):
the campaign now holds behind the site-dock coupling (map, Out of scope), and the sizing this
ticket owes changes shape with it -- a phase-2 cell becomes a PAIR of arms (one store rule, one
fulfillment rule) under one inbound policy, so the unit count is no longer `arms x policies`
per channel leaf. Re-estimate against the coupled run's wall and RSS per site day once it
exists; the calibration pin and the once-declared window are unchanged.

2026-09-12, from resolving
[Re-run the gate and fix the fee threshold](29-rerun-the-gate-and-fix-the-threshold.md): **the
gate PASSES, so phase 1 is launchable and this ticket's sizing is what stands between here and
the launch.** Two things it hands over.

**The coupled run this ticket was waiting for now exists.** The comment above says to re-estimate
against "the coupled run's wall and RSS per site day once it exists" —
`comparison_20260912_134002` is a coupled 40-day run at `--spec inbound_pilot` on the reference
pair, and it is the campaign's own regime (v3, era defaults, derived crew 23). Size against it.

**This ticket is denominated in SITE days and the fee knob is in CALENDAR days, and they differ
by exactly 3.** 29 lost a session to that confusion: a sweep recorded in site days was about to
be committed into a knob consumed in calendar days, which would have left the fee axis
identically zero. Both units are legitimate here and this ticket touches both — a batch IS a
site day (28,800 s, `timeline.DEFAULT_SHIFT_SECONDS`), so the 40-day depth and the 20–39
measurement window are site days; detention and `PHASE2_THRESHOLD_DAYS` are calendar days
(86,400 s). State which day every number is in, at every step. Do not inherit the bare word
"days" from the threshold work.
