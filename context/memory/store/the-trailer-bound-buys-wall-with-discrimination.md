---
name: the-trailer-bound-buys-wall-with-discrimination
description: "REFUTED as a lever: bounding the yard at k=8 is 2.1x faster but keeps only 12% of gmyopic's separation from fifo, because bounding IS a step toward arrival order"
metadata:
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-21T01:52:19.885Z
---

`INBOUND_TRAILER_BOUND` was the only lever with the right order of magnitude on phase 2's
14.9 h wall: `plan_order` costs T(T+1) `place_load` calls, a bound makes it k(k+1), and at
the measured yard depth of ~17 a bound of 8 is 306 -> 72. Probed 2026-09-20 as
`_probe_trailer_bound` (fifo / gmyopic / gmyopic_k8, 12 units, 2 h 43 m).

**The cost side delivered.** Isolating the gain evaluation as (priced cell - fifo cell) on
the `fifo` rider arm: 473.7 s -> 112.6 s, **4.21x**, against the 4.25x the arithmetic
predicts. On the ranked units the whole reorder section went 7,566 -> 2,938 s (2.58x) and a
unit's wall 8,182 -> 3,599 s (2.27x) -- less than 4.21x because a ranked unit's reorder also
carries placement work the bound does not touch.

**The discrimination side refuted it.** Yard overage, trailer-days past the free threshold:

    fifo (reference)          34.63
    gmyopic  unbounded        69.92     +101.9% vs fifo
    gmyopic  bounded k=8      38.71      +11.8% vs fifo   <- 12% of the gap retained

Bounding buys the wall by making `gmyopic` behave like `fifo`, and that is mechanical, not
bad luck: restricting the plan to the k longest-waiting trailers IS a step toward arrival
order, and arrival order is what `fifo` is. **The bound cannot be used**, and the finding is
itself a phase-2 result: what distinguishes a gain policy is precisely its willingness to
deviate from arrival order.

**The bigger thing the probe surfaced.** On `ss_pick_owed` -- the ranking's PRIMARY metric --
`gmyopic` and `fifo` differ by 0.034%, and `run_unload_ranking` declares a 0.1% noise floor
below which two cells are a TIE broken by yard overage. So on this fixture the cells tie on
the metric and the ranking is decided entirely by the tie-break, where `gmyopic` is twice as
bad as `fifo`. That is one cell of ten and not phase 2's answer, but any reading of the
campaign has to start from which quantity actually separated the cells.

**What is left for the wall.** Nothing with an exponent. The seven-commit round of
2026-09-20 netted parity at campaign scale; the constants are percent-level
([[aisle-best-is-what-a-pool-open-now-costs]]); the sweep cannot be made incremental
([[the-gain-sweep-cannot-be-made-incremental]]). Workers and matrix width are the remaining
levers, and matrix width is a scope decision, not an optimisation.
