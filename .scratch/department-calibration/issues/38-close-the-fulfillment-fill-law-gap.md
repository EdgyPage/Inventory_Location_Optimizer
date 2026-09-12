# Close the fulfillment fill-law gap

Type: grilling
Status: open

Graduated 2026-09-12 from inbound-optimization
[Re-verify the gate under the lead-aware record](../../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md),
which measured it and ruled out every alternative it could reach. HITL: the deliverable is a
decision about the coverage form, which is this map's, not a build.

## Question

The fulfillment channel's realized first-pass fill is ~8 points worse than the coverage record
promises, at the lead the record solved the floor at, and the gap is not a lead effect.

On `comparison_20260912_055947` (40 coupled era days on the reference `lt0` pair, days 20-39
measured, all four arms agreeing to the third digit):

| leaf | stamped missed share | realized | realized lead vs stamped | explained level |
|---|---|---|---|---|
| fulfillment | 0.0251 | **0.1044** | 1.782 d vs 1.766 d | 0.0253 |
| store | 0.0248 | 0.0302 | 1.789 d vs 1.766 d | 0.0249 |

Store is the control and it lands 0.005 above its stamp. Fulfillment lands 0.079 above, on
every arm, with the trend inside tolerance -- a level, not a runaway.

**What is already ruled out, so this ticket does not re-do it:**

- **The lead.** Both leaves realize the stamped 1.766 d almost exactly (Little's law over
  `in_transit_qty` / `units_ordered`). 37's record is doing its job.
- **Any other pipeline leg.** On the run's OWN `fill.vs_transit` curve, a missed share of
  0.1044 corresponds to an order-to-shelf lead of **~9.2 days** (0.09353 at 8.166 d, 0.11986 at
  10.721 d). The yard's detention is already inside `in_transit`
  (`TrailerTransit.merchandise()` counts `_yard`), `recv_depth` max is 0 on every day of every
  arm, and double-counting the entire yard detention on top of transit reaches only ~3 d, which
  the curve prices at 0.039. Nothing in the run is within a factor of three of 9.2 days.
- **The channel coupling.** Same site, same dock, same shared put pool, same record, and store
  passes. Coupling also moved fulfillment the RIGHT way against the uncoupled reading
  (0.148 -> 0.104), so "the shared crew starves fulfillment" has the sign backwards.

**So the question is the coverage FORM for this channel, not the pipeline.** What the answer
has to decide: whether the line floor's demand model under-covers fulfillment's busy SKUs, and
if so what the floor is solved against instead.

Leading hypothesis, offered as a starting point rather than a finding: memory
`sampler-affinity-flattens-the-fulfillment-line-rate` -- a per-SKU transient priced at the LINE
SHARE over-reads fulfillment touches by ~25%. That would leave the floor (1.4994 lines) under-
covering exactly the SKUs that drive the miss, and it is channel-specific in the right
direction: fulfillment's curve moves 0.006 -> 0.120 across the transit range where store's moves
0.021 -> 0.044, so fulfillment is far more sensitive to any under-statement of per-SKU demand.
`nothing-is-lost-under-the-era` is the other end of the same thread -- crews size on DEMANDED
units, and a floor that under-covers re-offers those units rather than losing them, which is
where the 285,041 re-attempt units come from.

Done when the form is decided and the reference pair's fulfillment supply reads in band on a
re-run. The inbound campaign holds behind it: inbound-optimization re-runs its gate and fixes
`PHASE2_THRESHOLD_DAYS` off the sweep already recorded on that constant.
