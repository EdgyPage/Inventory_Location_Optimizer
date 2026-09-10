# Re-verify the gate under the lead-aware record

Type: task
Status: open
Blocked by: 25, ../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md

Graduated 2026-09-10 from
[Verify the derived receiving crew under arrivals](23-verify-the-derived-receiving-crew.md).
AFK once unblocked.

## Question

Re-run 23's verification once the coverage record carries the inbound lead
(department-calibration 36) and the contention regime is chosen (25): `--spec inbound_pilot
--n-batches 40` on the reference pair, read through `Diagnostics/equilibrium_report.py --window
20-39` and the yard tables, exactly as 23 did. Two readings decide it, both required:

- every clause in band on both leaves -- the fulfillment supply level within +/-0.02 of the
  stamped fill rate and not trending (23 read 0.148 vs 0.025, trend +0.020);
- the yard binds under `fifo` at the chosen regime: strict contention and binding cuts both
  nonzero on the fulfillment leaf (the channel the campaign is about), non-saturated (no
  runaway depth, missed share not degraded).

In band on both -> phase 1 is launchable and
[Resize the funnel in site days](24-resize-the-funnel-in-site-days.md) sizes it. Out of band ->
the answer names the clause and the leaf, and the campaign holds again; a scalar change is the
owning map's decision, never this ticket's.
