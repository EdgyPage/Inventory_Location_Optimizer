# Re-verify the gate under the lead-aware record

Type: task
Status: open
Blocked by: 25, 27

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

## Comments

2026-09-10: department-calibration
[Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md)
is RESOLVED and this ticket's blocking edge moved to its two builds:
[Build the lead-aware coverage record](../../department-calibration/issues/37-build-the-lead-aware-coverage-record.md)
(the record) and [Chain the supplier lead before the trailer](27-chain-the-supplier-lead-before-the-trailer.md)
(the dispatch). What the re-run reads changes in three ways: the stamped fulfillment supply
expectation is now `1 - fill(lead)` at a transit of 1.766 site days (the reference pair's
attributes are 0, so that is the whole lead); the report prints the realized order-to-shelf
lead per leaf beside the stamp and `1 - fill(realized lead)` as the EXPLAINED level, so a
binding yard (25) is expected to lift the supply level ABOVE the band by exactly what the
realized lead explains -- read the explained level before calling the clause failed; and since
phase 1 will run with the yard on (36 decision 11), this gate's record IS the funnel's record.

2026-09-10, from department-calibration
[Build the lead-aware coverage record](../../department-calibration/issues/37-build-the-lead-aware-coverage-record.md):
the record side is built; this ticket's blocking edge on 37 is cleared (27 and 25 remain). What
the re-run will read, per leaf, from the equilibrium report / the throughput audit's supply row:
the STAMPED lead (`fill.lead_days`, 1.7656 at the pilot regime on the reference `lt0` pair), the
REALIZED lead (Little's law over the window: mean `in_transit_qty` over mean `units_ordered`),
and the EXPLAINED level `1 - fill(realized)` off the record's `fill.vs_transit` curve -- the
reference fulfillment curve reads 0.99384 at transit 0, 0.97488 at the stamp, 0.96102 at 3.05 d,
0.88014 at 10.7 d. The band is unchanged: supply within 0.02 of `1 - fill(stamped)`, which the
lead-aware record stamps at 0.02512 (store 0.02481). Under the derived record the reference pair
declares fulfillment at floor 1.4994 (was 1.2668), sum Q 2,595,593 (was 2,220,097), the warehouse
2,774 aisles / 2,505,050 bins (was 2,536 / 2,311,000): the run is a new era, and 24's pin must be
re-taken from it (setup at the pilot regime took ~35 min before the transit-tail truncation).
Read "supply near the explained level but above the band" as the yard binding, and a gap the
realized lead cannot explain as model error.
