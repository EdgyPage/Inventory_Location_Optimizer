# Declare the coverage against the inbound lead

Type: grilling
Status: open

Graduated 2026-09-10 from the inbound-optimization map's
[Verify the derived receiving crew under arrivals](../../inbound-optimization/issues/23-verify-the-derived-receiving-crew.md).
HITL: `grilling` + `domain-modeling`.

## Question

The era's coverage record sets `reorder_point = d_s * (lead_days + safety_days)` and stamps
`pipeline_qty = d_s * lead_days` (`Optimization/simconfig/coverage.py`), with `lead_days` read
from the catalogue's `lead_time_mean` -- **0.0 on every carton of the reference pair**. That is
right flag-off, where a reorder lands in the batch that placed it (in-transit 0 on every batch
of the reference run). Under the trailer pipeline the realized order-to-shelf lead is
**1.78 days** on fulfillment and 1.75 on store (Little's law over days 20-39: 48,598 units in
transit against 27,294 ordered/day), and the fulfillment leaf's supply level reads 0.148 against
a stamped 0.025, trending up -- the first era run with the yard on failed the supply clause on
every arm while every crew stayed in band. The store passes only because its implied coverage
is ~1,785 days per SKU.

How does the record declare the lead?

- **Derive it**: the trailer lead is a declared law (lognormal, median `INBOUND_LEAD_MINUTES`,
  sigma `INBOUND_LEAD_SPREAD`) plus a drain quantization (one drain per site day) plus a loading
  wait at the ordering site (trailer type, next-fit) -- a closed form over declared inputs, in
  the era's spirit (memory `no-calibration-simulations`), or at least a stamped expectation per
  channel. Which of the three terms the form must carry, and whether `safety_days` still means
  what it did once the lead has its own term.
- **Declare it**: a per-run `lead_days` input on the staffing record (era-only, five seams),
  provenance `assumed`, and the run's own Little lead reported against it by the audit.
- Either way the supply clause's expected level (`1 - fill_rate` under base stock with no
  pipeline, `coverage.py` ~217) must say what it expects WITH a pipeline, or the clause judges
  a lead-aware record against a lead-free expectation.

Out of this ticket: the pipeline's shape (loading, dispatch, lead law) -- the inbound map owns
it; the contention regime (inbound 25). The re-check is inbound 26.
