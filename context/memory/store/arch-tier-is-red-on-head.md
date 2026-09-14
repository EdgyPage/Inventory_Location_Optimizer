---
name: arch-tier-is-red-on-head
description: "Tests/architecture's failure COUNT moves with tree state and arch-layer staleness; a stable core of four is genuine source drift — DIFF against a git-archive copy, never trust a total"
metadata: 
  node_type: memory
  type: project
  originSessionId: 68469de0-3988-452f-9eb3-bc8fb5e0c145
  modified: 2026-09-14T00:00:00.000Z
---

**UPDATED 2026-09-14 — the count is not five, and never trust a single number from this tier.**
Re-measured after the `inbound-performance` effort closed (`c3b9aaba`, 12 commits past the arch
layer's last sync at `6de12cae` per `context/INDEX.md`'s `arch-synced-commit`): a git-archive copy
of HEAD read **16 failed / 367 passed / 1 skipped**, and the same working tree (dirty, mid-session)
read **13 failed / 371 passed**. Neither total is "the" number — see the method note below.

Of the 16 archive failures, **7 are archive-copy artifacts** (no `.git`, no untracked
worktrees, no live memory store to mirror against): `test_archgraph_nodes` (all three),
`test_architecture_html::test_integrity_committed_tree_matches_manifest`,
`test_docref_guard::test_reference_extraction_is_not_vacuous`,
`test_memory_sync::test_mirror_is_actually_tracked_by_git`,
`test_path_guard::test_every_tracked_file_is_clean`. Several more
(`test_archgraph_extract::test_committed_graph_is_current`,
`test_architecture_sync::test_architecture_matches_code`, all three `test_files_catalog_sync`
tests) are **arch-layer staleness at HEAD**, not source drift — they clear once the extract/nodes/
catalog chain is regenerated through the commits the layer hasn't seen yet.

**The stable core, confirmed present in both the archive run and `context/INDEX.md`'s own
`arch-synced-commit` comment (which lists it as pre-existing at its own sync point, `6de12cae`)**:

* `test_archgraph_extract.py::test_key_backbone_edges_present` — unchanged from the original
  finding below.
* `test_bin_mutation_sites.py::test_the_dead_site_is_still_dead` — unchanged, still matching
  prose in `Inbound/trailer.py`'s docstring, not a call.
* `test_runtree_consumption.py::test_no_new_handwritten_contract_paths` — unchanged.
* `test_schema_compatibility.py::test_every_loader_that_reads_a_conditional_table_is_declared` —
  **confirmed still failing, still `_shift_day_select`** (`Optimization/persistence/
  Picking_Data.py`), an undeclared conditional-table loader.

`test_architecture_coverage.py::test_hotpaths_execute_under_e2e_driver` is listed as pre-existing
in the INDEX comment but did **not** fail in the 2026-09-14 archive run — treat it as
environment/timing-sensitive, not a stable member of the core.

The original 2026-09-12 measurement (five, including one working-tree-only item) is kept below for
its detail on each failure's mechanism; the count itself is superseded by the paragraph above.

`python -m pytest Tests/architecture -q` reports **5 failed, 377 passed, 1 skipped** on a clean
checkout of `develop` (was 4 failed / 378 passed when this was written; a fifth arrived during
site-dock 16/17 and is listed below). Confirmed by extracting HEAD (`git archive HEAD | tar -x`,
see [[head-copy-via-git-archive]]) and running the tier there: site-dock 09, 11 and 18, all
2026-09-11.

**A git-archive copy has its own reds** — about six more, from having no `.git`, no committed
generated tree and no memory mirror (`test_archgraph_nodes`, `test_architecture_html::
test_integrity_committed_tree_matches_manifest`, `test_docref_guard`, `test_memory_sync`,
`test_path_guard`). Do not count those; DIFF the two failure lists rather than comparing totals.

The five, and why each is not yours:

* `test_archgraph_extract::test_key_backbone_edges_present` — the extractor no longer resolves
  `sim_assets.build_shared_assets -> PlanningMixin.plan_warehouse`. The call is real
  (`simdriver/sim_assets.py`, `Inventory_Manager.plan_warehouse(...)` inside a nested function);
  the MRO resolution or the expectation is stale.
* `test_bin_mutation_sites::test_the_dead_site_is_still_dead` — the caller scan matches
  `StorageCart.add_from_bin` in `Inbound/trailer.py`'s **docstring and comment**. There is no call;
  it is matching prose.
* `test_schema_compatibility::..._conditional_table_is_declared` — `_shift_day_select`
  (`Optimization/persistence/Picking_Data.py`) is an undeclared conditional-table loader.
* `test_runtree_consumption::test_no_new_handwritten_contract_paths` — the fifth, and it is NOT
  in the original four: it was already red in a `git archive` copy of `c96a4711`, so it arrived
  during site-dock 16/17 and belongs to neither.
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

**CONFIRMED 2026-09-14, same day, and it closes the hypothesis above.** The arch layer was
regenerated through the commits it had not seen (`extract.py --write` -> `--catalog-merge` ->
`--write-nodes` -> `render_html.py --build`, `arch-synced-commit` advanced `6de12cae` ->
`c3b9aaba`). The tier then read **5 failed / 379 passed** -- the four-item stable core plus
`test_hotpaths_execute_under_e2e_driver`, i.e. exactly the FIVE the original version of this
memory recorded.

So the original "five" was right, and every higher number measured that day was the derived layer
trailing HEAD, not drift accumulating:

| when | reading | why |
|---|---|---|
| original memory | 5 | the layer was in sync |
| mid-session, before adding files | 10 | derived layer stale by ~12 commits |
| mid-session, after adding 3 files under `Tests/` | 13 | the catalog gate reacting to the new files |
| after regenerating at HEAD | **5** | back to the core |

**The practical rule this earns:** before concluding the tier has got worse, check
`context/INDEX.md`'s `arch-synced-commit` against HEAD. If it trails, the extra failures are the
layer, and regenerating is the measurement -- not a fix. A session that adds any file under
`Tests/` or any new symbol will add catalog/graph failures on top of that, and those are its own
to clear.
