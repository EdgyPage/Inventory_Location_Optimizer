# Name the policy arms and their knobs

Type: grilling
Status: open
Blocked by: 01, 04, 10

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

## Comments

2026-08-27, from resolving "Design the standing-dock mechanics" (01): the user wants the arm
roster to carry the headline contrast "two departments greedily optimizing is worse than a
globally cost-aware optimization" — frame the fee/age-pressure family as the department-greedy
pole (inbound minimizing its own yard cost) against the space-aware family as the globally
aware pole (spending yard time to buy put + pick hours). Also from 01: dock priority is now a
worker-ALLOCATION preference under the door-team crew (decisive when workers < staged trailers,
graded otherwise) — score shapes must stay meaningful under that use, not assume an exclusive
unload sequence.

2026-08-29, from resolving "Design the space timeline" (03): now also blocked by "Define the
inbound objective" (10) — the user suspects placement-quality scoring is VACUOUS for trailer
ordering (every SKU benefits from easier picks), so the space-aware families named here must
be re-argued against 10's objective (on-shelf availability / unload-plan candidates). Score
terms may gain per-SKU demand (`Order.demand`) as an input; predictions in the SpaceView are
UNTIMED (no unload-window term exists).
