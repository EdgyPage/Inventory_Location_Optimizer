# Fold the eviction into reclaim_v

Type: task
Status: resolved

## Question

Close the version-vector eviction gap, as decided by
[Draw the cache-sharing boundary](06-draw-the-cache-sharing-boundary.md): `requeue_bin`
returns a bin to the free index through none of the three event classes, so two
version-equal freezes can straddle an eviction-only change. Fold it into `reclaim_v`,
whose meaning generalizes to "+1 per bin returned to the free index" (harvest or
eviction). No fourth counter; 03's equality-only contract stands.

Build:

- One hook adjacent to the single free-index return in `ReorderMixin.requeue_bin`
  (`Warehouse/inventory/inventory_reorder.py:143`, the `_index_add` call), guarded
  `if self.space_timeline is not None` — the structural twin of the harvest hook
  (`inventory_reorder.py:303-304`) and the fill hook
  (`Warehouse/inventory/Inventory_Management.py:939-940`). `requeue_bin` is reachable with
  the standing yard OFF (the reloader gates only on `reslot_frac > 0`), so the guard is
  load-bearing.
- Update the gap note in `Inbound/space.py`'s module docstring (the eviction caveat) and
  `reclaim_v`'s counter docstring to the generalized meaning; update the counter-semantics
  pin in `Tests/unit/test_space_timeline.py` if it enumerates bump sites.

Tests:

- Timeline ON + reloader fires: `reclaim_v` bumps by exactly the evicted-bin count —
  assert the eviction COUNT, not the end state. Force `per_aisle_cap >= 1` at test scale:
  it floors to zero otherwise and `requeue_bin` never fires
  (`context/memory/store/reloader-cap-floors-to-zero.md`).
- Yard OFF + reloader fires: the hook is silent (no timeline, no bump, no attribute
  error) and the flag-off path stays byte-identical.

## Answer

BUILT, commit `d266c07` — everything the ticket specified is code, with no deviation.

**The hook.** One `is None`-guarded call adjacent to the `_index_add` in
`ReorderMixin.requeue_bin`, the structural twin of the harvest and fill hooks.
`SpaceTimeline.evict(bin_)` bumps `reclaim_v` and does nothing else.

**What generalizing actually bought.** The ticket framed this as closing a gap; the
build is better read as making the counters *mean* something they only accidentally
meant before. They now partition by WHAT CHANGED, not by which function ran — `_index`
grows through exactly two doors and both bump the same counter. That is the property
that makes the vector a complete description of the free index rather than a log of call
sites, and it is the sentence now standing in the module docstring where the honest-gap
paragraph used to be. A fourth counter would have preserved the call-site framing and
broken 06's key-vector rule for nothing.

**`evict` writes no stamp and expires none — verified, not assumed.** An evicted bin was
occupied an instant ago, so it never ran dry, and `emptied_at` carries actual clear
stamps only (the same treatment the harvest gives a stampless notification). The
stale-stamp question is the one that needed checking, and it resolves structurally: a
grep for bin-occupancy assignment finds a SINGLE site, `Inventory_Management.py:919`,
which is the `fill` hook's own line — so the fill that put the unit in the bin already
popped any stamp. No defensive `pop` was added, because the code cannot reach a state
that needs one. (`SpaceView.emptied_at`'s docstring already promised "freed by eviction
… absent"; it now has a mechanism behind it rather than an absence.)

**The guard is load-bearing.** This is the ONE touchpoint reachable with the standing
yard off — the reloader gates on `reslot_frac` alone, so `requeue_bin` fires on
store-only arms that never construct a timeline. That is now stated in the docstring and
pinned by a test, rather than left as a fact a later reader would have to re-derive.

**Three pins, each proven to fail without the hook.** The hook was sabotaged (`if False`)
and the suite re-run: all three fail, the other ten pass — so they are non-vacuous AND
the hook is otherwise behavior-neutral.

  1. `test_the_eviction_bumps_reclaim_v_once_per_bin` — the real `Capacity_Reloader`
     drives it; `reclaim_v` equals the eviction count exactly, no other class moves.
  2. `test_an_eviction_alone_moves_the_version_vector` — the gap stated as its own
     failure: freeze, evict, freeze, and the vectors must differ. This is the test that
     would have caught the original bug.
  3. `test_the_eviction_hook_is_silent_without_a_timeline` — an attached and an
     unattached manager are indistinguishable after the same eviction (index, placements,
     put-queue stream, ledgers, quantities, churn), compared by LOCATION because
     `_mgr_fingerprint` holds Bin objects that compare by identity and is useless across
     two managers.

**Two degenerate defaults, not one.** `context/memory/store/reloader-cap-floors-to-zero.md`
warned that production `move_limit_pct` (0.005) floors the per-aisle cap to zero at test
scale. Building the fixture surfaced a SECOND, independent way to the same vacuum: the
reloader's reference size is `extra_large`, and `_warehouse()`'s pallet aisle holds only
mediums and larges, so the cap floors to zero even at `move_limit_pct=0.5`. Either
default alone would have made the whole section pass while covering no eviction. The
helper overrides both and ASSERTS `per_aisle_cap >= 1` before running.

**Verification.** `Tests/unit` 1451 passed. The guards pass (path, docref). One
PRE-EXISTING failure was found and is NOT from this work:
`Tests/architecture/test_bin_mutation_sites.py::test_the_dead_site_is_still_dead` fails
at HEAD because its caller detection is a bare substring scan and `Inbound/trailer.py`
(prior map) CITES `StorageCart.add_from_bin` in two docstrings without calling it. Every
input to that test is at HEAD and untouched here. Checked for relevance to this ticket's
completeness claim: `add_from_bin` empties a bin but never touches `_index` (the manager
learns via `_reclaim_empty_bins`), so the "exactly two doors" statement stands. Spun off
as its own task.

The derived arch layer is owed to the `architecture-maintainer`, as at 09/13/15.
