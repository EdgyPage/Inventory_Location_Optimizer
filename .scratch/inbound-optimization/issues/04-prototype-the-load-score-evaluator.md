# Prototype the load-score evaluator

Type: prototype
Status: open
Blocked by: 01, 03, 10

## Question

How is one trailer's load scored against a frozen space view? Build a cheap concrete
prototype to react to, comparing the two candidate shapes: greedy-sequential assignment that
CONSUMES bins as the load's items are placed (contention-aware, expensive) versus independent
per-item best-bin scoring (cheap, ignores contention) — both using the run's own assignment
function, evaluated over the SpaceView's two tiers — current empties plus UNTIMED predicted
clears (the space-timeline resolution, 03, dropped the unload-window horizon).
Measure the computational cost of each at realistic scale (trailers standing × items per load
× candidate bins) — this number is the caching stakes and feeds the cache-boundary ticket.
Recommend the evaluator contract: inputs (frozen view, load, horizon), output (one comparable
score), and cost.

Link the prototype under `assets/`. Consult `codebase-design` and the reuse list (CLAUDE.md
§2: `regime_of`, `BinKey`, the cost model) before inventing any scorer.

## Comments

2026-08-29, from resolving "Design the space timeline" (03): predictions are UNTIMED — the
unload-window horizon is gone (question edited above). And the objective the score serves is
reopened in "Define the inbound objective" (10, now blocking): placement quality may be
vacuous for trailer ordering, so the evaluator contract must be argued against whatever 10
adopts (on-shelf availability / unload-plan candidates).
