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
