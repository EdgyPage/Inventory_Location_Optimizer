# Prototype the load-score evaluator

Type: prototype
Status: open
Blocked by: 01, 03

## Question

How is one trailer's load scored against a frozen space view? Build a cheap concrete
prototype to react to, comparing the two candidate shapes: greedy-sequential assignment that
CONSUMES bins as the load's items are placed (contention-aware, expensive) versus independent
per-item best-bin scoring (cheap, ignores contention) — both using the run's own assignment
function, evaluated over current empties plus predicted clears within the candidate trailer's
unload window (the horizon needs the unload-span estimate from the standing-dock mechanics).
Measure the computational cost of each at realistic scale (trailers standing × items per load
× candidate bins) — this number is the caching stakes and feeds the cache-boundary ticket.
Recommend the evaluator contract: inputs (frozen view, load, horizon), output (one comparable
score), and cost.

Link the prototype under `assets/`. Consult `codebase-design` and the reuse list (CLAUDE.md
§2: `regime_of`, `BinKey`, the cost model) before inventing any scorer.
