# Name the policy arms and their knobs

Type: grilling
Status: resolved
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

## Answer

Resolved 2026-08-29 through two grilling rounds. One structural finding reshaped the
question's own arm list; one user rule reshaped the score design. Then the roster.

**Finding — the fee-greedy pole IS FIFO.** The fee proxy is `max(0, yard_days −
threshold)` with one shared threshold and one accrual rate, so every trailer's "due date"
is `arrived + threshold`: earliest-due-first = earliest-arrival-first = the seeded
`fifo`. (EDD with a common offset degenerates to arrival order; only unload-duration
awareness could separate them, a second-order refinement, not a pole.) So there is NO
standalone fee/age-pressure family: **`fifo` is simultaneously the baseline and the
department-greedy fee pole**, and ticket 01's headline contrast sharpens to "FIFO's
fee-minimal ordering vs gain arms that SPEND fee to buy put + pick hours" — a claim the
fee metric (07) can evidence directly.

**Rule — two separate scores, never blended (the user's call, overriding the recommended
λ-dial hybrid).** Labor-hours and fee-days never combine into one scalar anywhere. A
policy that weighs both composes them as a GATE, not a mix: the fee side is only ever a
yes/no urgency test with a days-denominated knob. This extends the charter's fee bullet
("never converted to dollars, never mixed into labor") into score design.

**The roster — six arms; an arm sets BOTH `INBOUND_YARD_POLICY` and
`INBOUND_DOCK_POLICY` to the same entry name** (the yard × dock cross product is
rejected: dock priority is a worker-allocation preference under door teams (01), its
gradient secondary; a targeted cross-arm on the winner is a post-funnel run, not a
roster axis):

| entry | role | reads |
|---|---|---|
| `fifo` | baseline + the department-greedy fee pole (seeded; exists) | — |
| `lifo` | adversarial control: anchors the fee axis's bad end and null-checks "does trailer ordering move labor at all" (lifo ≈ fifo on hours ⇒ the labor channel is weak) | — |
| `gain_myopic` | unload plan over `ctx.space.empties` only | the evaluator (14) |
| `gain_forecast` | unload plan over empties + `predicted` (the deferral pool) | the evaluator (14) |
| `gain_gated` | `gain_forecast` behind the FIFO urgency gate: trailers within the horizon of crossing the fee threshold form the URGENT SET, served FIFO ahead of everyone; the rest follow the plan. The horizon spans the poles (0 ≈ pure gain, large = pure FIFO) — the fee-vs-hours frontier is this one sweep | `INBOUND_URGENCY_HORIZON_DAYS`, `INBOUND_FEE_THRESHOLD_DAYS` |
| `futuresight` | `gain_forecast` reading `w` future batches from its own view slot — the declared-unlawful upper-bound reference (10), never recommendable | `INBOUND_FUTURESIGHT_BATCHES` |

**The knobs**, all riding the call-time `inbound_spec()` pattern and the five seams,
CLI flags deferred to the first sweep (family precedent):

- `INBOUND_FEE_THRESHOLD_DAYS` (float, days) — ONE knob shared by the fee report (07)
  and the gate's urgency test; its default value is 02/07's business, not named here.
- `INBOUND_URGENCY_HORIZON_DAYS` (float, days) — the gate arm's only dial.
- `INBOUND_FUTURESIGHT_BATCHES` (int; `'all'` = the oracle w=∞, a string sentinel that
  survives a run spec honestly; default None = knob inert). The arm REQUIRES it set and
  requires the precomputed script — refusal-until-clean on both (10's rule).

**Bound**: `INBOUND_TRAILER_BOUND` stays OUTSIDE the roster — every arm runs unbounded;
the bound is a post-funnel sensitivity run on the winning arm.

**Grids**: with blending rejected, the gain arms have NO weight knobs — the only swept
scalars in phase 2 are the horizon (days) and the window (batches). The map's
"weight-knob sweep design" fog item is superseded; grid design folds into the funnel
ticket (08).

**Ripples**: build allocation — ticket 14 (now unblocked, frontier) builds the evaluator
plus `lifo`, `gain_myopic`, `gain_forecast`, `gain_gated` and the two days-knobs; ticket
13 keeps the feed and gains the `futuresight` entry itself plus its knob, and is
re-wired blocked by 14 (the entry needs the evaluator). Comments posted on 07 (threshold
knob name) and 08 (roster fixed at six; sweep axes exactly H and w). `CONTEXT.md` gains
the URGENT SET / urgency-gate concept under Priorities.
