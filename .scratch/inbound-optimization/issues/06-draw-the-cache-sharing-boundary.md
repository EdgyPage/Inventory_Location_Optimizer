# Draw the cache-sharing boundary

Type: grilling
Status: open
Blocked by: 03, 04

## Question

For every expensive computation this effort adds (the space timeline's predicted clears, the
per-trailer load scores, the frozen ctx views), decide where it may be shared and where it
must not: CELL-SHARED is legal only for what is batch-demand-bound (the `_batches_*.pkl`
precedent); everything downstream of pick activity is ARM-LOCAL, because reorders are
pick-rollover-bound and the trailer stream diverges per arm (the charter's precompute trap).
Decide: the version-stamp scheme (what bumps a bin-state version — picks, puts, clock);
per-cache key composition (trailer identity × state version × horizon); the
behavior-neutrality cross-check (a test that compares cached against recomputed, the
sabotage-check pattern from the semantics ratchet); and how caches propagate — or are rebuilt
— across spawn-pool workers (memory: `config-knob-has-five-seams`; workers re-derive, never
inherit by fork).

Output: a table — computation × sharing grain × invalidation key × cross-check — that the
build tickets implement verbatim.
