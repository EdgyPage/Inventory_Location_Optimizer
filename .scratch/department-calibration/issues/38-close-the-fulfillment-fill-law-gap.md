# Close the fulfillment fill-law gap

Type: grilling
Status: resolved
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

2026-09-12, after [Measure the repeat structure and the realized lead
distribution](39-measure-the-repeat-structure-and-lead-distribution.md) resolved. **This
ticket is now unblocked, and its question has narrowed to one shape.** Read 39's answer
before starting; the short version:

- **Temporal dependence and the lead distribution are both dead.** Not "small" -- dependence
  across days is structurally impossible in this sampler (independent per-batch seeds, no
  cross-batch state) and measures NEGATIVE on fulfillment (-0.0089); the lead's realized pmf,
  reconstructed exactly rather than inferred, is worth +0.0007 (0.9%). The parent's Little
  lead was also confirmed whole, not partial (the receiving and put-away queues add
  <= 0.031 d). Do not re-open either.
- **The form is under-pricing its own event.** `P(>= 1 prior line for the same SKU within K
  days | a line)`: the record says 0.0414, the share-law-true control says 0.0399, reality is
  **0.1494** on fulfillment (3.74x) and 0.0105 against 0.0066 on store (1.59x).
- **A single multiplier on the declared rate, fitted to that probability alone, recovers 71%
  of fulfillment's gap and 52% of the store's** -- both leaves moving the same way, which no
  earlier variant managed.

**So the decision this ticket owes is: what does the floor solve against, given that the
realized line count is far more concentrated across SKUs than the declared share?** It is a
SHAPE on the section, not a per-SKU rate: substituting realized per-SKU rates zeroes the 78%
of fulfillment SKUs a finite window never touches and moves the answer backwards (measured,
twice). The three suspended decisions above (a sixth comparability break; carrying one rate
through `expected_travel` / `staffing` / the fragmentation transient; the `floors_at` /
`holds_at` derivation identity) were all suspended on "correct the rate" and now have a
concrete correction to be judged against. Decision 3 (`Binomial(K, p_s)` for `N`) also stops
being a small correction: at the corrected rate the Poisson/Binomial divergence is wider than
it was at the declared one, and it moves the same direction, so it may belong inside whatever
form is chosen rather than after it.

29% of fulfillment's gap and 48% of the store's are still unexplained; 39's answer names the
candidates and deliberately puts no order on them.

## Answer

**`pi_s` is the wrong marginal.** The record prices a SKU's prior lines at its BASE WEIGHT
SHARE (`freq / sum freq`), but the sampler draws `k` DISTINCT SKUs per batch WITHOUT
replacement, multiplying each survivor's weight by `prod lift(A, B)` over the partners already
drawn (`Warehouse/picking/Workload_Builder.py:104,116`). A weight share is not an inclusion
probability. The floor solves against the **draw probability** `p_s` instead -- the probability
the declared sampler puts SKU `s` in a batch -- and `p_s` is DEFINED as what that sampler does,
characterised by a generator-only draw. `N ~ Binomial(K, p_s)` replaces the compound Poisson in
the same change.

### Why this and not 39's multiplier

39's `m` is a fitted stand-in for a structural quantity, and the structure predicts `m`'s own
channel asymmetry without being told it. Expected already-drawn cluster-mates per candidate is
`cluster_size * k / N`:

| | store | fulfillment |
|---|---|---|
| batch fraction `k / N` (`settings.py:270-271`) | 0.00245 | 0.01814 |
| cluster size (`generate_affinity.py:90`) | 80 | 80 |
| **expected drawn cluster-mates per candidate** | **0.20** | **1.45** |
| 39's fitted `m` | 1.739 | 3.968 |

The multiplicative reinforcement at `Workload_Builder.py:116` therefore engages ~7.4x more often
per fulfillment draw, and each hit multiplies by ~4-5 (stored lifts are the top-20 tail of
`U(1, 5)`, `generate_affinity.py:329-341`). Meanwhile `relative_frequency` DISPERSION is
close across the two sections and tilted the WRONG WAY to explain anything -- measured on the
reference catalogue's own `inventory.db`, fulfillment CV **0.5813** against the store's **0.6389**,
concentration `n * sum(pi^2)` 1.3380 against 1.4082. The store is the MORE dispersed section and
the more concentrated one by share, yet it is fulfillment whose realized lines concentrate; a
frequency story predicts the opposite ordering. It is one mechanism at two sampling densities.

(Corrected 2026-09-12 while starting
[Characterise the draw probability](40-characterise-the-draw-probability.md): this answer first
cited CV 0.577 / 0.580 from the UNIFORM profile, on the belief that the reference pair was
`mixed_realistic_lt0`. It is `mixed_realistic_bell_lt0` -- the bell profile, whose `BELL_STORE_FREQ`
spreads category means 0.05-0.80 against a tight `BELL_FF_FREQ` mixture. The conclusion is
unchanged and slightly strengthened; the numbers and the attribution were not.) Fulfillment also collapses to ONE `(handling, category)`
group against the store's twelve (`generate_inventory.py:407-409`, `:285`), so its clusters sit in
a single densely-sampled pool.

This is also why 38's own rate substitution failed and 39's `m` closed only 71% / 52%: both
operate on a per-SKU RATE, and the defect is in the map from declared weight to realized
inclusion -- a different function, not a scaled one.

### The form, as decided

1. **The corrected object is the marginal itself, not a shape on top of it.** `p_s` replaces
   `pi_s` wherever the record reads a per-SKU rate. Rejected: a declared dispersion parameter
   beside `pi_s` (39's `m` is its one-parameter instance and it lands short on BOTH leaves by a
   similar proportion -- the signature of a stand-in, not of a missing second parameter); and
   firing decision 4's per-channel confidence now, which answers "the solve refuses" before the
   solve has been asked.
2. **`p_s` is defined by a generator-only draw, permanently -- not derived, and this is not the
   charter's fallback being taken reluctantly.** The lift is sequential over a without-replacement
   draw, so an exact closed form is a sum over subset orderings and every tractable approximation
   is an unvalidated model of a model. A generator draw has neither defect the charter's
   "derived first" exists to prevent: it is a pure function of declared inputs (catalogue
   frequencies, `affinity.db`, the batch fraction and cv, a seed), it touches no warehouse and no
   simulation, and it is repeatable to any precision bought. Chasing the closed form would buy
   LESS fidelity than the fallback, which inverts the rule's purpose.
3. **There is no fixed-point circularity, which is what makes (2) affordable.** Under the era the
   two `units_per_line` cancel exactly (`era_coverage.py:195,230-232` against `:96`), so
   `mean_fraction == STORE_DEMAND / FF_DEMAND` -- declared constants. `k` is a declared fraction
   times the section size and does NOT depend on `n`. `p_s` can be characterised once, before the
   solve, and never re-drawn inside it. Cost at era batch sizes is ~0.02-0.04 s/batch
   (`Workload_Builder.py:68` scaled from k/N 0.15-0.20 down to 0.00245 / 0.01814), and
   `batch_precompute.precompute_batches` already parallelises across a spawn pool with no
   warehouse in the loop.
4. **One rate, everywhere, in one commit.** `daily_demand`, `expected_travel`, `staffing` and the
   fragmentation transient all take `p_s`, not just the fill law. Two per-SKU demand rates in one
   record is the failure this map has paid for twice (`cut-is-a-level-not-a-flow`,
   `carryover-two-producers-one-key`), and pricing the PROBABILITY of a prior line at one rate
   while the STOCK that line draws down uses another is incoherent on its own terms.
   Consequence accepted deliberately: today 100% of both sections sits at the line floor, so
   `coverage_days`, the lead and `safety_days` bind on nothing; a corrected `p_s` should lift the
   busy SKUs OFF the floor, which is the first time the declared coverage would bind on anything.
5. **`N ~ Binomial(K, p_s)` rides inside the form, not after it.** Under (1) it stops being the
   small correction 38 first judged it: `p_s` is a probability, not a rate -- the sampler draws
   distinct SKUs and the era releases one batch a day, so a SKU takes at most one line per day --
   and a Poisson parameterised by a probability breaks outright as `p_s` approaches 1, which is
   where the busy fulfillment SKUs now live. Panjer covers the whole (a,b,0) class
   (`a = -p/(1-p)`, `b = (K+1)p/(1-p)`, `g(0) = (1-p)^K`), so it is a parameter change. Its
   increment is REPORTED separately even though it ships together. The `releases_per_day > 1`
   limitation is stated where `coverage.py:376` pins the supplier-lead rounding -- pinned to the
   era, not solved.
6. **The characterisation is a shared-asset stage with a fingerprint cache**, run per
   (pair, channel) ahead of `era_coverage.fixed_point` in `build_shared_assets`
   (`sim_assets.py:190`), on the `ensure_batches` pattern (`batch_precompute.py:169`). Rejected: a
   catalogue artifact beside `affinity.db` (the batch fraction is a RUN declaration in
   `settings.py` and `--max-skus` moves `len(section)`, so it would need keying on the run's
   declaration anyway); and computing it lazily inside `rescale_section` (`coverage.py` "imports
   no CONFIG and touches no file" is load-bearing -- `p_s` arrives as an ARGUMENT, exactly as the
   stamped line law does).
7. **The run's own `seed_batches`, drawn out to M batches**, so the run's actual script is a
   PREFIX of the characterisation and there is no second demand stream to reconcile. `M` is
   declared by requiring the plug-in `p_hat_s = c_s / M` and the unbiased factorial-moment
   estimate (`c_s (c_s - 1) / M (M - 1)`) to agree within tolerance -- which turns "how big is M"
   from a guess into a measurement. The plug-in is what enters the record; the moment estimate is
   a reported diagnostic, not a second rate. This matters because the fill law's line-weighted
   functional is CONVEX in `p_s`, so estimator noise inflates it upward -- the same Jensen artifact
   that was worth two thirds of 38's apparent movement at M = 20. The store binds M, not
   fulfillment (mean count ~25 against ~181 at M = 10,000). Rejected: shrinking `p_hat_s` toward
   `k * pi_s`, which reintroduces the wrong marginal as a prior.
8. **Era-only; flag-off stays byte-identical.** `coverage` runs in every mode (ADR-0002), so this
   would otherwise move flag-off stock levels too. Both prior breaks -- derived fill (fourth) and
   the lead-aware record (fifth) -- landed era-only, and the flag-off path has no first-time
   confidence and no floor solve, so a corrected `p_s` buys it nothing while costing every
   archived flag-off run its comparability. Rejected: a flag of its own, which adds a sixth seam
   to maintain (`config-knob-has-five-seams`) for a switch nobody would set independently.
9. **The gate is generator-side and declared BEFORE the measurement.** The record's own
   line-weighted `P(>= 1 prior line for the same SKU within K days | a line)` must match 39's
   empirical -- store **0.01052**, fulfillment **0.14938** -- within **10% relative on both
   channels**. Relative, not absolute: the two are 14x apart, so an absolute band is vacuous on one
   and brutal on the other. No warehouse is built to run this gate. The equilibrium instrument's
   `supply` band on one re-run is the CONFIRMING gate, not the primary one.
   Two rules fixed now rather than after the numbers land:
   - **Fulfillment closing while the store overshoots is FATAL**, by rule. It is the signature of
     the two dead variants (38's best reached fulfillment 0.1370 only by driving the store to
     0.0909 against a realized 0.0302) and it should not be a judgement call made afterwards.
   - **Landing short on both leaves is a REJECTION, not a stamped residual.** The whole value of
     this form over `m` is that it is derived; "close enough" is a much weaker defence for a
     derived quantity than for a fitted one, and a stamped known-bias is how a wrong constant
     survives for months.
10. **Rebuilds read the artifact and REFUSE on drift.** `p_s` is a fingerprinted artifact in the
    pair directory, travelling with the run tree (so a `COLD_DRIVE` rebuild resolves), plus a
    derivation identity on the record. `declare_from_record` recomputes the share from the
    catalogue (`era_coverage.py:346`), so without this a rate-law change would make every
    historical rebuild field a different warehouse with nothing stamped to detect it -- this is
    the `floors_at` / `holds_at` pattern applied to the thing that actually needed it, and the
    refusal is the important half. Rejected: stamping ~400k floats per pair on the record; and
    stamping only the inputs and re-drawing, which makes every analysis pass pay the draw.
11. **Whatever warehouse the solve asks for is the one we buy.** Fulfillment sits at floor 1.267
    with a stamped miss of 0.0251 against a 0.0253 target, so the solve is on the boundary; a
    corrected rate raises the miss at that floor to roughly 0.08 (39's re-priced figure) and the
    floor must then buy enough shelf to survive a SECOND prior line. EXPECTATION, not a
    measurement: nearer 2-2.5 lines than 4x, because `P(>= 2 prior lines)` falls off steeply from
    `P(>= 1) = 0.149`. That is plausibly a considerably larger warehouse than the current 2,774
    aisles and a slower campaign, and a bin cap cannot contain it (`a-bin-cap-is-self-defeating`:
    a smaller warehouse is a shorter trip, a higher derived lines/day, and BIGGER levels).
    Rejected: holding the geometry and lowering the declared confidence to what it supports --
    that chooses the warehouse and lets the confidence fall out, which is the same inversion this
    map's destination exists to prevent. 38 decision 4 (confidence declared per channel) survives
    as the named fallback, firing ONLY if `_MAX_FLOOR_LINES` actually refuses.
12. **Glossary, and an ADR.** `pi_s` keeps the name **line share** (a weight); `p_s` is the
    **draw probability** (an outcome). ADR-0006 records the form and must AMEND ADR-0004, whose
    first-time confidence is priced through exactly the law being replaced. The rejected
    alternative it must carry, with 39's numbers, is the fitted multiplier.

### What this does NOT claim

The mechanism is argued from the sampler's structure and from its prediction of the channel
asymmetry; it is **not yet measured**. Nothing here is a finding about `p_s`'s value. Decision 9
exists precisely so that the form can be falsified on the generator, in minutes, before a sixth
comparability break is bought.

### Graduated

The fog patch **An affinity-aware line share** is cleared from the map: it asked whether the record
should carry a sampler-faithful per-SKU rate and how one is derived without a run, and both halves
are answered above. Four `task` tickets carry the build, in order:
[Characterise the draw probability](40-characterise-the-draw-probability.md) ->
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) ->
[Land the draw probability through every closed form](42-land-the-draw-probability.md) ->
[Re-run the reference pair and record the form](43-rerun-and-record-the-form.md).

The inbound campaign's
[Re-run the gate and fix the fee threshold](../../inbound-optimization/issues/29-rerun-the-gate-and-fix-the-threshold.md)
was blocked on THIS ticket and has been re-pointed at the last of the four: resolving a decision
does not give the campaign a corrected era, and the old edge would have turned the inbound map
green on a warehouse that does not exist.
