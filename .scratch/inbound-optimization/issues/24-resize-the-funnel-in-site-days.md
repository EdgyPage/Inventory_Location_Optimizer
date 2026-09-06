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
