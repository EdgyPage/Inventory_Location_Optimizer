# Future experiment discussion

A scratchpad for levers worth testing in future sweeps — beyond what
[Experiment 1](experiments/experiment-1/index.md) covered. Jot ideas here; promising ones
become their own experiment folder.

!!! note "Notes"
    <!-- paste thoughts here -->

## Candidate levers (seed list)

- **Re-slotting** — enable the bounded per-batch capacity reloader (the `RSL*` variants,
  currently disabled) and measure whether continuous re-optimisation beats reorder-only placement.
- **Alternate pick-time calibrations** — cost models beyond the four ergonomic penalties, e.g.
  different weight/volume exponents or cart-swap costs, to probe how robust the ranking is.
- **Warehouse scale** — larger SKU counts / aisle counts to see whether the winners hold as the
  layout grows.
- **Batch structure** — stronger affinity-correlated batches (co-picked partners), longer or
  variable lead times, or demand that drifts over the run.
- **Objective trade-offs** — cases where a strategy that wins on travel loses on cumulative task
  time, to sharpen the "an optimization can make things worse" finding.
