# Give the fifo rider a faithful gain bundle

Type: task
Status: resolved

## Question

**Phase 2 cannot run until this is answered.** Every gain cell refuses the `fifo` arms at
worker startup, so all five of them die before the first batch.

The chain, verified rather than inferred (2026-08-31, while resolving
[Build the run-shape layer](18-build-the-run-shape-layer.md)):

- `strategy_runner` builds `_gain_bundle_for(strat, …)` for **every arm in the set** whenever a
  gain policy is named — the gate is on the POLICY, not the arm
  (`strategy_runner.py`, the `_GAIN_POLICIES` intersection).
- 08 makes `fifo` a **mandatory** rider in the phase-2 arm set: it is
  `run_channel_rollup`'s baseline (which now RAISES without it) and the order-blind negative
  control.
- `fifo` is not in `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, so `_gain_bundle_for` raises for both
  `uni_fifo_norsl` and `opt_fifo_norsl`.

Pinned by `Tests/unit/test_restock_selection.py::test_a_gain_bundle_really_is_refused_for_the_rider`,
and reported by the selector in its own field, `rider_needs_bundle_extension`.

### The decision this ticket has to make first

**What is a FAITHFUL evaluator for uniform-random placement?** `fifo` is `_build_uniform` — a
uniform-random enqueue, not a ranked pool — so neither existing adapter fits directly:

- the **merge** adapter (tmin/tmax) needs an extremal-D direction, and uniform has none;
- the **pool** adapter needs the arm's own builder to rebuild over aisle-state copies, and
  there is no pool to rebuild.

`rank_random` is the nearest precedent and may carry the whole answer: it consumes no RNG,
using a deterministic stand-in selector under **expectation pricing** over the aisle heads (the
decided deviation ticket 04 recorded — an ordering entry may consume no RNG). Whether an
expectation over uniformly-chosen bins is faithful to what `fifo` does, or is a fiction priced
under its name, is the question. If it is a fiction, say so and take the alternative below.

### Constraints inherited

- **Faithful-to-arm is the whole contract** (10, 14). An evaluator that cannot price what the
  arm would actually do must refuse rather than approximate.
- **It sits OUTSIDE 08's cap of three.** The cap governs which OPTIONAL families are worth
  extending; this one has no opt-out, so counting it against the cap would silently buy one
  fewer real family.
- **06's Tier-1 cross-checks apply** (equivalence + sabotage), as they did for the four
  families that landed with 14.
- **Two edits, both required**: the branch in `_gain_bundle_for` AND the name in
  `FAITHFUL_GAIN_FAMILIES`. A branch without the name leaves the selector backfilling past a
  family it can run; a name without the branch puts a rule into phase 2's arm set that dies at
  its first drain. `test_the_faithful_set_is_the_one_the_driver_actually_accepts` pins the pair.

### The rejected alternative, named so it is not re-proposed

**Exempting `fifo` from the bundle** — gating on policy AND arm instead of policy alone. It
would leave the baseline arm running v1 ordering inside a gain cell, so every cell's own
baseline would be measured under a *different inbound policy* than the rows it baselines. That
is worse than refusing: it produces numbers, and they are not comparisons.

If the faithfulness question resolves against an evaluator, the honest fallback is not an
exemption — it is a change to 08's arm-set rule, and that reopens the funnel design.

### Why this is not blocked on phase 1

`fifo` is in **every** possible arm set, so the scope is known now. Doing it before phase 1
also de-risks the 480-unit phase-2 sweep rather than discovering the refusal at its start.

The rest of [Extend the gain bundles](20-extend-the-gain-bundles.md) stays genuinely gated:
which OTHER families need extending is phase 1's output.

## Answer

**BUILT. Phase 2's gain cells accept the rider.** The faithfulness question resolves FOR an
evaluator, and more strongly than the ticket hoped: the expectation over a uniform draw is not
an approximation that happens to be defensible, it is **exact**. `fifo` is now the first entry
in `FAITHFUL_GAIN_FAMILIES`, and `_gain_bundle_for` builds it a bundle on a THIRD adapter.

### The decision: exact, not a fiction — and `rank_random` does not carry it

`rank_random` was the nearest precedent and it is the wrong one. Its adapter is the POOL
adapter, whose whole faithfulness argument is *call the arm's own builder over copies*; `fifo`
is `_build_uniform`, which sets `place_one` only and has no pool to call. Any pool written for
it would be a fiction with nobody's name on it.

It needs none. `_uniform_assignment` draws uniformly from the whole tier `_candidates_raw`
returns, and two facts make that pricable in closed form:

- **Sequential draws without replacement leave the marginal alone.** Every unit's bin is
  uniform over the tier AS FROZEN, whatever the units before it drew — so a load's expected
  cost is the sum of per-unit tier means, with no correction term.
- **`_pair_cost` is AFFINE in a bin's (x, y, height multiplier).** So the mean of the cost over
  a bin set equals the cost read at the set's means. Three moments per tier, cached; O(1) per
  unit after that.

`_cost_at` is now that one formula with the location passed in (`hm=None` reads the bracket
step at a real bin, a value is a tier's mean), so the exactness is structural rather than a
claim in a docstring. It is checked against brute force: every injective assignment of three
units to four bins, averaged, equals the adapter's number. Note the third moment is the mean OF
the steps, never the step at the mean height — a different number, and the pin fails when the
sabotage swaps them.

This makes `fifo`'s evaluator **stronger** than `rank_random`'s, which prices each unit at the
mean over the pool's live aisle heads and is an approximation at every step.

### What contention means for an arm with no preference

Exactness in the price is exactly what empties the identity of a take: if the arm does not
prefer a bin, losing a near one costs what losing a far one costs. So consumption is a **SEAT
COUNT**, not a set, and capacity is the only lever an inbound ordering has on this arm. The
adapter spills up the chain when a tier runs dry and pays the existing exhaustion penalty past
it; exclusions reduce the COUNT and never the moments (what another load consumed is a
uniformly random subset, so what is left has the same mean — filtering it out of the moments
would bias the price by precisely the thing the arm does not choose on).

That has one consequence in the machinery, and it is the only place this build touched shared
code. `plan_order`'s leftover model unions the OTHER candidates' now-takes. Independent uniform
draws essentially never collide, so handing every candidate the same front-of-list bins would
collapse that union to a single load's worth and price a whole yard's contention as one
trailer's. The sweep therefore carries a **shared block allocator** (`alloc`, one per greedy
round, inert for the other two adapters), so candidates get their own bins.

**One recorded deviation from the obvious implementation:** the allocator WRAPS past the end
rather than truncating. Truncating was tried and is wrong in the exact regime the arm exists
for — with two bins and two two-unit loads, the candidate swept first takes both and its
leftover set comes back empty, so it shows a gain of zero and the LATER arrival wins on sweep
position rather than on what it loses. Wrapping marks an oversubscribed tier's bins shared
(`n > 1`) for everyone, and the hot load then wins from either position. Pinned both ways round
by `test_an_oversubscribed_tier_is_shared_rather_than_handed_to_whoever_swept_first`, which
fails under truncation (verified by sabotage, not asserted).

**Residue, named:** the union is exact while a tier's demand fits in it, and exact again once
every bin is claimed twice; between those it under-excludes, because the leftover model hands a
candidate back the bins no OTHER candidate's block happened to name. Uniform contention wants a
count and the seam speaks in identities. This is not a new distortion — the merge adapter
under-states displacement depth in the same regime — and it is bounded by the exhaustion
penalty at the far end.

### The finding this forced into the open

With seats for everyone, **`gain_myopic` over `fifo` has gains of exactly zero and the plan is
arrival order.** The same scene under a ranked bundle reorders, so this is a property of the
ARM, not an inert evaluator: uniform placement has no preference over bins, so a myopic policy
has nothing to exploit but capacity. Two things follow, and both are good:

- It makes the rider a **clean order-blind control** — it runs the cell's policy faithfully and
  the policy provably does nothing, which is a result. This is what separates it from the
  rejected exemption, which would have run v1 ordering by construction and produced numbers
  that were not comparisons.
- The other three gain policies DO move it: the deferral side draws from a different pool
  (`gain_forecast`'s predicted tier changes the blend), the gate reorders by urgency, and
  `futuresight` reprices through the window. Only the myopic cell sees the coincidence.

Pinned as `test_an_uncontended_uniform_tier_gives_the_myopic_arm_nothing`, with the reversed
input reversing the plan so it cannot pass on a fixed answer.

### Obligations discharged

- **Both required edits**, as the ticket insisted: the branch in `_gain_bundle_for` AND the
  name in `FAITHFUL_GAIN_FAMILIES`. `test_the_faithful_set_is_the_one_the_driver_actually_
  accepts` pins the pair; the refusal message names the uniform expectation beside the other
  two adapters.
- **06's Tier-1 cross-checks.** Equivalence: `test_structured_plan_equals_naive_rebuild_per_
  candidate` is now parameterized over the merge AND uniform bundles, with the naive reference
  sharing the round's allocator (it is part of the plan's definition, not of the structure).
  Sabotage: perturbing a cached predicted-tier moment must change the plan, and does.
- **End to end, not at the seam.** `test_the_rider_plans_a_real_drain_through_the_driver_
  bundle` runs a real `Inventory_Manager` drain under `gain_myopic` with the bundle the DRIVER
  builds from the real `uni_fifo_norsl` strategy, on `test_standing_yard`'s harness. Removing
  the driver branch reproduces the original blocker verbatim. A counter guards it: the plan
  must have ordered more than one standing trailer (it orders 11), or the test would pass on a
  yard with no decision in it.
- **The two written-to-fail selector tests fired and were inverted** —
  `test_the_mandatory_rider_needs_no_extension` and
  `test_a_gain_bundle_really_is_built_for_the_rider`, the latter now asserting BOTH fifo arms
  build while `rank_labor` still refuses, so the branch is about fifo and not a gate that
  stopped gating. `rider_needs_bundle_extension` stays in the artifact, reporting empty: a
  future `BASELINE_RULE` change must surface there and not 480 work units into phase 2.
- Unit tier 1547 green.

### Cost, to the funnel

The uniform adapter is the CHEAPEST in the suite — O(|tier|) once per entry call for the
moments, then O(1) per unit, against the merge adapter's per-tier sort and the pool adapter's
rebuild-over-copies. So the mandatory rider adds the least evaluator overhead of any arm in a
phase-2 gain cell, which is the opposite of what a mandatory extra usually costs. (Complexity,
not a measurement; 04's per-drain milliseconds are the merge adapter's and are unaffected.)

### Owed

- **The derived architecture layer**, as at 09 / 13 / 15: `verify_architecture.py` reports
  graph.json stale plus the two renamed selector tests. It belongs to the
  `architecture-maintainer`, which regenerates the whole chain.
- The run-tree source fingerprint was refreshed through `preflight --yes` (canaries A and B
  both ran): **no schema event** — `5c9bc35db55b` still valid, tree shape unchanged.
