# Prototype the load-score evaluator

Type: prototype
Status: open
Blocked by: 01, 03, 10

## Question

Prototype the GAIN evaluator the unload plan orders by (the objective resolution, 10):

    gain(t) = E[put + pick work if t's load places from the pool NOW]
            − E[same if deferred to the NEXT drain's pool (leftovers + predicted clears)]

FAITHFUL-TO-ARM: both expectations estimate the bins THIS arm's pool would grant (a virtual
copy of the arm's own placement machinery over the SpaceView's two tiers), never an
idealized best-bin cost — pools disagree with the cost model (the travel-D pools ignore the
height term entirely), and a plan optimized against a rule the arm doesn't use optimizes a
fiction. The pick side carries E[visits] ≈ quantity / per-visit draw; the put side is
travel-dominated and paid once; cost-model weights, no new knobs.

Prototype the fidelity ladder: full greedy-sequential virtual pool (contention-aware,
consumes bins as the load places — the plan's virtual-consumption step needs this shape)
versus independent per-item best-bin scoring (cheap, ignores contention) — and find where
between them the resulting ORDERING stops changing, which is the only fidelity that
matters. Measure computational cost at realistic scale (standing trailers × items per load
× candidate bins) — this number is the caching stakes and feeds the cache-boundary ticket
(06). Recommend the evaluator contract: inputs (frozen view, load, the arm's placement),
output (gain — one comparable number per candidate), and cost.

Link the prototype under `assets/`. Consult `codebase-design` and the reuse list (CLAUDE.md
§2: `regime_of`, `BinKey`, the cost model) before inventing any scorer.

## Comments

2026-08-29, from resolving "Design the space timeline" (03): predictions are UNTIMED — the
unload-window horizon is gone (question edited above). And the objective the score serves is
reopened in "Define the inbound objective" (10, now blocking): placement quality may be
vacuous for trailer ordering, so the evaluator contract must be argued against whatever 10
adopts (on-shelf availability / unload-plan candidates).

2026-08-29, from resolving "Define the inbound objective" (10): question rewritten above —
the objective is expected future work (put + pick), the output an unload plan with
set-composition semantics, the evaluator faithful-to-arm; the original "two candidate
shapes" survive as the fidelity ladder. Load-bearing facts for the prototype, verified
during 10: no bin is chosen during the receive drain (units enqueue in canonical merged
order, bins are allocated in one `_drain_putaway → _stock` pass, ranked waves served by the
pool's own `sort_key` precedence), so gain is about pool MEMBERSHIP, not seats; reclaim
runs at drain step 0, so `predicted` is exactly the next drain's pool gain. All three
blockers are now resolved — this ticket is on the frontier.
