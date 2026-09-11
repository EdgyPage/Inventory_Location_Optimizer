---
name: arch-tier-is-red-on-head
description: "Tests/architecture has four standing failures on clean develop HEAD, so a red run there is not evidence your change broke it — baseline against a git-archive copy of HEAD before attributing one"
metadata: 
  node_type: memory
  type: project
---

`python -m pytest Tests/architecture -q` reports **4 failed, 378 passed, 1 skipped** on a clean
checkout of `develop`. Confirmed twice by extracting HEAD (`git archive HEAD | tar -x`, see
[[head-copy-via-git-archive]]) and running the tier there: site-dock 09 on 2026-09-11, and
site-dock 11 the same day.

The four, and why each is not yours:

* `test_archgraph_extract::test_key_backbone_edges_present` — the extractor no longer resolves
  `sim_assets.build_shared_assets -> PlanningMixin.plan_warehouse`. The call is real
  (`simdriver/sim_assets.py`, `Inventory_Manager.plan_warehouse(...)` inside a nested function);
  the MRO resolution or the expectation is stale.
* `test_bin_mutation_sites::test_the_dead_site_is_still_dead` — the caller scan matches
  `StorageCart.add_from_bin` in `Inbound/trailer.py`'s **docstring and comment**. There is no call;
  it is matching prose.
* `test_schema_compatibility::..._conditional_table_is_declared` — `_shift_day_select`
  (`Optimization/persistence/Picking_Data.py`) is an undeclared conditional-table loader.
* `test_schema_compatibility::..._requirements_is_validated_here` — **working-tree only**: it walks
  `.claude/worktrees/*/` checkouts of other branches and reports their copies of production files
  as undeclared. It passes in a `git archive` copy, which carries no untracked worktrees.

A fifth trap sits beside them: **the architecture LAYER can be stale at HEAD**. `arch-synced-commit`
in `context/INDEX.md` read `20ba2fcd` while HEAD carried `8a5b4f42` — a code commit the chain had
never been run over — so regenerating `graph.json` surfaces someone else's drift inside your diff.

**Why:** the maintainer agents (`architecture-`, `context-`, `memory-maintainer`) leave their work
in the tree and do not commit, so a chain nobody ran is invisible until a verifier is run by hand,
and a tier nobody runs to green stays red. The cost is a session spent hunting a regression you did
not cause — and the mirror-image risk of dismissing one you did.

**How to apply:** run the tier, then attribute every failure before touching it — extract HEAD with
`git archive HEAD | tar -x -C <dir>` and run the same node IDs there. Anything failing in both is
not yours; write which ones in the ticket rather than "pre-existing". Before regenerating the arch
layer, read `arch-synced-commit` and say in the commit message which earlier commits your sync is
also covering. Related: [[hand-run-test-tiers-rot-silently]], [[real-test-coverage-is-317]].
