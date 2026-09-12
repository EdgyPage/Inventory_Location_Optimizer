# Re-measure the fill-law targets under v3

Type: task
Status: resolved
Blocked by: 44

Graduated 2026-09-12 from
[Fix the sampler's duplicate draws as v3](44-fix-the-sampler-duplicate-draws.md). AFK, and
answerable from the GENERATOR alone -- no warehouse, no simulation, no new run.

## Question

[Measure the repeat structure and the realized lead distribution](39-measure-the-repeat-structure-and-lead-distribution.md)
measured the empirical prior-line probability on a **v2** run:

| | store | fulfillment |
|---|---|---|
| the record's Poisson at the declared line share | 0.00607 | 0.04140 |
| share-law-true control | 0.00661 | 0.03991 |
| **EMPIRICAL** | **0.01052** | **0.14938** |

Those three numbers are the gate in
[Gate the form on the generator](41-gate-the-form-on-the-generator.md). If the era adopts v3 they
are measured against a sampler the era no longer declares, and the gate would be scoring the draw
probability against a target containing the very artifact v3 removes.

Re-measure them under v3 and state which of 39's conclusions survive. Specifically:

1. **The empirical prior-line probability**, per channel. 39 read it off the sampler's own
   `_batches_*.pkl` -- generator output, before any pick, miss or re-offer -- so it is
   reproducible under v3 with a fresh batch script and no run. Use 39's own asset,
   [measure_repeat_and_lead.py](../assets/measure_repeat_and_lead.py).
2. **The concentration signature.** 39 found fulfillment touching 34,993 SKUs against 46,325
   predicted (-24.5%) at -1.8% total lines. Both halves are now suspect in opposite directions:
   duplicate draws SUPPRESS the distinct-SKU count directly, and they also cut delivered lines by
   8.64%. Report how much of the -24.5% was the defect and how much is affinity concentration --
   this is the number 38's whole argument rests on, so it must be re-established rather than
   assumed to survive.
3. **The lag-1 suppression.** 39 measured a real, many-sigma same-SKU suppression at lag 1
   (0.838x fulfillment) that it could not explain and ruled out of scope. Duplicate draws are a
   plausible cause that nobody could have seen at the time: a repeat consumes a draw that would
   otherwise have gone to another SKU. Re-measure it under v3; if it vanishes, say so and amend
   the map's Out-of-scope entry, which currently hands it to the sampler effort as unexplained.
4. **What does NOT need re-measuring, and why.** 39's lead-distribution result is a property of
   `TrailerTransit.lead_for` and the trailer draw, not of the batch sampler; state explicitly
   whether it is untouched rather than leaving it ambiguous.

The realized missed shares (fulfillment 0.1044, store 0.0302) need a RUN and are not this
ticket's -- they are re-established by
[Re-run the reference pair and record the form](43-rerun-and-record-the-form.md).

Done when the gate's targets are restated under the era's declared sampler, each of 39's four
findings is marked survives / changed / dead with the number behind it, and
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) is edited to cite the new
targets instead of the v2 ones.

**Method warnings:**
- Build the synthetic control the same way 39 did -- counts drawn from the share law being exactly
  true, pushed through identical machinery -- and report the increment over it, never the raw
  number. On 38 the control was worth two thirds of the apparent movement.
- A line is a distinct `(batch_id, sku)`, never a row of `picks`.
- Keep the store as a live control: every variant that reached fulfillment's value by breaking the
  store has been wrong, twice.

## Answer

Resolved 2026-09-12. **The targets are not restated, they are REVERSED.** Under v3 the
generator's own behaviour is close to what the record already predicts -- within ~19% on both
channels, and in OPPOSITE directions -- so 38's concentration premise and 39's "the record
under-prices its own event 3.7x" are both dead. The route is re-ordered as a result: the run is
hoisted in front of the form work (user decision).

Reproducible in two commands -- draw the script, then measure it:

    REPO=. python .scratch/department-calibration/assets/draw_batches_under_sampler.py <dir> v3
    python .scratch/department-calibration/assets/measure_repeat_and_lead.py \
        --run comparison_20260912_055947 --pair <pair> --inventory <inventory.db> \
        --batch-dir <dir>

39's asset gained `--batch-dir` (defaulting to the pair directory, so 39's own invocation
reproduces unchanged) and now PRINTS the touched-SKU concentration signature instead of leaving
it to be carried by hand between tickets. **The v2 baseline was re-run first and reproduces 39
exactly** -- lag lifts 0.8461 / 1.8078 / 1.6569 and 0.8383 / 0.9772 / 0.9807, `m` 1.739 / 3.968,
52% / 71%, rebuild gate delta 0.000e+00 -- so every v2-to-v3 difference below is the sampler and
nothing else.

### The measurement

| fulfillment, days 20-39 | v2 | v3 |
|---|---|---|
| lines | 52,598 | 57,407 (+9.1%) |
| SKUs touched vs share-law-true control | 34,683 (**-25.2%**) | 47,910 (**+3.3%**) |
| SKUs drawn on >= half the days | **335** | **0** |
| max appearances per SKU | **17 of 20** | 5 of 20 |
| lag-1 lift | **0.838x** | 1.014x |
| lag-1 co-occurring pairs | **4,815** | 1,037 |
| P(prior line within K) | **0.14938** | **0.03485** |
| ... against the record's Poisson 0.04140 | 3.74x | **0.87x** |
| fitted `m`, share of gap | 3.968, **71%** | 1.000, **0%** |

| store, days 20-39 | v2 | v3 |
|---|---|---|
| lines | 12,491 | 12,623 |
| SKUs touched vs control | 11,894 (+4.0%) | 12,118 (+5.9%) |
| max appearances per SKU | 7 of 20 | 3 of 20 |
| lag lifts 1 / 2 / 3 | 0.846 / 1.808 / 1.657 | 0.848 / 0.842 / **3.022** |
| P(prior line within K) | **0.01052** | **0.00721** |
| ... against the record's Poisson 0.00607 | 1.59x | **1.19x** |
| fitted `m`, share of gap | 1.739, **52%** | 1.189, **13%** |

### 1. The empirical prior-line probability -- the gate's targets

| | store | fulfillment |
|---|---|---|
| the record's Poisson at the declared line share | 0.00607 | 0.04140 |
| share-law-true control | 0.00661 | 0.03991 |
| **EMPIRICAL under v3** | **0.00721** | **0.03485** |

**The two channels now err in OPPOSITE directions**: the record UNDER-prices the store by 19%
and OVER-prices fulfillment by 16%. No single multiplicative correction can close both -- which
is exactly the failure mode 41 pre-registered as fatal, arriving before the form was built.
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) is edited to cite these
numbers, and its question is noted as INVERTED on fulfillment.

### 2. The concentration signature -- DEAD, and it was the defect

38's whole argument rested on fulfillment touching far fewer SKUs than the share law predicts
(-24.5% there, -25.2% as 39 re-measured it). Under v3 it is **+3.3%**, i.e. slightly MORE than
the control. The shortfall was never affinity clustering.

The raw counts make the mechanism unambiguous rather than inferred: under v2, **335 fulfillment
SKUs were drawn on at least half the 20 days and one on 17 of 20**; under v3 the maximum is
**5 of 20 and no SKU reaches half**. Those were the Fenwick-boundary SKUs with `p_s` ~0.75-0.80.
They suppressed the distinct-SKU count directly and they carried the repeat statistic: lag-1
co-occurring pairs fall from 4,815 to 1,037, a 78% drop, while line-days ROSE 9%.

### 3. The lag-1 suppression -- GONE on fulfillment

0.8383x -> **1.0138x**, inside the permutation control's own spread. 45 anticipated this and it
is confirmed: a duplicate draw consumed a slot another SKU would have taken. The map's
Out-of-scope entry, which handed it to the sampler effort as unexplained, is amended.

**The store's lag structure did NOT settle and is not the same finding.** It goes 0.846 / 1.808 /
1.657 under v2 to 0.848 / 0.842 / **3.022** under v3 -- lag 3 rising as lags 1-2 fall. The counts
are small (121 co-occurring pairs against a permutation expectation near 40) but the excess is
many sd, so it is real rather than noise. It moves the aggregate barely: the store's empirical
prior-line probability is 1.09x its share-law-true control, against 1.59x under v2. Reported, not
explained; it belongs with the sampler effort alongside the rest of the lag structure, and it is
too small to carry a fill-law argument either way.

### 4. What does NOT need re-measuring, and what DOES

**The DRAWN lead law is untouched, as 45 expected.** `TrailerTransit.lead_for` is a pure function
of `(seed, tag, seq)` (`Inbound/transit.py:119`), so no batch-sampler change can move it; 39's
stamped-vs-drawn comparison (1.7656 / 1.6477 mean K) stands as measured.

**The REALIZED lead does need a run, and 39's figure is a v2 outcome.** Dispatch-to-emptied
depends on how many reorders the script generates; v3 delivers 9.5% more fulfillment lines, so
the trailer population moves. The `--batch-dir` measurement above deliberately read the same v2
`_site/inbound_*.db`, so the lead block it printed is v2 data and is NOT evidence about v3.

**And the same caveat governs the whole score.** The realized missed shares this ticket scored
against -- store 0.0302, fulfillment 0.1044 -- are **v2-run outcomes**. The "explains 0% / 13%"
rows above compare a v3 generator against a v2 run and are therefore not yet meaningful. They are
reported to show that 39's mechanism does not survive on the generator side, not to claim the gap
is unexplained on the run side.

### The consequence for the route (user decision, 2026-09-12)

There is a strong reason to think the defect inflated the **realized** miss as well as the
predicted one: those 335 artifact SKUs were drawn on 15-17 of 20 days against stock levels sized
for ~1.5 lines per SKU, so they would have been missing almost constantly. **PREDICTION, not
measurement.**

So the run is hoisted in front of the form work:
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md)
is created and unblocked, and
[Characterise the draw probability](40-characterise-the-draw-probability.md) now waits on it
rather than on this ticket. If the realized first-pass fill reads in the supply band under v3,
40 / 41 / 42 are deriving a correction for a gap that no longer exists; if a gap survives, 46
gives it its v3 shape so 41 can be aimed at a live target instead of an inverted v2 one. The run
cost is unchanged -- it is the run 43 always carried, taken earlier.

**38's resolution is invalidated on its central claim** and its map entry is annotated; the
ticket stays closed, because the route did walk through it and its rejected alternatives
(per-SKU rate substitution, the re-weighted lift) remain correctly rejected for their own
reasons.
