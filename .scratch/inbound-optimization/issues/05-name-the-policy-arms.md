# Name the policy arms and their knobs

Type: grilling
Status: open
Blocked by: 01, 04

## Question

Define the concrete registry entries — the arms the funnel sweeps (~6 total including the
FIFO baselines). For YARD priority (freed door ← standing trailer) and DOCK priority (crew ←
staged trailer) separately: which named policies exist (fifo baseline; space-aware myopic;
space-aware forecasting; fee/age-pressure hybrids), the shape of each score term (load score
from the evaluator, age-since-arrival, overage risk against the fee threshold), which weight
knobs each policy exposes and their `settings.py` names, and how `bounded_order` composes
with each. Policies are pure keys over (trailer, frozen ctx) — the no-deferral charter means
ranking is the entire expressive surface, so the timeliness-vs-space tradeoff must be
representable in the terms and weights chosen here.
