# Build the gain evaluator and the gain-plan arms

Type: task
Status: open
Blocked by: 05

## Question

Implement the evaluator contract recommended by
[Prototype the load-score evaluator](04-prototype-the-load-score-evaluator.md) `## Answer`,
as the gain-family `@ordering` entries the arm roster (05) names: the myopic arm (gain
against `ctx.space.empties` only) and the forecasting arm (deferral pool includes
`predicted`), on the generalized seam ticket 12 built.

The contract, in one paragraph (04 holds the full record): 10's greedy plan loop, one call
per drain; inputs = the frozen SpaceView, the standing candidates' load compositions, the
arm's placement bundle injected by the driver (`open_pool` + wp + freq/qty tables + frozen
aisle-state copies — the `drain_sku` injection precedent), and cost-model weights with no
new knobs; output = the permutation; pricing = put travel paid once + E[visits] × per-visit
pick cost. Fidelity rides one internal seam: extremal-D arms get the k-cheapest merge
structure (proven order-equal to the full pool up to co-occurrence near-ties, 5× cheaper),
selector arms get the arm's own pool over copies; rank_random prices by expectation over
aisle heads (no RNG in an ordering entry). Exhaustion resolves tiers over
`SpaceView.empties` keys the way `_candidates` does — the prototype's flat fallback is NOT
the build's spill rule.

Neutrality obligations: flag-off byte-identical as always; with the arms ON but the entry
proposing arrival order, the plan must be inert (the seam's degenerate case); purity —
no manager mutation, no RNG — pinned the way ticket 12's seam tests pin the fifo path.

## Comments

2026-08-29, from resolving "Name the policy arms and their knobs" (05) — UNBLOCKED, and
the concrete scope is now fixed. This ticket builds, in both `YARD_POLICIES` and
`DOCK_POLICIES`: `lifo` (pure key, the adversarial control), `gain_myopic` (plan over
`empties` only), `gain_forecast` (empties + `predicted`), and `gain_gated`
(`gain_forecast` behind the FIFO urgency gate: the URGENT SET — trailers within
`INBOUND_URGENCY_HORIZON_DAYS` of crossing `INBOUND_FEE_THRESHOLD_DAYS` — is served FIFO
ahead of the plan; both knobs land here, days-denominated, on the `inbound_spec()`
pattern). Scores never blend hours with days — the gate is the only legal composition
(05's two-separate-scores rule). The `futuresight` entry is NOT here — it rides ticket
13 (blocked by this one). Every arm sets both registry knobs to the same name; all run
with `INBOUND_TRAILER_BOUND = None`.

2026-08-29, from resolving "Draw the cache-sharing boundary" (06): this build carries
Tier 1 of the cross-check contract — one unit equivalence test, structured plan
(sort-once / slice-under-consumption) ≡ naive rebuild-per-candidate plan on seeded
scenarios, plus a sabotage assertion in the same test (perturb the sorted structure,
assert the comparison catches it). NO cross-drain result cache: the sorted-D arrays live
and die inside one frozen ctx, no invalidation key exists there at all; gain evaluations
are arm-local ONLY (never cell-shared), and any future version-keyed cache is Tier-2
bound (paired-run byte-identity) per 06's table.
