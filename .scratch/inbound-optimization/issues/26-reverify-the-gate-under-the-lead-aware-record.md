# Re-verify the gate under the lead-aware record

Type: task
Status: resolved
Blocked by: 25, 27, 28

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

## Comments

2026-09-10, from resolving
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md):
**this ticket also waits on the site-dock coupling** (map, Out of scope: the successor
effort), which has no ticket here to list -- the gate is re-run on the COUPLED dock, not per
leaf. What it now reads: the site's receiving utilization against the record's `rho_recv`, the
crew's busy share and the dock's parallelism ceiling (28), strict contention and binding cuts
per SITE against 25's band (a quarter to a half of drains; detention p50 under the threshold;
stable depth; standing-at-end a tail), the realized order-to-shelf lead against the stamped
1.766 with `1 - fill(realized)` as the explained supply level (36 decision 10), and the
overage-by-threshold table -- this ticket fixes `PHASE2_THRESHOLD_DAYS` (25 decision 6). Do
not start before 28 and the coupling have landed; a per-leaf re-run would re-measure the
artefact 25 retired.

## Answer

Resolved 2026-09-12. One 40-day coupled era run on the reference pair
(`comparison_20260912_055947`: `--spec inbound_pilot --n-batches 40` on
`catalogue_reference_lt0`, launched detached, `run.log` clean -- no Traceback, no dead arm,
conservation OK on all eight leaves, `receiving_report` 8/8 arms and 4/4 coupled pairs with
0 FAILED), read through `Diagnostics/equilibrium_report.py --window 20-39` and the site yard
tables.

**SPLIT: the yard reading PASSES and the supply clause FAILS -- fulfillment, every arm. The
campaign holds again.** And the failure is a DIFFERENT one from 23's, which matters more than
the verdict: 23's answer was "the lead is not in the coverage record", and the lead IS in the
record now and IS realized. What is left cannot be a lead effect at all.

The pilot spec was made coupled first (`PILOT_RUN_DEFAULTS` gained `couple_channels`), so the
gate re-runs as one command rather than a typed flag -- phase 2 states coupling in
`PHASE2_RUN_DEFAULTS`, and a regime the two specs declare in different places is one they can
drift apart on. Pinned in `Tests/unit/test_era_wiring.py` against phase 2's own key.

### The yard: 25's band, and the artefact is gone (criterion b -- PASS)

One crew of 22 receivers over both channels' trailers, where 23 fielded 22 on EACH leaf against
that leaf's own trailers. That halving is the whole mechanism.

| reading | 23, per leaf (ful / store) | this run, one site dock (window 20-39) |
|---|---|---|
| strict contention | **0/40** / **0/40** | **8 / 7 / 8 / 4 of 20** per arm (34% mean) |
| binding cuts | 0 / 2 | **11-12 of 20**, every arm |
| yard depth mean / max | 6.5/12 / 8.9/15 | 16.8 / 23 |
| free doors at freeze | 4.00 / 3.92 | 2.15-2.35 |
| detention p50 / max | 0.18 / 0.36 d | 1.15 / 1.837 d |
| standing at end | 0 / 2 | 1-2 of 609 |

Non-saturated by every reading 25 asked for: depth FALLS across the window (first half 18.0 ->
second half 15.5), `yard_end` max 4-5, `recv_depth` max 0 on every day of every arm (the dock
never stands a unit overnight), 607-609 of 609 trailers cleared. Site utilizations in band --
put 0.856 vs 0.838, recv 0.889 vs 0.845 (f 0.179 / s 0.710) -- against a dock ceiling of 182%
(cap 10 x 4 doors / 22 crew), so the CREW binds and the doors are the ceiling, which is the
physical picture 25 specified. The whole-run figure the analysis prints (31 of 160 drains) is
lower than the window's 34% because contention accumulates: reading it off the measured window
is the right denominator.

`uni_tmin` alone reads 4/20 (20%), just under 25's quarter-to-a-half band; the other three sit
35-40%. An arm-specific low reading is a policy result, not a configuration failure -- which is
what the band is for.

### The supply clause: fulfillment, every arm, and NOT the lead (criterion a -- FAIL)

Store PASSES all five clauses on all four arms (supply 0.030 vs 0.025, +0.005, band +/-0.02).
Fulfillment FAILS supply on all four: level **0.104 vs 0.025 (+0.079)**, trend +0.0097 (inside
the trend tolerance -- it is not running away), 285,041 re-attempt units.

**The realized lead matches the stamp on BOTH leaves**, which is what closes 23's diagnosis:

| leaf | stamped lead | realized (Little) | in-transit / ordered | explained level | measured |
|---|---|---|---|---|---|
| fulfillment | 1.766 d | **1.782 d** | 49,486 / 27,774 | **0.025** | 0.104 |
| store | 1.766 d | **1.789 d** | 11,787 / 6,588 | 0.025 | 0.030 |

So the explained level explains NOTHING of fulfillment's excess, and by this ticket's own rule
(36 decision 10) that reads as model error rather than a binding yard.

**The run's own fill-vs-transit curve makes it decisive.** A missed share of 0.1044 sits on the
fulfillment curve at a transit of **~9.2 days** (0.09353 at 8.166 d, 0.11986 at 10.721 d). The
realized order-to-shelf lead is 1.78 d; the yard's detention is ALREADY inside `in_transit`
(`TrailerTransit.merchandise()` counts `_yard`); the dock floor is empty overnight; and even
double-counting the entire yard detention on top reaches ~3 d, which the curve prices at 0.039 --
less than half the gap. **No leg of this pipeline, real or double-counted, can produce 0.104.**
The fill law over-predicts fulfillment's first-pass fill by ~8 points at the lead it was solved
at.

Store is the control that rules the coupling out: same site, same dock, same shared put pool,
same record machinery, same realized lead -- and its level lands 0.005 above its stamp. Coupling
also moved the fulfillment level the RIGHT way against 23 (0.148 -> 0.104, trend +0.020 ->
+0.010), so a starved-by-the-shared-crew story has the sign backwards.

Leading hypothesis for the owning map, not a finding here: memory
`sampler-affinity-flattens-the-fulfillment-line-rate` -- a per-SKU transient priced at the line
share over-reads fulfillment touches by ~25%, which would leave the line floor (1.4994) under
-covering the busy SKUs exactly where fulfillment's curve is steepest (its missed share moves
0.006 -> 0.120 across the transit range against store's 0.021 -> 0.044).

### What this fixes, and what it does not

`PHASE2_THRESHOLD_DAYS` is **measured but NOT committed**, and the reason is on the constant
(whatif_config). 3.0 is now provably degenerate: no trailer of 609 was detained past 1.837 d,
so the fee is identically zero on every arm and `gain_gated`'s H grid derives from a number
nothing can exceed. The 3.5x channel disagreement that made 3.0 a compromise does not exist
under one dock. The measured knee is ~1.3 d (a quarter of trailers pay; the arms separate 1.7x
at 4.96 vs 8.61 trailer-days); the full sweep is recorded on the constant so re-fixing it is a
lookup. It is not committed because the gate FAILED: closing fulfillment's supply gap raises its
served units ~8% and the ordered units -- hence the dock's load -- follow, so a threshold taken
here is taken under the wrong regime.

### Graduated

- department-calibration owns the coverage record and the fill law, so the gap goes there:
  **the fulfillment fill law over-predicts first-pass fill by ~8 points at the stamped lead**,
  with this run as the evidence and the ~9.2-day equivalent transit as the falsifier.
- This map re-runs the gate once that lands, and fixes the threshold off the recorded sweep
  at the same time.
