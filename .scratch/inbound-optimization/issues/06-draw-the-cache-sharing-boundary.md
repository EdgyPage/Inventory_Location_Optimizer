# Draw the cache-sharing boundary

Type: grilling
Status: resolved
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

## Answer

Resolved 2026-08-29 by grilling — six questions, all recommended options accepted. The
posture is **legality-first** (Q1): the measured stakes (04: 19–156 ms/drain × ~100
drains/arm at `N_BATCHES=100` ⇒ seconds per arm-run) earn NO cache machinery, so the table
records the legal ceiling, the key any future cache must use, and the cross-check it must
ship — and builds almost nothing now. Build tickets implement it verbatim.

### The table

| Computation | Sharing grain (legal ceiling) | Invalidation key (any future reuse) | Cross-check | Built now |
|---|---|---|---|---|
| Frozen ctx views (`SpaceView`) | Arm-local; lifetime = one drain | None — reuse across drains contradicts the freeze | 11's purity/frozen-copy tests (passing) | Yes (11) |
| Predicted-clears projection | Arm-local (reads the released demand batch × live bin lists — downstream of picks) | Must include `demand_v` (it reads demand), and `demand_v` strictly increases per drain ⇒ a cross-drain hit is impossible: recompute IS the keyed behavior | Tier 2, if anyone ever tries | No — recompute per freeze (status quo) |
| Empties snapshot + sorted-D arrays | Arm-local; within one frozen ctx NO key exists at all (04's finding) | Cross-drain: `(reclaim_v, fill_v)` — the subset it reads; honest under evictions only after the fold (16) | Tier 1 now; Tier 2 for any cross-drain reuse | Evaluator-internal only: sort once per drain, slice under consumption (ships with 14) |
| Gain evaluations / the unload plan | **Arm-local ONLY** — the charter's precompute trap (downstream of pick state and the per-arm trailer stream); cell-sharing is illegal, not merely unbuilt | Trailer `seq` × the version triple; H/w are arm-constants, so they enter the key only if a cache outlived one arm-run — which arm-locality forbids anyway | Tier 1 now; Tier 2 future | No result cache (stakes MODEST, 04) |
| Futuresight window feed | Cell-shared **by inheritance** — the batch script (`_batches_*.pkl`) is already the shared artifact, one file per pair, re-opened per worker | None — a pure function of (script, batch index); rides `demand_v` (10: no fourth counter) | 13's lockstep/refusal obligations | Read-ahead on the worker's in-memory batch list; **no new artifact, no feed file** (13) |
| Lead draws | Shared across arms **by determinism** (`SeedSequence([SEED_WORLD, TAG, seq])`, common random numbers), not by storage | None — stateless pure function | 15's determinism tests | No cache (15) |

Boundary line the table implies, recorded so nobody re-derives it: the three-class vector
versions the FREE INDEX and the DEMAND STREAM, not bin contents — partial picks change
`bin_.storage.quantity` under no counter. Every computation above is sound because each
either reads demand (⇒ `demand_v` forces per-drain recompute) or reads only the free index
(⇒ `(reclaim_v, fill_v)`, honest after the fold). A future computation that reads bin
quantities WITHOUT reading demand has no sound key in this vector: it reopens 03's design,
it does not reuse this table.

### The version-stamp scheme (Q2: fold, not fence)

`demand_v`/`reclaim_v`/`fill_v`, equality-only, no fourth counter — settled by 03/11,
closed here. The `requeue_bin` gap closes by **folding the eviction into `reclaim_v`**,
whose meaning generalizes honestly to "+1 per bin returned to the free index" (harvest or
eviction). One hook at `requeue_bin`'s single `_index_add` site
(`Warehouse/inventory/inventory_reorder.py:143`), guarded `if self.space_timeline is not
None` like its harvest (`inventory_reorder.py:303-304`) and fill
(`Warehouse/inventory/Inventory_Management.py:939-940`) siblings; `requeue_bin` is
reachable with the yard OFF, so the guard is load-bearing. Rejected: fencing
reloader+standing arms out of cache scope — a standing trap that must be remembered in
code that doesn't exist yet, guarding a feature that only fires at scale
(`reloader-cap-floors-to-zero`), exactly where a stale-cache bug would be invisible.
Reloader+standing arms are therefore IN cache scope. Graduated as
[Fold the eviction into reclaim_v](16-fold-the-eviction-into-reclaim-v.md).

### The cross-check contract (Q4: two tiers)

- **Tier 1 — ships with the evaluator build (14), runs every CI**: one unit equivalence
  test, structured plan (sort-once / slice-under-consumption) ≡ naive
  rebuild-the-pool-per-candidate plan on seeded scenarios, PLUS a sabotage assertion in
  the same test (perturb the sorted structure, assert the comparison catches it) — the
  test provably can fail (`real-test-coverage-is-317`).
- **Tier 2 — binds any future version-keyed cache; none exists**: a paired-run
  byte-identity test — cache-on vs recompute-always, byte-identical DB — before merge.
  Cached ≡ recomputed as a RUN property, not a unit property.

### Spawn-pool propagation (Q5: files down, rebuild within)

- Cell-shared ⇒ materialized as a FILE parent-side before any worker spawns; path (+
  content fingerprint, per the batches precedent) rides `workunits._shared`; every worker
  re-opens by path. Nothing cell-shared is ever pickled through the arm pool.
- Arm-local ⇒ constructed inside the worker after spawn; never inherited, never pickled.
- Any knob that controls caching rides ALL FIVE seams (`config-knob-has-five-seams`):
  settings.py, call-time CONFIG accessor, CLI flag, run_spec record+restore,
  `workunits._shared`.
- No cache object ever crosses a process boundary.

### What this closes

The map's "cache build" fog resolves to NO BUILD: what remains is 16's one-line fold (+
its tests) and Tier 1 riding 14. The futuresight feed gets no artifact (comment posted on
13); the evaluator build carries Tier 1 (comment posted on 14). The counters stay dormant
contract until a cadence change multiplies drain frequency — at which point the legal path
is already drawn and the vector is already honest.

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

2026-08-29, from resolving "Prototype the load-score evaluator" (04) — THE MEASURED
CACHING STAKES this ticket was waiting on: one plan (= one drain) costs 19–156 ms at the
recommended fidelity across realistic-to-stressed scale (6×30×120 to 24×300×600 trailers ×
units/load × bins/class), so an arm-run's evaluator overhead is single-digit-to-low-tens
of SECONDS against a sim arm measured in minutes. The stakes are MODEST: nothing here
justifies a cross-drain result cache on its own. The 4–6× that a naive
rebuild-the-pool-per-candidate implementation wastes is recovered by an
EVALUATOR-INTERNAL structure (sort each class's candidate Ds once per drain, slice under
consumption) — inside one frozen ctx, so no invalidation key is needed at all. What
remains for this ticket's table is the per-drain FREEZE inputs (predicted projection,
sorted-D arrays over `empties`), which are version-vector-keyable per 03's contract; weigh
them against these numbers before adding any machinery.
