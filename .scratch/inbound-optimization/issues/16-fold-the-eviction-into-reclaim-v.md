# Fold the eviction into reclaim_v

Type: task
Status: open

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
