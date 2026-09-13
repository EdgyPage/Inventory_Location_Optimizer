---
name: empty-bin-preference-is-structural
description: "put-away already never adds to an occupied bin — _execute_placement removes a bin from the free index, so \"prefer empty bins\" needs no scoring term; measured zero occupied-bin placements across four arms"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T13:13:31.352Z
---

Asked 2026-08-25 to make putters "emphasise utilizing empty bins rather than adding to
existing bins to prevent conflicts with pickers." **Nothing needed building** — the guarantee
is already structural, and adding a scoring term for it would have been a preference layered
over an invariant, which reads as a tunable and is not one.

`Inventory_Manager._execute_placement` removes the chosen bin from the free index at
placement. The candidate set every assignment function sees is that index, so an occupied bin
is not a low-scoring candidate — it is **not a candidate**. Measured: 1,416 placements × 4
arms, **zero** into an occupied bin.

**What this rules out, and what it does not.** It rules out a putter and a picker meeting in
one bin *during put-away*. It does not model dock or aisle contention, and it does not mean
bins are never shared over a run's life: `requeue_bin` (the reloader) and the repack/singleton
rescues return bins to the index, so a bin empties and is re-filled later.

**The trap if you go looking:** an experiment that "turns on empty-bin preference" and shows
no change is not evidence the feature is inert — it is evidence the baseline already had it.
Check the free index before attributing a null result to the scoring.

See [[putaway-seams-for-inbound]], [[placement-pools-and-the-audit-point]].
