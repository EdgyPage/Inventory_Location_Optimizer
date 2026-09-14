---
name: symbol-table-relationship-not-verified-by-symbols
description: "a gate that resolves symbol NAMES cannot detect a wrong RELATIONSHIP between them; three instances hit in inbound-performance, all healthy-looking"
metadata:
  node_type: memory
  type: project
---

A symbol table that encodes a **relationship** (a flow anchor, a frozen-oracle pairing, a
copy-target table) cannot be verified by resolving the symbols on either side of it — both can
exist and still be wired wrong, and the check reports green.

Three instances hit in one effort (`.scratch/inbound-performance/`, closed 2026-09-14; full
record in `docs/design/INBOUND_PERF_FINDINGS.md` §5), each failing silently in the direction of
looking healthy:

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

**Why:** existence-checking and relationship-checking are different questions; a table built to
answer the second gets tested with a check built for the first, and nothing in the check's own
logic can tell you it asked the wrong question.

**How to apply:** for any symbol table that encodes a relationship, write a test that *exercises*
it and asserts it produced something — not one that resolves its entries. The three new gates from
this effort are the pattern: `test_every_flow_anchor_that_should_fire_does_fire`,
`test_carve_map_symbols_resolve`, and `test_the_carve_is_a_partition_and_is_inert_when_empty`
(all in `Tests/calltree/test_calltree_anchors.py`). Related: [[hand-run-test-tiers-rot-silently]],
[[fingerprint-chain-verified-end-to-end]] (same lesson, a different chain).
