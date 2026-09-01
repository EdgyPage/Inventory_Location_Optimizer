# Give the fifo rider a faithful gain bundle

Type: task
Status: open

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
