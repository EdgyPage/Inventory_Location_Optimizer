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
with each. Policies are ordering functions over (candidates, frozen ctx) — pure keys the
degenerate case (the objective resolution, 10; ticket 12 builds the seam) — and the
no-deferral charter means the plan is the entire expressive surface, so the
timeliness-vs-space tradeoff must be representable in the terms and weights chosen here.

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

2026-08-29, from resolving "Define the inbound objective" (10): the space-aware families are
now GAIN families — myopic = gain against current empties only; forecasting = gain over both
tiers (predicted clears as the deferral pool). The FUTURESIGHT WINDOW family joins the
roster as a declared-unlawful upper-bound reference: window w in batches, w=∞ absorbs the
retired oracle fog item; the batch script is i.i.d., so a window carries realized-noise
knowledge only — expect modest separation over the static rates and frame it that way. Its
knob (name, per-arm declaration) is named HERE and the feed is built by ticket 13, blocked
on this one. Entries may be ordering functions (pure keys degenerate — ticket 12 builds the
seam), so the roster can carry the plan policy directly. Corollary to carry into the
roster's claims: on order-blind restock arms (fifo, cmax/cmin) the bin-quality channel is
zero — inbound gradients concentrate on the pool arms.

2026-08-29, from resolving "Prototype the load-score evaluator" (04): the evaluator's cost
does NOT constrain this roster — a full gain plan is 19–156 ms per drain at realistic
scale (arm-run overhead in seconds), so name arms on merit, not on evaluator budget. The
contract is recommended in 04's `## Answer` and its build is graduated as ticket 14,
blocked by THIS one. Two facts the roster should respect: the fidelity seam is per arm
FAMILY (extremal-D arms get a proven-equal cheap path; selector arms need the arm's own
pool, and rank_random's virtual pool must price by expectation — no RNG in an ordering
entry), so a roster restricted to D-pool arms for phase 2 keeps the evaluator simplest;
and the gain evaluations sit on frozen per-drain state, so weight knobs (05's grids)
change gains only through the score terms — no recompute-cost asymmetry between arms.
