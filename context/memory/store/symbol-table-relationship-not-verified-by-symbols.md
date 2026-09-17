---
name: symbol-table-relationship-not-verified-by-symbols
description: "a gate that resolves symbol NAMES cannot detect a wrong RELATIONSHIP between them; four instances now, all healthy-looking -- the fourth hid a dead calltree section for a month"
metadata:
  node_type: memory
  type: project
---

A symbol table that encodes a **relationship** (a flow anchor, a frozen-oracle pairing, a
copy-target table) cannot be verified by resolving the symbols on either side of it — both can
exist and still be wired wrong, and the check reports green.

Three instances hit in one effort (`.scratch/inbound-performance/`, closed 2026-09-14; full
record in `docs/design/INBOUND_PERF_FINDINGS.md` §5), each failing silently in the direction of
looking healthy, and a fourth in `.scratch/architecture-deepening/` ticket 07:

1. **`Tests/calltree`'s minlabor frozen oracle** was re-priced in production by `fc7a46a5`
   (ADR-0001) and in the sibling `Tests/unit/test_travel_balanced_equivalence.py`, but not here —
   `Tests/calltree` is in no gate (see [[hand-run-test-tiers-rot-silently]]). The two sides of the
   comparison ran different cost formulas, so the test accused a correct cache.
2. **A sabotage test**
   (`test_the_identity_copier_lets_the_virtual_placement_reach_the_warehouse`) installed its
   identity into `AISLE_COPIERS` after production moved to `AISLE_VIEWS` (`Inbound/gain.py`) — it
   sabotaged a table nothing read, and only failed loudly because a later refactor moved the seam
   under it.
3. **`yard_plans`/`dock_plans` flow anchors** whose symbols BOTH resolved but whose relationship
   was wrong: `plan_order` is a great-grandchild of `yard_order` via `bounded_order`, not a direct
   child. They read 0 for their whole life. `test_flow_anchors_resolve`
   (`Tests/calltree/test_calltree_anchors.py`) names this exact failure mode in its own docstring
   while being structurally unable to detect it, because it resolves symbols against modules and
   never looks at a call tree.


4. **`SECTION_MAP`'s eight `t_save` anchors** named the `save_<table>` wrappers, which had zero
   production callers -- the write path was `save_checkpoint_bundle`, never anchored at all. All
   eight resolved. The calltree's `t_save` read **0.000000 in every archived capture** from at
   least 2026-08-18 until ticket 07 (2026-09-17) collapsed the writers and repointed the anchor
   at `CheckpointBuffer._write`. Hidden for a month because a SECOND live instrument answers to
   the same name -- see [[two-instruments-named-t-save]]. This is the same file that already
   carried instance 3, and the gate written for instance 3 (`test_every_flow_anchor_that_should
   _fire_does_fire`) covers flow anchors only, so `SECTION_MAP` still had the old shape.

**Why:** existence-checking and relationship-checking are different questions; a table built to
answer the second gets tested with a check built for the first, and nothing in the check's own
logic can tell you it asked the wrong question.

**How to apply:** for any symbol table that encodes a relationship, write a test that *exercises*
it and asserts it produced something — not one that resolves its entries. The three new gates from
this effort are the pattern: `test_every_flow_anchor_that_should_fire_does_fire`,
`test_carve_map_symbols_resolve`, and `test_the_carve_is_a_partition_and_is_inert_when_empty`
(all in `Tests/calltree/test_calltree_anchors.py`), joined by ticket 07's
`test_a_traced_arm_actually_spends_time_in_t_save`, which WRITES through the production
path and asserts the section moved. Note instance 4: a gate of this shape covers the one
table it was written for, so a sibling table in the same file can still rot. Related: [[hand-run-test-tiers-rot-silently]],
[[fingerprint-chain-verified-end-to-end]] (same lesson, a different chain).
