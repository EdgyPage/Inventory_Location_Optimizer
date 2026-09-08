---
name: drain-order-is-smallest-first
description: "picks drain the SMALLEST-on-hand bin first across both tiers (ADR-0003, 2026-09-08), retiring the forward-pick drain preference; it is NOT gated on a dry free index, so absolute pick/travel/throughput numbers break comparability at that commit"
metadata: 
  node_type: memory
  type: project
---

`Workload_Builder.drain_sku` takes the **smallest-on-hand bin first, ties by `location`,
across both tiers as one ordering**. It is the consolidation half of ADR-0003: the own-bin
rung merges remnants into the fullest bin, this drains the emptiest to zero and hands it back
to the free index. Neither works alone.

**Two things this broke that the ticket did not foresee.**

1. **The forward-pick preference is retired from the drain.** Singletons were drained before
   pallets "so that forward-pick locations are always preferred over reserve locations"
   (`is_forward_pick`'s docstring). One ordering across both tiers ends that. The split still
   exists everywhere else — the two indexes, `_candidates_raw`, `binkey_of` — it just no
   longer decides pick order. Nothing tested the old contract: the one test that named it
   (`test_singletons_still_drain_before_pallets`) passed after the change because its fixture
   gave every bin the same quantity.

2. **It is NOT a no-op when the free index is healthy**, unlike the rest of ADR-0003.
   Measured before building: right after INITIAL STOCKING, with no reorder fired and the
   index nowhere near dry, 800–1,145 of ~1,200 SKU-tiers already hold 2+ bins and 224–327 of
   them are drain-order-sensitive (across coverage 1–10 days); one SKU-tier held up to 199
   bins. So travel, tasks and depletion move on essentially every run.

**The comparability break.** Absolute pick / travel / throughput numbers published before this
commit are not comparable with ones after it. The user was shown the measurement and chose to
land it anyway rather than split it into its own ticket. This is the same class of break as
[[per-item-charge-hard-break]] and the aisle-split change in
[[field-the-requirement-one-planner-contract]] — and the third in a fortnight, so check which
side of all three an archived number sits on before quoting it.

**Decision 7's stated rationale does not survive its own premise.** It reads "when a SKU sits
in two" bins and rejects nearest-first "because travel is what the arms compete on". At 20–199
bins per SKU-tier, smallest-first changes travel wholesale too — it just does so without
anyone having chosen the travel consequence. Worth revisiting if a layout or inbound effort
wants to reason about what the arms are actually competing on.

See [[empty-bin-preference-is-structural]], [[placement-pools-and-the-audit-point]].
