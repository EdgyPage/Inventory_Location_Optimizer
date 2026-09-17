# 05 - the cold-start tie-break, and cmin's per-unit lift scan

Type: refactor
Status: needs-triage
Blocked by: 04

**Folded from `.scratch/complexity-round/issues/17-the-cold-start-tie-break-is-the-quadratic.md`
by cross-reference. That file stays where it is and remains the authority** -- its record of why a
heap is the wrong fix here belongs to that effort's argument and must not be orphaned. Read it
first; this ticket exists only so the work is scheduled with the rest of the
`Assignment_Functions.py` pass.

## Why it is here

Tickets 02, 03 and 04 all open `Warehouse/placement/Assignment_Functions.py`. Doing this one in
the same pass means one re-run of the frozen oracles instead of two, and one byte-identity
argument instead of two. Its own byte-identity argument is already paid for.

## What the original ticket establishes

- **`cluster_map`**: `tied` is the set of aisles whose lift compares exactly equal. A SKU with no
  partners placed yet gives every live aisle 0.0, so the tie-break degenerates to "the live aisle
  whose pref is closest to `target`" -- a nearest-neighbour over the union of the live aisles' pref
  lists. One merged sorted array answers it in O(log N) against O(A) today. The tied fraction was
  measured **rising 3.0% -> 14.4%** across the ladder: the cold case is the growing case, not a
  corner.
- **Two things make it work rather than a line**: the merged array must be maintained as bins are
  consumed (`by_aisle[aid].remove(chosen)` plus the `prefs_by_aisle` multiset-sync beside it), and
  the exact tie order must survive -- among equal gaps the winner is the earliest aisle in
  `by_aisle` order, which is precisely the property the equivalence test needed two attempts to be
  able to observe.
- **`cmin`**: `score_of` is called once per aisle per unit by `_pick_extremal_aisle`, and `co`
  depends on the SKU's affinity row, so nothing caches across units. `cluster_map` already solved
  this shape with a `run_cache` across a same-SKU run. Measured ~9.5 placements per SKU run, so the
  ceiling is large.
- **A VALUE-ONLY cache is not enough.** `_place`'s docstring records that the first cut cached
  across the winner's set growth and drifted by one ulp when the summation order flipped, moving
  one placement at 8k meso scale.

## The trap the original ticket exists to record

**A heap is the wrong fix here, and this was the round's third reach for one and the first time it
lost.** Within a SKU run `lifts` changes only for the winner, so a max-heap looks obvious -- read
the original for why it is not. Do not re-derive this; do not re-propose it.

## Verification

Per the original ticket, plus: the three placement equivalence files, and `run_digest.py` DB-row
neutrality. Byte-identical is the bar.

## Comments

When this resolves, append the answer to BOTH files and add the context pointer to
`complexity-round/map.md`'s Decisions-so-far as well as this effort's -- it is that effort's last
live ticket and closing it here closes it there.
