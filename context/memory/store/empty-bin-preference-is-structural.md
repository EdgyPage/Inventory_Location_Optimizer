---
name: empty-bin-preference-is-structural
description: "put-away prefers an empty bin and falls back to the SKU's OWN bin when none fits (ADR-0003, 2026-09-08); the old never-adds-to-an-occupied-bin invariant held only while reorder lots were pallet-sized"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-09-08T00:00:00.000Z
---

**AMENDED 2026-09-08 by ADR-0003.** The rule is now *empty bin first, the SKU's own bin only
when no empty bin fits* — the no-meeting property below survives as the FIRST RUNG of a
fallback chain, not as an invariant.

Asked 2026-08-25 to make putters "emphasise utilizing empty bins rather than adding to
existing bins to prevent conflicts with pickers." Nothing needed building **at the time**:
`_execute_placement` removes the chosen bin from the free index, so an occupied bin was not a
low-scoring candidate but **not a candidate**. Measured: 1,416 placements × 4 arms, zero into
an occupied bin.

**Why that stopped being enough.** It held only while reorder lots were pallet-sized. Under
base stock every pick reorders what it took, so a line taking part of a shelf leaves a remnant
and the top-up arrives as its own unit needing its own bin. Against a warehouse sized at one
bin per SKU the free index ran dry, units sat in `pending` with no expiry, and the store
section's missed share climbed for forty days. The fallback chain in
`Inventory_Manager._stock_per_unit` is now: empty bin → the SKU's own bins (fullest first) →
repack rescue → singleton rescue → `pending`.

**The consequences worth knowing.**

- `Inventory_Manager._execute_topup` is a FIFTH bin-mutation site (`storage.quantity += n`),
  allowlisted in `Tests/architecture/test_bin_mutation_sites.py` and recorded by
  `BinRecorder` as a `bin_placement` row with `bin_state='occupied'`. **FOUR folds** over that
  log have to ADD on those rows rather than replace — `Visualization/precompute._bin_spans`,
  `readers/base._state_from_log`, `Tests/bench/bin_log_harness.fold`, and
  `Diagnostics/replay_run.occupied_from_bin_log`. Each under-counted silently until taught,
  and the fourth was missed on the first pass precisely because the first three had been
  found; if you add a fifth reader of `bin_placement`, this is the question to ask it.
- **A quantity that goes UP must re-`_fit` the unit.** `StorageUnit` caches
  `_height`/`_width`/`_length`/`_stack_axis` and `Pallet.storage_size` at construction and
  nothing recomputes them. Picks only mutate quantity downward, where a stale-large cache is
  conservative — the top-up is the first upward mutation, and there the cache lies in the
  UNSAFE direction (a 42x12x46 order caches `'small'` at qty 1 and is really `'extra_large'`
  at qty 4). `requeue_bin` re-admits the same object and `_candidates_raw` reads
  `unit.storage_size`, so without the re-fit an evicted top-up gets offered small bins and
  `_execute_placement` puts it in one with no fit check.
- The rescues are now RECEIVING work, priced per resulting pack at the dock's unload price
  and written as `event_type='repack'` work events. The staffing record stamps `f_repack = 0`
  (provenance `assumed`) and the equilibrium check's fifth clause FAILS on any measured
  repack — a repack is a finding about warehouse sizing, not a cost to absorb into a band.

**The trap if you go looking** (unchanged, and now sharper): an experiment that "turns on
empty-bin preference" and shows no change is not evidence the feature is inert — it is
evidence the free index never ran dry. Read `batch_stats.free_bins` and `put_topups` before
attributing a null result to the scoring.

See [[putaway-seams-for-inbound]], [[placement-pools-and-the-audit-point]],
[[warehouse-size-comes-from-the-levels]], [[drain-order-is-smallest-first]].
