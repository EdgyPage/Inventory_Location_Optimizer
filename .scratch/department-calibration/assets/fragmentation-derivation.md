# The stationary-fragmentation closed form — derivation for review

Ticket: [Derive the stationary fragmentation closed form](../issues/34-derive-the-stationary-fragmentation-closed-form.md)
(department-calibration 34). Module: `Optimization/simconfig/fragmentation.py`. Check
script: [validate_fragmentation.py](validate_fragmentation.py) (run name in, archive found
through `COMPARISON_OUTPUT_DIR`). Precedent: [expected-travel-derivation.md](expected-travel-derivation.md).

The question: how many bins does a SKU hold in steady state under the era's rules, and how many
extra bins per bucket does that add to the requirement the planner sizes from?

## 0. What the simulator does (read from the code, not assumed)

Every rule the chain reproduces has one body in the sim; the chain calls none of them at run
time, so the anchors are the contract:

| rule | where | what it does |
|---|---|---|
| base stock | `inventory_reorder._fire_reorders` | after a batch, a SKU whose position (on hand + queued + in transit) is at or below `rp` orders `Q + pipeline − position`; on the line floor `rp = Q − 1`, so every line orders exactly what it took |
| lead 0 lands before the next batch | `check_reorders` steps 1–5 | fire, then release (a lead-0 order is released in the same call), then `_stock` places the queue — before the next batch picks |
| smallest-first drain | `Workload_Builder.drain_sku` | a SKU's demand in a batch drains its bins in ascending on-hand order, ties by `location`; a line for a SKU appearing twice in a batch is one aggregated quantity |
| empty-first top-up | `Inventory_Manager._candidates_raw` → `_execute_placement` | every arriving unit takes an EMPTY bin of its own tier; the own-bin rung (`_top_up_own_bins`) fires only when the whole tier chain is dry and is judged at zero (32, 33) — both finished runs read `put_topups = 0` |
| plan-slot packing | `Storage_Primitive.viable_storage_units` (the `stock_plan` branch) | a lot of `r` fills the plan's leading slots in order, `take = min(per, remaining)`: `r // per` units of `per` and one of `r % per`; a fulfillment plan packs the same way into `FulfillmentBin`s |
| size tier of a unit | `Pallet` / `Singleton` / `FulfillmentBin` constructors, `binkey_of` | a pallet's tier is the smallest that fits its stacked quantity, so a pallet of 1 can sit in `small` where the plan's pallet of 4 sat in `medium`; a singleton is always `singleton`; the bin KEEPS its tier as picks thin the unit |
| nothing is lost | `strategy_runner` carryover (`unpicked_unstocked`, `unpicked_daycut`) | a line the shelf could not fill is re-offered next batch against a full shelf |
| snapshot timing | `strategy_runner` (`free_bin_depth` after `check_reorders`, `save_bin_keyframe` in the pre-pick block) | `batch_stats.free_bins` and the keyframe at batch `i` are the state after `i` days — batch 0 is the setup state (33 checked it bucket by bucket) |

Two facts of the reference pair that make the check clean: every SKU sits on the line floor
(base stock, `floor_lines` 1.2728 / 1.2668), every SKU has lead 0, and no top-up or spill
fired in 40 days on either leaf.

## 1. Inputs, all known at setup

Per SKU: the stamped line law `Demand.line` (`poisson_max1(λ)`; 20 distinct λ on the pair),
the declared `equilibrium_qty` `Q` and `reorder_point` `rp`, the planner's `stock_plan`
(468 distinct plans; slots of `per` 1–16, up to three slots), the regime, and the dimensions
(which decide the tier of every kind the plan can produce). Per section: the declared line
count `n` and each SKU's line share `π_s = freq_s / Σ freq` — only the TRANSIENT reads these;
the stationary law reads no line rate at all.

## 2. The chain

**State.** A unit is `(q_now, single, n0)`: the items it holds now and the KIND it was packed
as — singleton or pallet, and the quantity it was packed with. The kind decides the tier, and
the bin keeps the tier while picks thin the unit. A state is the sorted tuple of units;
ascending order IS the drain order. The fielded state `f` is `packing(Q)`: the plan itself.

**One line of `q`**, with `S` the items on the shelf:

- `q < S`: drain `q` smallest-first (ties between units of equal quantity and different kind
  split uniformly over the tied units — the built layout interleaves every tier's aisles across
  the whole id range, aisle ids 1–1282 for the store's five tiers, so `location` order carries
  no information across tiers); if the shelf is then at or below `rp`, `packing(Q − on_hand)` lands in fresh
  bins. Base stock is `rp = Q − 1`: every partial line lands its own quantity.
- `q ≥ S`: the shelf empties, the lot of `Q` refills `f`, and the remainder is re-offered next
  day against a full shelf — so the state after the whole line is `f` with the residual
  `(q − S) mod Q` drained and repacked. `staffing.fired_lots` prices the same line as lots of
  `Q` and one of the residual (ticket 28); the two closed forms agree by construction.

**Stationary law.** `π = π P` over the states reachable from `f`. Every line that empties the
shelf regenerates the chain, so the reachable set holds one recurrent class. Solved by power
iteration on the lazy chain `(P + I)/2` (aperiodic whatever cycle the drain runs in; 446 of the
pair's 489 classes converge to an L1 step of 1e-12 in at most 78 steps, 43 are single-state), with
a pinned direct solve of the balance equations for a chain still moving after 2,000 steps (a rare
partial line next to the resets, or a long drain cycle above the floor — the test suite has one).
The landing state after a shelf-emptying line is built on demand per residual: a table for every
residual below `Q` was quadratic in `Q` and cost ten minutes on a shelf of a thousand items.

**Readings.** `E[units of kind k]` under `π`, `E[bins] = Σ_k`, the bin-count law `{bins: p}`, and
`stationary_extra = E[bins] − |f|`.

**Transient.** `μ_j = μ_{j−1} P` from `μ_0 = δ_f`, kept 64 lines deep (`J_MAX`; beyond it the
stationary law). A SKU seeing `λ_s` lines a day is at day `t` the Poisson(`λ_s t`) mixture over
`j` — `λ_s = n π_s`, or a rate a caller hands in per SKU (§4 says why that seam exists).

**Sharing.** The chain depends on (regime, law, plan, `rp`) only — the tier is a per-SKU lookup
through the packer's own unit classes (`bucket_of`, cached on regime/handling/category/dims/kind).
The reference pair's 400,000 SKUs are 489 classes; 395 store, 94 fulfillment. Every class on the
pair solves in 16 s total; the section aggregation is vectorised per class and per day.

**Per bucket.** `expected_extra = E[units landing in the bucket] − fielded`, summed over the
section's SKUs. It can be NEGATIVE: a plan's singleton or small-tier remainder unit migrates down
a tier under churn and never comes back until a line empties the whole shelf. The section sum is
what the shelf needs beyond its declaration.

## 3. What is not modelled (so the residual has a name)

- **Supply jitter.** `_fire_reorders` receives `max(1, round(N(lot, lot·cv)))`; the chain lands
  exactly what the position rule ordered. The catalogue's `supply_cv` runs to 0.15.
- **Lead.** Taken as zero (every catalogue today). With a lead the multiset a lot lands as is the
  same; its timing against the lines in between is not. The record counts `positive_lead_skus`.
- **Two lines in one batch** are one aggregated quantity in the sim; the chain treats each line
  alone (under 1% of a fulfillment SKU's days on the pair, far less on the store).
- **The tie split** across tiers (above) is a symmetric assumption, not a measurement (the
  code reviewer's correction from per-kind to per-unit weights moved no number on the pair).
- **Spills and own-bin top-ups** are judged at zero by the equilibrium check, not modelled.
- **The line-rate law of the transient** — see §4; the stationary law does not read it.

## 4. The check against the two finished runs (no new runs)

`comparison_20260909_204522` (the 2026-09-09 reference pair, 40 era days, `fifo`, both channels;
`opt_fifo` and `uni_fifo` are byte-identical) and `comparison_20260910_100637` (the same command,
2 batches, with the per-bucket `free_index`). Realized extra bins after `t` days is
`free_bins[0] − free_bins[t]` on the leaf (the leaf simulates only its own section, so the whole-
geometry total moves by that section alone; 32's correction), and per tier the keyframe
sidecar's bin counts at batches 0 and 25.

### 4a. The chain scored at each SKU's REALIZED line count

The keyframes give every SKU's bin change over 25 days and the `picks` table how many batches
it was picked in. Scoring the chain's trajectory at that count separates the shelf physics from
the line-rate law:

| leaf | SKUs lined by day 25 | realized Δbins | chain at the realized counts | residual |
|---|---|---|---|---|
| store | 14,219 | +4,478 | +4,333 | −3.2% |
| fulfillment | 41,669 | +12,874 | +12,482 | −3.0% |

By the number of line-batches `j` a SKU saw (mean Δbins per SKU, realized / chain):

| j | store SKUs | store | fulfillment SKUs | fulfillment |
|---|---|---|---|---|
| 1 | 11,399 | +0.304 / +0.301 | 27,033 | +0.302 / +0.300 |
| 2 | 2,314 | +0.366 / +0.325 | 8,530 | +0.332 / +0.298 |
| 3 | 393 | +0.328 / +0.292 | 2,630 | +0.329 / +0.299 |
| 4+ | 113 | +0.345 / +0.341 | 3,476 | +0.294 / +0.301 |

The one-line effect — the whole mechanism of 32's finding — is reproduced to a third of a percent
on both sections. The −3% is carried by `j = 2, 3`, where the realized second line adds a little
more than the chain's: the supply jitter (a lot that lands a unit short or long changes the next
line's drain) and the aggregation of two lines in one batch are the named causes, in that order.
**Tolerance stated: the chain's per-SKU expectation is trusted to ±5% of the realized bin change.**

### 4b. The transient at the declared line share

| store, day | realized | expected | resid |
|---|---|---|---|
| 10 | 1,599 | 1,744 | +9.1% |
| 20 | 3,524 | 3,429 | −2.7% |
| 25 | 4,478 | 4,249 | −5.1% |
| 39 | 6,763 | 6,471 | −4.3% |
| window 20–39 | 3,239 | 3,042 | −6.1% |

Per tier at day 25 (store): medium +3,113 realized / +3,105 expected, small +6,701 / +5,904,
singleton −5,564 / −5,073, large +212 / +293, extra-large +16 / +20. The tier MIGRATION — the
singleton bucket emptying into small while the section grows by less than a fifth of the flow —
is the closed form's own prediction, and it lands within 10% per tier.

| fulfillment, day | realized | expected | resid |
|---|---|---|---|
| 10 | 6,612 | 7,717 | +16.7% |
| 25 | 12,874 | 16,452 | +27.8% |
| 39 | 18,279 | 22,438 | +22.8% |
| window 20–39 | 7,305 | 8,585 | +17.5% |

The fulfillment over-read is NOT the chain (4a): it is the line-rate law. At the declared share
the Poisson thinning touches 54,995 SKUs by day 25; the run touched 41,669. The batch sampler's
affinity lift (`Workload_Builder._lift_weighted_sample`: `weight(B) = freq(B) · Π lift(A, B)`)
spreads a fulfillment section's lines over SKUs almost flat across the frequency deciles
(realized 0.42–0.60 lines per SKU by day 25 where the share predicts 0.13–0.96; correlation
between frequency and realized lines 0.04) and clusters them (variance/mean 3.3 in the top
decile). The store is insensitive because almost every touched SKU sees exactly one line
(14,219 touched vs 14,102 predicted). Day 1 over-reads on both leaves for a known reason: batch
0 is a mild ramp (memory `batch-95-flat-spot-is-shared-demand`).

So: the stationary stamp needs no line rate and is trusted as in 4a; the transient is exact per
line and only as good as its per-SKU rate, which is why `section_fragmentation` takes
`lines_per_day_by_sku` beside the share. The trajectory band (fog) should read the sampler's
own rate through that seam.

### 4c. The two-batch run

Same pair; day 1 reads 121 / 177 (store) and 561 / 856 (fulfillment) — the batch-0 ramp — and
its per-bucket `free_index` at batch 0 equals the record's setup free on all 63 buckets (33).

## 5. What the closed form says about the reference pair

| section | fielded bins/SKU | stationary | expected extra | of the requirement | of the setup free |
|---|---|---|---|---|---|
| store | 4.205 | 4.465 | +62,305 | 6.2% | 28% of 220,817 |
| fulfillment | 6.554 | 6.856 | +48,204 | 4.6% | 26% of 187,793 |

Where it comes from: the extra is carried by plans whose units hold 3–6 items in ONE or a few
units (`((False, 4, 1),)` 3,654 SKUs → +3,260 bins; `((False, 3, 1),)` +3,094; `((True, 3, 1),)`
+2,444; `((True, 2, 1),)` +2,325). A plan of single-item units never fragments (bins = on hand);
a plan of 2-item units with a trailing 1 never does either (a remnant of 1 is a fresh 1); the
fulfillment's `((True, 2, k),)` plans carry exactly one dangling unit half the time (+0.5).

**The store's small-pallet buckets run dry in steady state.** Every singleton remainder that is
picked comes back as a pallet of 1 in `small`, and never returns to `singleton` until a line
empties the whole shelf. The stationary extra of six `small` buckets exceeds their setup free by
two to three times:

| bucket | requirement | setup free | stationary extra | implied fill |
|---|---|---|---|---|
| conveyable/food/small | 63,382 | 12,618 | +33,810 | 0.652 |
| conveyable/electronic/small | 44,379 | 9,621 | +24,476 | 0.645 |
| conveyable/clothing/small | 10,048 | 1,952 | +7,880 | 0.560 |
| conveyable/seasonal/small | 9,166 | 2,834 | +4,974 | 0.648 |
| non-conveyable/food/small | 6,239 | 1,761 | +3,332 | 0.652 |
| non-conveyable/seasonal/small | 6,037 | 1,963 | +3,270 | 0.649 |

while eleven `singleton` buckets read a negative extra (conveyable/food/singleton −27,004 of a
49,350 requirement). The 40-day run had the small buckets at +6,701 of 12,618 free by day 25 —
on that slope they exhaust around day 47, a spill up to `medium` follows (judged at zero by the
rework clause), and the store's missed share would climb. The fulfillment section has no
bucket whose extra exceeds its free. This is the number 35 exists to size: the fill is per
BUCKET, and a bucket's headroom must cover the migration into it, not only its own churn.

## 6. Design

- `Optimization/simconfig/fragmentation.py` (pure; no CONFIG, no file, no RNG): `packing`,
  `drain_all`, `SkuChain` (stationary + transient per class), `poisson_weights`,
  `section_fragmentation` (per-bucket sums with the optional transient and the per-SKU rate seam).
- The stamp: `era_coverage.stamp_fragmentation`, called by `fixed_point` after the
  fielded-equals-declared check, in EVERY mode (like the fill rate). Each
  `coverage.final.<ch>.fielded.buckets[]` row gains `expected_extra` (0.0 on a bucket no plan
  reaches; a kind whose bucket the plan never built RAISES), and the block gains
  `fielded.fragmentation = {expected_extra, provenance: derived, method, n_classes,
  capped_classes, positive_lead_skus, fielded_bins_per_sku, stationary_bins_per_sku,
  seconds}`. Two refusals guard the contract: a plan that packs less than the declared
  order-up-to (the ledger would order past the plan) and a class past `STATES_MAX`
  (200,000 states; the pair's widest holds ~9,000) both raise at setup rather than price
  something else or run on.
- Cost at setup: ~16 s for the chains plus the per-SKU tier lookup on the reference pair, once
  after the fixed point converges; it does not run inside the rounds.
- Tests: `Tests/unit/test_fragmentation.py` — the packing against `viable_storage_units` on real
  orders (store mixed plan, fulfillment plan), the drain and its tie split, the hand-computable
  two-outcome chain (stationary, law, trajectory, day mixture), base stock vs above-floor,
  single-item plans, the Poisson weights, the section sum reconciled with
  `bucket_requirements`, the tier migration with a negative bucket (and the direct solver on a
  slow mixer), the rate seam, purity, and the stamp through `stamp_fragmentation` and through
  `fixed_point` with the coverage tests' fake planner.

## 7. For the user before 35 builds on it

1. **Tolerance.** ±5% per SKU on the bin change (4a). Accept, or name the jitter as the next term.
2. **The transient's rate.** Keep the line share as the default (consistent with every other
   closed form on the record) and let the trajectory band read the sampler's rate through the
   seam — or derive an affinity-aware line share once, for all of them. Recommendation: the
   former; the latter is its own ticket and touches 13's residual on fulfillment too.
3. **Negative buckets.** `expected_extra < 0` on a bucket is real (the singleton buckets). 35's
   fill `requirement / (requirement + extra)` exceeds one there; the declared minimum headroom
   floors it. The small-pallet buckets are the ones that move the reference warehouse.
4. **Lead.** The chain prices lead 0 and stamps the count of SKUs with a lead. A lead-aware chain
   is fog until a catalogue carries one.
