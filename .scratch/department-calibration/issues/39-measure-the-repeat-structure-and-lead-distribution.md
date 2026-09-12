# Measure the repeat structure and the realized lead distribution

Type: task
Status: open

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
