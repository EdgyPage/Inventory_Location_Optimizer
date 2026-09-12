# Build the site space view

Type: task
Status: resolved

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Design the site space view](08-design-the-site-space-view.md) settled the rule and
[Build the coupled work unit](18-build-the-coupled-work-unit.md) made a second leaf exist;
[Build the site put-away pool](19-build-the-site-putaway-pool.md) has since shown what a
per-leaf artefact costs when two leaves share one resource, and this is the same defect one
layer up.

**Why it is its own ticket rather than part of the coordinator's.** `_receive_standing` freezes
`ctx.space` from a `SpaceTimeline` documented as *"one per arm"* and attached to ONE manager, so
under one yard a mixed trailer is ranked on half the site's free space. `freeze(mgr, epoch)`
takes one manager and is pinned PURE by `Tests/unit/test_space_timeline.py`, so this is a real
design cost and not a signature tweak — 01 said so when it graduated 08. The coordinator build
([Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md)) is
blocked on it, because a coordinator that drains one dock while reading one leaf's free space is
a coupled run answering the uncoupled question.

## Question

Build 08's answer: the site-view composer itself, the `empties` regime filter with its
`regime_of(bin)` assertion, the element-wise versions, the per-regime `released_at`, the window
refusal, and the composer's unit test.

## What proves it

- **Flag-off and uncoupled byte-identical, MEASURED** — the preflight canaries plus a row-level
  diff against a `git archive HEAD` copy, the way 19 proved its own (put rows counted BEFORE the
  diff is trusted: an empty table diffs clean).
- The regime filter is proven to FAIL on a bin of the other regime, not merely to be present.
- Nine verifiers; `Tests/architecture` baselined by diffing the failure LIST against a
  `git archive` copy, never the totals (memory `arch-tier-is-red-on-head`).

## Answer

**BUILT** — `Inbound/site_space.py` (`compose_site_view`), wired live at the coordinator's one
leaf, with `Tests/unit/test_site_space_view.py`. `SpaceTimeline` is untouched: `freeze` keeps its
single-manager signature and its purity pin, and all the site-ness is a pure function over
already-frozen data, exactly as 08 decided.

### Wired at ONE leaf, not left for the second

08 says the build "needs two frozen views to exist, so it waits on the coupled unit builder".
Built that way it would be a pure function with a unit test and **no production consumer** — the
shape this repo has two memories about (`gpu-broker-dormant-not-for-placement`,
`hand-run-test-tiers-rot-silently`). So the composer is reached from the live drain TODAY, with
one contribution, and a composition of one **returns its view by identity**. That is not a special
case bolted on: a composition of one partitions nothing, so there is nothing to filter — and
filtering there would silently remove a phantom that belongs to the UNCOUPLED model, on every
standing-yard run already on disk. `SiteReceiving._freeze_views` is the seam the second leaf
changes; `receive` does not change again.

This is the same precedent tickets 09, 11, 12, 13 and 16 set on this map — the seam goes in ahead
of what it serves — and it is why the module's guards are mutation-checkable now rather than in
two tickets' time.

### 1–5 and the four scalars, as built

1. **Composition, not a wider signature.** `freeze(mgr, epoch)` untouched; `attach` untouched;
   every hook and counter untouched. The composer is `compose_site_view([(regime, view), ...])`.
2. **`empties` is FILTERED, `predicted` and `emptied_at` are not.** A leaf's `empties` is
   snapshotted from `mgr._index`, the WHOLE geometry's free index, so every leaf lists the other
   channel's free bins as permanently, falsely available. `predicted` is projected from that
   leaf's own demand over its own SKUs and `emptied_at` is harvested from its own reclaims, so
   both are already leaf-own. The composed key set is therefore **partitioned by regime**, and the
   tuples pass through **by identity** so the downstream read-only pin (`view.empties[key] is
   empties_before`) holds.
3. **`versions` element-wise**, `((d, d'), (r, r'), (f, f'))` — slot 0 still means "demand
   changed", so a future sub-vector cache key keeps working and no fourth counter appears. A SUM
   is the illegal form and a test pins why: A +1 / B +0 and A +0 / B +1 sum identically.
   **`frozen_at`** is one site epoch and a disagreement is refused. **`released_at`** is carried
   per regime and never averaged.
4. **Two injections, one per leaf** — unchanged, nothing to build.
5. **The filter asks the BIN, never the key**, and a test pins the trap: `BinKey` is a plain
   tuple, so `regime_of` falls through every `getattr` and answers `'store'` for a fulfillment
   key — silently, and always in the same direction. The tag decides, `regime_of(bin)` checks.

### Deviations under force

- **The composer is its own module, not a function in `space.py`.** 08 section 1 keeps
  `space.py`'s defining property — *"This module imports nothing from Warehouse at all"* — and the
  filter needs `regime_of`. A new module was the only way to have both; `Inbound/site_space.py`
  costs one import edge (`inbound -> wh_kernel`, already used by `receiving.py`) and gives the
  site rule one owner, the same shape `putaway_pool.py` has for put-away.
- **`released_at`'s TYPE changes on a composed view** — a float on a leaf's, a
  `{regime: float | None}` mapping on a composed one. 08 says "per leaf, carried per regime, never
  averaged", and there is no way to have that in a scalar slot. Nothing in production reads it (08
  section 0's census), the alternative fabricates a time, and the drain test asserts the
  single-leaf path still carries a scalar — so the day the one-leaf path quietly starts composing,
  something fails.
- **One guard is unreachable and says so rather than being tested.** Two contributions colliding
  on an `empties` key cannot happen while the filter is right and no two contributions share a
  regime (both refused above it). Defence in depth, marked as such — the same honesty ticket 12
  used for `put_queue_split`.

### Findings

- **The leaves hold TWO WAREHOUSES, and `emptied_at` is keyed by `id(bin)`.** `_build_leaf` calls
  `Warehouse_Builder().from_config(...).build()` per leaf, so the two id-spaces are independent
  and CPython recycles ids freely: a union could hand one leaf's lookup the other leaf's stamp,
  for a **different bin**, with nothing on the value saying so. 08's census says nothing reads this
  field, which is exactly why it would have been got wrong quietly. Refused rather than merged.
- **`YardTransit` uses `__slots__`**, so an instance-level spy on `freeze_ctx` raises
  `AttributeError: read-only`. The non-vacuity test patches the CLASS.
- **`tuple(x)` where `x` is already a tuple returns the SAME object**, so the first attempt at a
  "the tuples are rebuilt" mutation was a no-op that reported MISSED. The identity pin is real;
  the mutation had to be `tuple(list(bins))` to test it. A mutation that does not mutate reads
  exactly like a test that does not test.
- **One claim is unobservable at one leaf by construction.** A drain that went back to calling
  `freeze` directly would move no number, because the composer IS identity there — so no
  data-level test can catch it. It is asserted on the SEAM instead (a spy on `_freeze_views`),
  because a source scan is satisfied by `if False:` and by a commented-out call alike (18's
  finding).

### What proves it

- **The standing path is BYTE-IDENTICAL, measured.** One standing-yard store arm (6 batches,
  250 SKUs, 500 doors, split allocation) run in this tree and in a `git archive HEAD` copy:
  **98,676 rows across 9 tables identical**, the only difference being `simulation_runs`'
  wall-clock timestamp. The drain froze a view on every batch (asserted directly, not inferred),
  so the diff is over a path the composer actually ran on.
- `Tests/unit/test_site_space_view.py` **20 new tests**, all green; `test_space_timeline.py` and
  `test_site_receiving.py` green unchanged — `freeze`'s purity pin still covers it verbatim.
- **13 guard mutations, 13 caught**: the filter dropped, the filter asking the key, the tuples
  rebuilt, versions summed, `released_at` averaged, a composition of one rebuilt, an untagged
  contribution accepted, two leaves claiming one regime, two freeze instants, a window zipped, an
  `emptied_at` collision overwritten, a heterogeneous key accepted, and the drain bypassing the
  composer seam.

### What this hands onward

- **[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md)**
  changes `_freeze_views` and nothing else in `receive`: return one tagged contribution per leaf,
  and the composer does the rest. The tags come from the same knowledge the `{sku: leaf}` owner
  dict is built from, and an untagged contribution is refused the moment there are two — so the
  absence at one leaf cannot survive into the coupled case.
- **The futuresight window's composition rule is written down on its own refusal.** Zip by batch
  index, then union each pair of `{sku: qty}` dicts; whoever lifts the refusal has the rule and
  does not have to re-derive it.
