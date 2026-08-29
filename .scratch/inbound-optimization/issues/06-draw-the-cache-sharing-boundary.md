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

## Comments

2026-08-29 (from resolving "Build the space timeline", 11): the version vector is now live —
`SpaceTimeline.demand_v/reclaim_v/fill_v` in `Inbound/space.py`, per-event-class, equality
the only legal operation, and this ticket may not add counters of its own (03's contract).
One gap to account for when composing keys: `requeue_bin` evictions (reloader arms) return a
bin to the free index through NONE of the three event classes, so two version-equal freezes
can straddle an eviction-only change; the frozen views themselves are always correct (they
snapshot live state — staleness is confined to version-keyed reuse), and the evicted unit's
eventual re-placement does bump `fill_v`. Stated in the module docstring too. Decide here
whether reloader+standing arms are simply declared out of cache scope or the eviction is
folded into an existing class.

2026-08-29, from resolving "Define the inbound objective" (10): the three-counter contract
survives futuresight — the window slot (ticket 13) changes in the same event that bumps
`demand_v` and is a pure function of the batch index, so it shares `demand_v`; no fourth
counter. Sharing-grain hints for this ticket's table: the window FEED is batch-script-bound
(the `_batches_*.pkl` precedent — cell-shareable); the GAIN evaluations (04's evaluator) are
downstream of pick state and the per-arm trailer stream — arm-local, per the charter's
precompute trap.
