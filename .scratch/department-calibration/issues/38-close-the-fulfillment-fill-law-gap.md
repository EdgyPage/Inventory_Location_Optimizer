# Close the fulfillment fill-law gap

Type: grilling
Status: open
Blocked by: 39

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

## Comments

2026-09-12, a wayfinder session on this ticket. **The leading hypothesis is dead: the per-SKU
line RATE is not the mechanism.** Measured on `comparison_20260912_055947` (cell `k1_off`, arm
`opt_fifo_norsl`, days 20-39), re-pricing the fill through a parallel scorer that calls
`coverage._served_under_lead` with a supplied `rate` array. The rebuild gate passed exactly first
(`declare_from_record` at `declared_at` / `floors_at` reproduces the stamped `fill_rate` to
delta 0.000e+00 on both leaves, `sum_q` and `pipeline_units` identical), so the numbers stand.

| rate | weighting | store | fulfillment |
|---|---|---|---|
| share `n * pi_s` | stamped `d_s` | 0.0248 | 0.0251 |
| share | realized | 0.0254 | 0.0220 |
| realized per SKU | stamped `d_s` | 0.0257 | **0.0209** |
| realized per SKU | realized | 0.0909 | 0.1370 |
| flat over touched | stamped `d_s` | 0.0258 | 0.0215 |
| flat over touched | realized | 0.0831 | 0.0726 |

The literal substitution -- realized rates, everything else held -- moves fulfillment 0.0251 ->
**0.0209**, the wrong direction. The only variant reaching the target region (0.1370) also
switches the weighting, overshoots the realized 0.1044 by 31%, and fails its own control by
driving the store to 0.0909 against a realized 0.0302.

Two artifacts of that variant were measured and netted out:
- **Finite-window rate granularity.** A SKU seen once in 20 days scores at 0.05/d against a true
  0.018/d. Synthetic control -- counts drawn from `Poisson(20 * lambda_share)`, i.e. the share law
  exactly true, pushed through the identical machinery (3 seeds, spread < 0.0004): fulfillment
  **0.0651**, store **0.0776**.
- **Rollover re-offer contamination.** A missed unit is re-offered and picked later, adding a
  distinct `(batch_id, sku)`, so the realized line count is endogenous to the miss rate it is
  meant to predict. Fulfillment's 6+ line bucket: 11,946 of 12,875 line-batches coincide with an
  `unpicked_unstocked` carryover row for that SKU.

Difference-in-differences against the control: fulfillment **+0.0109 of a +0.0793 gap (14%)**,
store +0.0019 of +0.0054 (35%). **About 0.068 -- 86% -- is unexplained by the rate.**

**Sampler concentration is real, and it is a DEPENDENCE, not a rate.** Fulfillment touched 34,993
SKUs against 46,325 predicted (-24.5%) while total lines moved only -1.8%; 1,467 SKUs saw 6+
lines against 4 predicted; section-wide realized / predicted rate is 0.9819. The rate-substitution
test uses a 20-day mean and is structurally blind to temporal clustering, which is the shape this
evidence actually has. Graduated as
[Measure the repeat structure and the realized lead distribution](39-measure-the-repeat-structure-and-lead-distribution.md),
which also picks up the second unmeasured assumption: **the lead was ruled out on its MEAN only**
(Little 1.782 vs stamped 1.766), and the loss is convex in `K`.

**The finding that reframes this ticket: every SKU sits at the line floor.** 239,938/239,938 store
and 160,062/160,062 fulfillment, `Q == L_s`, 100% of stamped demand each -- matching the record's
own `floor_line_skus` / `floor_line_demand_share`. `coverage_days`, the lead and `safety_days`
bind on NOTHING; the entire declared level is `floor_lines * E[line]`, ~1.5 lines per SKU. The
fill law is therefore exactly one event -- a second line inside the replenishment lead -- and
`floor_lines` is the only lever the solve has.

**Decisions taken this session that survive the falsification** (user, 2026-09-12):
1. **The sampler stays out of scope.** Re-weighting the affinity lift to preserve the marginal
   line share was considered and rejected: the record must model the demand the sim generates,
   and re-weighting would invalidate the affinity structure the fulfillment channel exists to
   exercise, on every archived run. Recorded on the map's Out-of-scope entry.
2. **Derived first, generator-draw as the named fallback** -- see the charter amendment on the
   map (a constant may not be measured from a WAREHOUSE run; characterising a declared
   generator's own output is not a calibration simulation).
3. **`_served_under_lead`'s frequency law is wrong independently of the rate.** The sampler draws
   DISTINCT SKUs per batch and the era releases one batch a day, so a SKU takes at most one line
   per day: `N ~ Binomial(K, p_s)`, not `Poisson(K * rate_s)` (`coverage.py:350`). Panjer covers
   the whole (a,b,0) class, so this is a parameter change, not a new derivation
   (`a = -p/(1-p)`, `b = (K+1)p/(1-p)`, `g(0) = (1-p)^K`; `f(0) = 0` already holds). It moves the
   same direction as any rate fix -- at `K = 2`, mean 0.9, Poisson puts 0.407 on "no prior line"
   where Binomial puts 0.303. It is NOT the gap, and it must ride whatever form this ticket
   finally decides rather than landing alone. Limitation to state when it lands: at
   `releases_per_day > 1` a SKU can take more than one line a day and the binomial is wrong
   again -- pinned to the era, the way `coverage.py:376` pins the supplier-lead rounding.
4. **If the floor solve cannot clear sqrt(0.95) inside `_MAX_FLOOR_LINES`** (128 lines per SKU,
   `coverage.py:84`), the confidence is **declared per channel** -- store and fulfillment each
   stamping what they can hold, the audit judging each against its own -- as an amendment to
   ADR-0004 rather than a workaround, because the finding is precisely that the two channels do
   not face the same kind of demand. Only fires if the solve refuses.

**Suspended pending 39**, all three downstream of "correct the rate": buying a sixth
comparability break for a bigger fulfillment warehouse; carrying one rate through
`expected_travel` / `staffing` / the fragmentation transient as well as `daily_demand`; and
stamping a derivation identity on the record against rebuild drift (`declare_from_record`
recomputes the share from the catalogue, so any rate-law change would make historical rebuilds
field a different warehouse with nothing stamped to detect it -- the `floors_at` / `holds_at`
pattern is the answer whenever a rate change does land).
