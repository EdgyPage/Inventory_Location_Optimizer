# Re-measure the fill-law targets under v3

Type: task
Status: open
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
