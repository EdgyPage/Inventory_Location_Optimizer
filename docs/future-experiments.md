# Future experiment discussion

A scratchpad for levers worth testing in future sweeps. Jot ideas here; promising ones become
their own experiment folder.

!!! note "Notes"
    The seed list below was written after [Experiment 1](experiments/experiment-1/index.md), when
    placement was the only lever on the table. Experiments 2–6 have since answered several of its
    entries and — more usefully — changed the question. Placement turned out to be a **labor**
    lever worth single-digit percentages; layout and scheduling turned out to be **throughput**
    levers worth considerably more. The open questions worth spending a sweep on now are mostly
    about how far the throughput side goes, and whether the two sides really compose.

## Already answered

Keep these here rather than deleting them — knowing a lever was tested and bounded is as useful as
knowing one is untried.

| Lever | Answered by | Result |
|---|---|---|
| Alternate pick-time calibrations | [Exp 1](experiments/experiment-1/comparison-20260624.md) | The ranking is robust; the **margin** is not. Steeper ergonomic penalties widen the placement win from ≈ −1.2 % to ≈ −9.5 %. |
| Objective trade-offs | [Exp 2](experiments/experiment-2/highlights.md) | Found, and sharper than expected: `Compact` wins fulfillment on total task time while losing ≈ 39 % makespan and ≈ 25 % throughput. |
| Demand shape | [Exp 3](experiments/experiment-3/index.md) | A bell mixture sharpens the store win (≈ −4.9 %) and shrinks the fulfillment win (≈ −1.2 %). Flagged there as not a controlled A/B. |
| Warehouse geometry | [Exp 4](experiments/experiment-4/index.md) | Halving aisle length lifts throughput ≈ +11 % store / ≈ +6 % fulfillment, for **100 % of arms**. Velocity zoning is a net loss — alone it costs the store ≈ −11 %. |
| Picker scheduling | [Exp 5](experiments/experiment-5/index.md), [Exp 6](experiments/experiment-6/index.md) | LPT lifts throughput a median ≈ +13.7 % store / ≈ +5.6 % fulfillment at **±0.07 % labor**. The gain is removed idle time, not reduced work. |

## Candidate levers

- **Re-slotting** — still the largest untouched lever. The bounded per-batch capacity reloader
  (the `RSL*` variants) remains disabled by default. Does continuous re-optimisation beat
  reorder-only placement, and does it pay for the churn it creates? This was on the original list
  and is the one entry that has neither been tested nor superseded.
- **Stacking the two known-good levers** — Experiment 4 found the aisle split and Experiment 6
  found LPT, but no sweep has run them **together**. Experiment 5's finding that the scheduler and
  the assignment function stack is suggestive, not conclusive, for layout × scheduler: both act on
  throughput, so they may compete for the same slack rather than compose.
- **A smarter scheduler** — Experiment 5 leaves this explicitly open. Does work-stealing extend
  LPT's gain, or does task granularity impose a floor? LPT already recovers most of the idle time
  at 187 near-equal fulfillment tasks, so the headroom is probably in the store channel.
- **A labor lever for fulfillment** — the other open question from Experiment 5. Fulfillment task
  labor is ~87 % cart-swap, so no placement rule moves it much. Fewer, larger carts would, but that
  changes the operation rather than the layout. Is there anything in between?
- **Batch structure** — stronger affinity-correlated batches, longer or variable lead times, or
  demand that drifts over the run. Partially probed by Experiment 1's lead-time variants, but
  never with correlated co-picking, which is where the affinity-based families should shine and
  so far have not.
- **A controlled demand A/B** — Experiment 3 compares bell against uniform across runs that also
  differ in SKU count and fulfillment share, and says so. A single sweep varying only the demand
  shape would settle what that comparison can currently only suggest.
