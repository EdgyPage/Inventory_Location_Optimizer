# Re-run the gate and fix the fee threshold

Type: task
Status: open
Blocked by: ../../department-calibration/issues/43-rerun-and-record-the-form.md

Graduated 2026-09-12 from
[Re-verify the gate under the lead-aware record](26-reverify-the-gate-under-the-lead-aware-record.md).
AFK once unblocked.

## Question

Re-run the coupled gate once department-calibration
[Close the fulfillment fill-law gap](../../department-calibration/issues/38-close-the-fulfillment-fill-law-gap.md)
lands, and fix `PHASE2_THRESHOLD_DAYS` on it.

`--spec inbound_pilot --n-batches 40` on the reference pair and nothing else -- the spec now
carries the coupling, so there is no flag to remember. Read exactly as 26 did:
`Diagnostics/equilibrium_report.py --window 20-39`, `Diagnostics/receiving_report.py`, and the
site yard tables under `<pair>/_site/inbound_*.db`.

**One reading is genuinely open and the other is not.** 26 already passed the yard criterion on
the coupled dock -- strict contention 20-40% of window drains, binding cuts 11-12 of 20, depth
falling, `recv_depth` max 0 -- and nothing in 38's fix is aimed at the dock. So this re-run is a
CONFIRMATION there and a verdict on the supply clause: fulfillment within +/-0.02 of its stamped
fill rate, store still in band, every other clause unchanged.

Expect the yard to get BUSIER, not quieter: closing the fill gap raises fulfillment's served
units ~8%, and ordered units -- hence trailers -- follow. That is why the threshold was not
fixed at 26.

**Fixing the threshold is a lookup, not a search.** The sweep is recorded in the comment block
on `PHASE2_THRESHOLD_DAYS` in `Optimization/config/whatif_config.py`; re-take it at the same
grid on the passing run and set the value at the knee (~1.3 d on 26's run: a quarter of trailers
accruing overage, the arms separating ~1.7x, neither pole saturated). 3.0 is degenerate under
one dock -- no trailer of 609 exceeded 1.837 d -- so the value MUST move; the only question is
where the passing regime puts the knee. Committing it also un-degenerates `gain_gated`'s H grid,
which is derived from the threshold, and retires the "fulfillment-calibrated compromise" caveat
the leaf model forced: one dock has one detention distribution.

In band -> phase 1 is launchable and
[Re-size the funnel in site days](24-resize-the-funnel-in-site-days.md) sizes it. Out of band ->
name the clause and the leaf, as 23 and 26 did.
