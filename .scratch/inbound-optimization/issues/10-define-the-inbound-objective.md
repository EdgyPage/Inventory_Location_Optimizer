# Define the inbound objective

Type: grilling
Status: resolved

## Question

The charter's score — a trailer's load placement-quality against available space — may be
VACUOUS for trailer ordering: prime freight wants prime bins, but every SKU benefits from
easier pick locations, so every ordering chases the same spots (raised resolving "Design the
space timeline", 03). Decide what the yard/dock decisions actually optimize. Fixed already:
the staging decision anchors to immediately available bins (operationally stable — 03's
record). Open: whether the objective becomes ON-SHELF AVAILABILITY — minimize missed orders
(the `unmet` / `_shortfall` counters the sim already produces; per-SKU demand rides
`Order.demand`) while the best available bins go to the highest-demand items — which also
gives the oracle view something to do; whether "just-in-time unloading" is expressible at all
under the charter's no-deferral rule (a freed door is always filled — the only lever is WHICH
trailer, never WHETHER, so timing value must live inside the ordering); and whether the
decision output stays a per-drain ranking (the pure-key registry contract) or becomes an
UNLOAD PLAN — an ordered list of standing trailers minimizing the total work of future picks
over the SpaceView's two tiers — recomputed at the drain quantum. The user flagged the
sequencing of that plan-based flow as the hard part; getting it concrete is this ticket's
work.

Downstream: the evaluator prototype (04) and the arm roster (05) consume the answer; the
on-shelf-availability columns would land in the yard metrics (07); the funnel's selection
metric (08) may gain the new objective beside total production hours.

Consult `grilling` + `domain-modeling`; `codebase-design` if the unload-plan shape wins.

## Answer

Resolved 2026-08-29 through three grilling rounds. A three-reader verification pass over the
drain, placement and batch-script code reshaped the middle of the question between rounds;
the user's round-one override reshaped the top. Facts first, then the decisions.

**Facts that reshaped the question** (all verified at the anchor):

1. The vacuity suspicion is a code fact. Bin ranking is SKU-agnostic — `_RankedAssignPool`
   scores a bin by travel cost D alone; only queue precedence (`freq × labor_cost +
   β·co-occurrence`) is SKU-specific. "Match the load to available bin quality" has almost
   no per-trailer signal.
2. No bin is chosen anywhere in the receive drain. Unloaded units are enqueued in canonical
   merged order (trailers by dock rank, units by local rank, refills appended in yard-pull
   order) and allocated later in one `_drain_putaway → _stock` pass — and the ranked-arm
   wave loop serves units by the POOL's own precedence (`sort_key` descending), not queue
   order. Within one drain the yard/dock ordering does not hand out seats; the arm's
   placement machinery does.
3. What the ordering does control is SET COMPOSITION. The crew clock cuts the unload
   sequence, the put clock cuts the queue, and partially-unloaded trailers hold their doors
   — so the real lever is which loads meet this drain's bin pool and which wait for the
   next. And because bin reclaim runs at drain step 0, the `predicted` tier is exactly
   "what the next drain's pool gains": the untimed prediction is the right resolution for a
   drain-quantized include-vs-defer decision (03's untimed call, vindicated by mechanics it
   didn't know about).
4. The bin-quality channel is arm-dependent: FIFO restock draws uniform over the whole
   tier's free bins (order-blind); cmax/cmin are pool-less path-dependent; the other ~14
   restock families consume quality-ordered pools. `_RankedAssignPool` prices D only and
   ignores a height term that can exceed the whole D range; only the labor-family pools
   price it.
5. Pick work is multi-visit: a placed unit drains over E[visits] ≈ quantity / per-visit
   draw, each visit paying bin-dependent travel plus height-scaled handling; put work is
   travel-dominated and paid once per placement. Both are functions of the bin — the raw
   material of a future-work score.
6. The batch script is i.i.d. draws from the static `Order.demand` rates — a future window
   carries realized-noise knowledge (which SKUs land, exact counts), nothing structural.
   `batches[i].items` is a flat `{sku: qty}` dict; a window slice is zero plumbing at the
   standing-demand injection site, but `inject_demand` REPLACES standing demand and
   `predicted` projects from exactly that field, so a window must be its own view slot.

**The decisions:**

**1. The objective is EXPECTED FUTURE WORK** — the user's call, overriding the
on-shelf-availability recommendation: the yard/dock decisions order standing trailers to
minimize the expected put + pick hours their placements will generate. Rationale as given:
availability keyed to standing demand is a lagging signal — released orders are past work;
the campaign should chase the forward labor consequence. Placement-quality-vs-space scoring
is dropped (fact 1); on-shelf availability is demoted to a reported axis (decision 7), never
a target or selection metric. Unload hours are order-invariant and drop out of the score.

**2. Score = expected put + pick work, cost-model weights, no new knobs.** Put is certain
(paid once per placement, travel-dominated); pick is E[visits] × the bin-dependent visit
cost. A pick-only score would claim put labor is free, which this sim's timed put-away
specifically does not model.

**3. Just-in-time unloading is emergent, not a mechanism** — closed clause. Under
no-deferral the only lever is WHICH trailer takes the freed door; a not-needed-soon trailer
can only be passed over, never held, and the fee proxy is the instrument that prices its
standing. No timing term, no hold capability; do not reopen "hold the door for tomorrow's
trailer."

**4. The output is an UNLOAD PLAN with set-composition semantics.** The registry seam
generalizes: a yard/dock entry may be an ordering function `(candidates, ctx) -> ordered
list`; pure keys stay the degenerate case (key-sort wraps them — 'fifo' unchanged,
byte-identity by construction). The consumption contract is untouched: computed once per
drain at ctx-freeze, consumed front-first by same-drain refills, no mid-drain re-score. The
order's MEANING is a priority over membership in this drain's served set (facts 2–3): the
clocks turn it into a cut, and within the served set the arm's own pool assigns seats. This
amends the charter's "pure-key registries suffice." The concrete greedy:

    plan(candidates, ctx):
      virtual = two-tier pool state from ctx.space   # empties now; predicted = next drain's gain
      while candidates:
        gain(t) = E[put+pick work if t's load places from the pool NOW]
                − E[same if deferred to NEXT drain's pool (leftovers + predicted)]
        take argmax gain (ties → FIFO by arrival); virtually consume what its load would take
      return the order

The seat-level sequencing flagged as the hard part is GONE — the code never gave the
ordering that power. Corollary, accepted explicitly: on order-blind restock arms (fifo,
cmax/cmin) the bin-quality channel is zero and inbound ordering moves only timing and fee —
phase-2 gradients will concentrate on the pool arms, and that is a finding, not a failure.

**5. The evaluator is FAITHFUL-TO-ARM.** `gain` estimates the bins a load would receive
from THIS arm's pool (a virtual copy of the arm's own machinery — phase 2 runs top-k arms
only, a handful of families), never an idealized best-bin cost: a reference-cost evaluator
would systematically disagree with e.g. `tmin`, which ignores the height term (fact 4).
Same argument that made the space timeline's forecast "the sim's own rule by construction"
(03). How coarse the virtual pool can be is exactly the prototype question — 04's body is
rewritten around it.

**6. The FUTURESIGHT WINDOW family** (the user's proposal, made precise): an arm with
window w reads `batches[i+1..i+w]` beside everything lawful; w=∞ IS the oracle — the
"oracle upper-bound arm" fog item is absorbed as this family's endpoint. The family is a
declared-unlawful upper-bound REFERENCE (a real WMS cannot see undispatched orders) and
never lands in the recommendable set; honesty flag from fact 6: its edge over the static
rates is sampling-noise knowledge, so expect modest separation and say so up front.
Plumbing, all four decided: its own SpaceView slot and feed, never merged into standing
demand (protects "Predicted clear" and the one-batch-deep pin); no fourth version counter —
the window changes in the same event that bumps `demand_v` and is a pure function of the
batch index, so it shares `demand_v` (03's three-counter contract survives; 06 unaffected);
the window clamps at the script's end; futuresight arms REQUIRE the precomputed script — on
a fingerprint miss they refuse loudly (refusal-until-clean, the effort's own precedent)
rather than sampling a window inline.

**7. Reporting: missed share stays, as an axis.** Missed pieces = the `unpicked_unstocked`
+ `unpicked_unavailable` FLOWs (never `unpicked_daycut`, a labor artifact), reported as a
SHARE of `items_demanded` — a count with no denominator cannot distinguish "policy worse"
from "more demand." Lands in the yard metrics (07), beside hours + fee in the funnel table
(08), never a selection metric.

**8. Regime acceptance criteria** for the lead-distribution ticket (02), re-derived for
this objective: under FIFO ordering the chosen distribution must produce (a) yard
contention — standing trailers regularly exceed free doors at drain start — and (b) binding
cuts — drains regularly ending with unserved standing trailers or partially-unloaded ones.
Both measurable from `YardTransit.stamps` and staged-remainder counts (07's surfaces).
Rollover stays off. A pilot FIFO run showing neither is a config to reject before sweeping.

**Ripples:** 04's question rewritten around the gain evaluator (all its blockers now
resolved — frontier); 05's body amended (ordering functions; futuresight in the roster;
comment); comments on 02 (acceptance criteria), 06 (window shares `demand_v`; sharing-grain
hints), 07 (missed-share + regime columns), 08 (selection metric confirmed); charter
bullets amended (objective, ordering seam, forecast source); glossary gains Future work,
Unload plan, Futuresight window; graduated task tickets "Generalize the ordering seam" (12,
blocked by 11 — same files as the in-flight space-timeline build) and "Build the
futuresight window feed" (13, blocked by 05 and 11). A stale-docstring find from the
verification pass (the FIFO put-away mechanism misdescribed at its definition and in one
memory note) was spun off as a separate chip — out of this map's scope.
