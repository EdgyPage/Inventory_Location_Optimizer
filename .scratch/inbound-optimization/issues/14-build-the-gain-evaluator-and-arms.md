# Build the gain evaluator and the gain-plan arms

Type: task
Status: resolved
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

## Answer

Built 2026-08-30. Everything the contract (04) and the roster comment (05) name is code,
on the seam 12 built:

- **`Inbound/gain.py`** — `GainBundle` (the driver-injected faithful-to-arm bundle),
  `_Evaluator` (virtual two-tier placement: sort-once / slice-under-consumption MERGE
  adapter for the extremal-D family; POOL-over-copies adapter for selector arms, with
  expectation pricing for rank_random), `plan_order` (10's greedy verbatim), and the
  three `@ordering` entries — `gain_myopic` (empties only), `gain_forecast` (deferral
  pool includes `predicted`), `gain_gated` (the FIFO urgency gate over the forecast
  plan: the urgent set is served FIFO by stamp, and the plan prices its space AFTER the
  urgent loads consume).  Registered in BOTH standing registries at package import;
  `lifo` seeded in `priorities.py` as a pure key.  Tier spill mirrors `_candidates_raw`
  branch-for-branch over injected tier tables; past total exhaustion a unit prices at
  worst-of-chain x 1.5 (zero when nothing seats anywhere, so it cancels).
- **The injection seam**: `ctx.gain` (a new DockContext slot) delivered by
  `YardTransit.freeze_ctx` from a driver-assigned `gain_bundle`; `_gain_bundle_for` in
  `strategy_runner.py` builds the bundle only when a gain policy is named — tmin/tmax
  -> merge (direction by arm), rank_popularity -> the arm's OWN pool builder over
  copies (zero drift surface), rank_random -> deterministic stand-in selector +
  expectation over the aisle heads (04's recorded deviation); any other placement
  family, and velocity zoning, REFUSE LOUDLY at build.
- **The knobs**: `INBOUND_FEE_THRESHOLD_DAYS` (2.0 placeholder, the one knob 07's fee
  report shares) and `INBOUND_URGENCY_HORIZON_DAYS` (0.0) ride settings -> CONFIG ->
  `inbound_spec()` -> the worker payload, with explicit None tests so a swept 0.0
  survives both seams; CLI flags stay deferred to the first sweep (family precedent).
- **THE ONE RECORDED DEVIATION — the deferral pool** (the 11 precedent).  The
  prototype's "leftovers = the current virtual pool" makes the myopic arm's gain
  IDENTICALLY ZERO (no predicted tier, so both gain terms price the same pool):
  `gain_myopic` would be `fifo` in disguise — the opt_fifo/uni_fifo class of fake arm.
  The build prices leftovers as what the OTHER remaining candidates leave standing:
  each greedy step sweeps every candidate's now-placement once, and a candidate's
  deferral pool excludes every bin some OTHER candidate's placement took (leave-one-out
  over the sweep's own takes — zero extra placements, symmetric so identical loads
  still tie -> FIFO, and it degenerates to the prototype's shape exactly when takes do
  not overlap).  The myopic arm's signal is therefore CONTENTION for today's space —
  present exactly in the regime 10's acceptance criteria demand, absent under
  abundance, which is the correct physics.
- **Neutrality + Tier-1, all passing tests** (`Tests/unit/test_gain_plan.py`, 20
  tests; full unit tier 1413 green): flag-off and standing-fifo untouched (nothing on
  those paths reads the new slots); identical loads tie to arrival order (the
  degenerate inert case, both input orders); purity through the real `yard_order` seam
  (no RNG, no manager/transit/view mutation; pool copies never advance the live aisle
  dicts, guarded by call counters against silent adapter fallback); 06's Tier-1
  contract — structured plan == naive rebuild-per-candidate on seeded scenarios, with
  the sabotage assertion proving a perturbed sort structure flips the plan; the gate's
  composition made visible (a bracket-step scenario where the urgent prefix's
  consumption flips the plan pair), stampless-never-urgent, pure FIFO at horizon >=
  threshold; exhaustion pinned both ways (seats-next-drain-only defers; seatable
  nowhere prices exactly zero); the spec->bundle knob ride incl. 0.0.
- **Flagged for the funnel (08)**: the POOL-family per-drain cost is unmeasured at
  production scale (the pool is rebuilt per group x evaluation over full-warehouse
  aisle-dict copies; the merge family is the measured-cheap path) — take a bench
  number before phase 2 sweeps a pool arm.  And if phase 1's top-k lands on an
  unserved placement family (map/cluster/labor/comp/expn/fifo...), `_gain_bundle_for`
  refuses loudly and needs a conscious extension, not a silent one.

Reviewed: code-reviewer (no critical findings; its q<=0 pricing, popularity-builder
reuse and load-hoist findings are folded in) and test-reviewer (determinism proven
over 7 runs incl. hash-seed variation; both vacuity guards folded in).  The
architecture layer regen (new module + test file) rides the architecture-maintainer,
whose output stays in the working tree for review as usual.
