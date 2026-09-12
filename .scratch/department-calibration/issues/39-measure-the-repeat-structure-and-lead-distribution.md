# Measure the repeat structure and the realized lead distribution

Type: task
Status: resolved

Graduated 2026-09-12 from
[Close the fulfillment fill-law gap](38-close-the-fulfillment-fill-law-gap.md), whose leading
hypothesis (the per-SKU line RATE) was measured and explains only 14% of the gap. AFK, and
answerable entirely off `comparison_20260912_055947` as it sits on disk -- no new run. 38 is
blocked by this ticket; the campaign is blocked behind 38.

## Question

The fill law reduces, on this catalogue, to a single event: **a line arrives and the SKU's own
previous line is still being replenished.** Every SKU in both sections sits at the line floor
(239,938/239,938 store, 160,062/160,062 fulfillment; `floor_line_demand_share` = 1.0), so the
shelf is ~1.5 lines everywhere and neither `coverage_days`, the lead nor `safety_days` binds on
anything. `_served_under_lead` (`Optimization/simconfig/coverage.py:350`) prices that event as a
compound Poisson at a CONSTANT per-day rate over a lead drawn from `transit_day_law`. Fulfillment
realizes 0.1044 against a stamped 0.0251; the store realizes 0.0302 against 0.0248.

38 established that the section's rate is right (realized / predicted = 0.9819 fulfillment,
1.1986 store) and that substituting realized per-SKU rates moves fulfillment the WRONG way.
Two structural assumptions in that form remain unmeasured, and either could carry the residual.

**1. Independence across days.** The sampler draws `k` distinct SKUs per batch with weights
`freq(B) * prod lift(A, B)` mutated by each prior pick in the same batch
(`Warehouse/picking/Workload_Builder.py:104,116`), so a SKU's cluster-mates being drawn drag it
back into consecutive batches. Fulfillment touched 34,993 SKUs against 46,325 predicted (-24.5%)
while total lines moved -1.8%, and 1,467 fulfillment SKUs saw 6+ lines against 4 predicted. A
20-day mean rate -- what 38 substituted -- smooths this away entirely, so nothing measured so far
can see it.

Measure: the empirical `P(a line for SKU s on day d+j | a line on day d)` for j = 1, 2, 3, per
channel, against the marginal rate. **Exclude re-offered picks from the conditioning event** --
a missed unit is re-offered and picked later, which adds a distinct `(batch_id, sku)` and makes
the line count endogenous to the miss rate it is meant to predict (38 measured 11,946 of 12,875
line-batches in fulfillment's 6+ bucket coinciding with a carryover row). Report the lift over
marginal per channel and, if it is material, over the frequency deciles.

**2. The lead DISTRIBUTION, not its mean.** 38's parent ruled the lead out on Little's law alone
(realized 1.782 d vs stamped 1.766 d). The loss is convex in `K`, so a lead with the same mean and
a fatter tail prices very differently, and fulfillment pushes 30,583 units/day against the store's
6,125 through a shared dock.

Measure: the realized order-to-shelf lead per unit as a DISTRIBUTION, per channel, against the
stamped 138-day transit pmf (`transit_day_law`, top mass {1: 0.500, 2: 0.339, 3: 0.103, 4: 0.034}
at the pilot regime). Then re-price the stamped fill against the realized lead pmf, holding the
rate at the declared share, and report the missed share it produces.

Done when both are measured and each is scored for how much of the +0.0793 fulfillment gap it
accounts for, against the store control's +0.0054. State plainly what is left unexplained.

**Method warnings, all paid for once already:**
- Score any per-SKU quantity at realized counts before trusting it (memory
  `sampler-affinity-flattens-the-fulfillment-line-rate`), and build a synthetic control: draw
  counts from the share law being exactly true, push them through the identical machinery, and
  report the increment over that control, never the raw number. On 38 the control was worth
  +0.040 on fulfillment and +0.053 on the store -- two thirds of the apparent movement.
- A line is a distinct `(batch_id, sku)`, never a row of `picks` (a line splits across bins).
- `demand_flows`' `supply_new` is a documented lower bound on fresh demand
  (`Optimization/simconfig/equilibrium.py:535-547`); say so wherever it is the denominator.
- Resolve run-tree paths with `Optimization.runschema.resolver_for`, never by joining strings or
  by directory name.

## Answer

**Neither assumption carries the residual. The mechanism is the per-SKU line-COUNT
distribution, and it accounts for 71% of the fulfillment gap against a store control that
moves the same way.**

Measured on `comparison_20260912_055947`, cell `k1_off`, days 20-39, all four arms.
Reproducible in one command:
[measure_repeat_and_lead.py](../assets/measure_repeat_and_lead.py) (paths from
`COMPARISON_OUTPUT_DIR`; the rebuild gate reproduces the record's `fill_rate` to
**delta 0.000e+00** on both leaves before anything is substituted).

### 1. Temporal dependence: absent by construction, and NEGATIVE where it matters

Read off the sampler's own `_batches_*.pkl` -- the demand before any pick, miss or re-offer
touches it -- so the rollover contamination the ticket warned about cannot enter at all: a
re-offer is a pick-side event and never a sampler draw. No filtering was needed.

**Structural finding first.** Each batch is sampled from its own
`random.Random(seed_batches + i)` and draws `k` DISTINCT SKUs with the affinity lift mutated
only by picks *within that batch* (`Warehouse/picking/Workload_Builder.py:104,116`). There is
no cross-batch state. Day-to-day dependence is therefore structurally impossible except
through the batch size `k_d`, whose measured lag-1..3 autocovariance is worth at most 1.6% of
the co-occurrence rate on either channel. The "clustering" 38 inferred from
34,993-touched-against-46,325 is not temporal.

Lag lift over a within-SKU day permutation (realized counts kept exactly, alignment destroyed):

| channel | lag 1 | lag 2 | lag 3 |
|---|---|---|---|
| store | 0.846x | 1.808x | 1.657x |
| fulfillment | **0.838x** | 0.977x | 0.981x |

Cross-checked independently on the consecutive-line gap histogram over all 40 days
(49,388 fulfillment pairs): gap 1 = 0.863x, gap 2 = 1.198x, gap 3 = 1.203x. **The lag-1
suppression is real and large** (many sd, on both estimators, on both windows) and the
`k_d` autocovariance does not account for it. It is not explained here, and it is not the
gap: it moves the miss DOWN, and the mass it displaces returns at lags 2-3.

In the currency the fill law actually prices, temporal dependence alone is worth
**-0.00887 on fulfillment** and +0.00123 on store.

### 2. The lead distribution: measured exactly, worth 0.9%

`TrailerTransit.lead_for` is a pure function of `(seed, tag, seq)`
(`Inbound/transit.py:119`), so every trailer's drawn transit is RECONSTRUCTED from
`yard_trailers.seq` -- `dispatched_s = arrived_s - lead_for(seq)` -- not inferred from a level.

| | mean K | E[K^2] |
|---|---|---|
| stamped `transit_day_law` | 1.7656 | 4.2310 |
| drawn, the 298 trailers dispatched in-window | 1.6477 | 3.6275 |
| **realized** (day emptied - day dispatched) | 1.803 - 1.829 | 4.263 - 4.322 |

Yard + door detention: mean 1.13-1.15 d, p90 <= 1.41 d, max 1.84 d. Re-priced through
`coverage.fill_rate` with the realized pmf, rate held at the declared share:

| leaf | stamped | realized-lead | share of its gap |
|---|---|---|---|
| fulfillment | 0.0251 | 0.0255 - 0.0258 | **0.9%** |
| store | 0.0248 | 0.0249 | 1.9% |

**And the parent's Little's law was never partial.** It read `in_transit_qty / units_ordered`,
which covers order -> released-to-dock only (`TrailerTransit.merchandise`). Adding the
receiving and put-away queues moves W by **<= 0.031 d** on any arm (put depth <= 836 units,
`recv_depth` 0 throughout), so no leg was hiding behind the mean. Both the mean and the
distribution are now ruled out.

### 3. What it IS: the record under-prices its own event 3.7x

The fill law reduces to one event, so it was measured directly:
`P(>= 1 prior line for the same SKU within K days | a line)`, K ~ the stamped pmf, pooled
over the window's lines.

| | store | fulfillment |
|---|---|---|
| the record's own Poisson at the declared share | 0.00607 | 0.04140 |
| counts drawn from the share law being EXACTLY true (38's control) | 0.00661 | 0.03991 |
| permutation control (realized counts, days independent) | 0.00929 | 0.15825 |
| **EMPIRICAL** | **0.01052** | **0.14938** |

Against the share-law-true control -- which reproduces 38's -25.2% touched-SKU signature as
the *contrast*, so the finite-window granularity artifact is netted out -- the empirical is
**3.74x** on fulfillment and 1.59x on store. The permutation control sitting ABOVE the
empirical on fulfillment is the same fact as (1): all of the excess is count shape, and
temporal dependence gives a little of it back.

### 4. The score, in missed-share currency

Not a per-SKU rate substitution: 38 measured that and it moves the wrong way, and now we know
why -- it sets `rate = 0` for the 78% of fulfillment SKUs a 20-day window never touched, and
removing their loss outweighs what the busy SKUs add under stamped `d_s` weighting. The
concentration is a SHAPE on the declared section, not a per-SKU rate.

So: the single multiplier on the declared rate that makes the record's own weighted
prior-line probability equal the realized one, with levels, weights, line laws and the lead
pmf all held at the stamp. **`m` is fitted to the prior-line probability alone, never to the
missed share** -- the missed share below is what that one parameter then produces.

| leaf | m | stamped | re-priced | realized | share of gap |
|---|---|---|---|---|---|
| fulfillment | 3.968 | 0.0251 | **0.0816** | 0.1044 | **71%** (+0.0564 of +0.0793) |
| store | 1.739 | 0.0248 | **0.0276** | 0.0302 | **52%** (+0.0028 of +0.0054) |

Both leaves move the same direction and both land short by a similar proportion. That is the
control 38's best variant failed: it reached fulfillment 0.1370 only by driving the store to
0.0909 against a realized 0.0302.

### What is left unexplained

**Fulfillment +0.0229 (29%), store +0.0026 (48%).** Named, not measured, and no order among
them is implied:

- **The N law itself.** 38 decision 3 -- `N ~ Binomial(K, p_s)` not `Poisson(K * rate_s)` --
  moves the same direction and is not in the `m` above, which scaled a Poisson rate. At
  `m * rate` the Poisson/Binomial divergence is larger than at the declared rate, so this is
  no longer the small correction 38 judged it to be.
- **A single multiplier is the crudest possible shape.** It preserves the declared rate's
  RANKING across SKUs; the realized concentration may also re-order it.
- **The store's own residual is proportionally the larger one** (48% vs 29%) on a gap 15x
  smaller, so a mechanism reaching only fulfillment will not close both.

### Method notes for whoever picks up 38

- The store control is doing real work: every variant that reached fulfillment's realized
  value by breaking the store has been wrong so far, twice.
- Scoring at realized lines is what made this visible. The record's fill is a demand-weighted
  average over ALL declared SKUs; the realized miss is carried by the SKUs that get lines. The
  two weightings disagree by a factor of 4 on fulfillment and the disagreement IS the finding.
