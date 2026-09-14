# Prove the calltree instrument is honest before trusting a number from it

Type: task
Status: resolved

`Tests/calltree/` and `Tests/bench/` are hand-run tiers in **no gate**. The documented failure mode
is that a measurement taken through a rotted harness looks perfectly healthy — it has already cost
this repo three dead frozen-oracle tests and a put-away configuration that had never executed outside
a unit test, which is exactly why the `_admit_held` quadratic survived every release.

## Question

Does the framework this whole effort depends on currently work?

## Answer

**No. Two independent breaks, both pre-existing on clean `develop` with zero source edits.**

### 1. A frozen oracle is failing

```
python -m pytest Tests/calltree -q
-> 1 failed, 21 passed in 550.10s
FAILED Tests/calltree/test_rank_cache_equivalence.py::test_minlabor_cache_matches_frozen_oracle
```

The assertion compares a 4-tuple. The first three elements are **identical**
(`285481, 134669, 639`); the trailing sha256 differs. So the same number of placements is made and
some placement lands differently.

The oracle file was last touched **2026-08-25** (`d0a84b36`). `Warehouse/placement/Assignment_Functions.py`
has changed **four times since**, including `fc7a46a5` (2026-09-05), a declared HARD BREAK, and
`6de12cae` (2026-09-13). A verbatim frozen copy that predates four changes to the thing it mirrors is
the prime suspect, but the test compares production-with-cache against production-patched-to-the-oracle
*in the same tree*, so a pure cost-model change should cancel on both sides. Root cause graduated to
its own ticket.

**Why it blocks:** `rank_minlabor` is fulfillment's **#1** and store's **#2** arm in `PHASE2_PAIRS`,
and it is the one family whose `_make_pool` copies the two-level `aisle_member_pos`. This oracle is the
designated guard for the highest-value planned refactor.

### 2. `run_fullfid` cannot run at all

`Tests/calltree/calltree_scenarios.py` `run_fullfid` passes `max_bins=20000, min_bins=5000` to
`build_shared_assets`. `plan_warehouse` now **raises** `UnfieldableRequirement` when a cap binds below
the declared levels:

```
10 bucket(s) cannot hold the stock levels this run declared, so the line floor cannot be kept:
    fulfillment/fulfillment/ff_medium/fulfillment: needs 413,260 bins, 900 emitted x fill = 765 available
    ...
```

Reproduced with the inbound config ON and OFF, byte-identical output, so it is unrelated to inbound.
Note `max_skus=300` does **not** shrink the requirement — fewer SKUs means each carries more of the
day's lines, so the levels grow. That is the documented self-defeating-cap effect, hit in the wild.

The consequence for this effort: `capture_fullfid` is the **only** tier that traces the real driver on
a real catalogue, and it is dead. Repair graduated to its own ticket.

### Clean baseline recorded

`python -m pytest Tests/unit -q -k "inbound or trailer or yard or dock or receiv or putaway or gain"`
-> **570 passed** in 32 s. That is the green baseline every later change is measured against.

## Comments

Both breaks are in tiers that gate nothing, which is why neither was reported by anybody. Whether to
add a fast subset of these tiers to a gate is a live question, deliberately not decided here.

### Architecture-tier baseline, recorded 2026-09-13

`python -m pytest Tests/architecture -q` -> **10 failed, 374 passed** in 84 s, on a working tree
carrying only this effort's `.scratch/` additions.

All ten are **pre-existing**. The files-catalog drift names
`Tests/unit/test_funnel_spec_pairs.py` — a test file from the previous day's commits whose functions
were never merged into `context/files.yml`; `.scratch/` does not appear in any failure. The arch layer
is simply mid-regeneration: `context/files.yml`, `context/arch/{graph,nodes,site_manifest}.json` and
`docs/architecture/**` are all modified-but-uncommitted in the working tree.

```
test_archgraph_extract.py::test_key_backbone_edges_present
test_archgraph_nodes.py::test_committed_nodes_is_current
test_archgraph_nodes.py::test_check_nodes_subprocess_clean
test_architecture_coverage.py::test_hotpaths_execute_under_e2e_driver
test_architecture_sync.py::test_architecture_matches_code
test_bin_mutation_sites.py::test_the_dead_site_is_still_dead
test_files_catalog_sync.py::test_catalog_matches_tree
test_files_catalog_sync.py::test_merge_is_idempotent
test_runtree_consumption.py::test_no_new_handwritten_contract_paths
test_schema_compatibility.py::test_every_loader_that_reads_a_conditional_table_is_declared
```

A standing memory records FIVE such reds as of ~2026-09-11; it is now ten, so that memory is stale
and the extra five accumulated since. Two are genuine source drift worth someone's attention
independently of this effort: `_shift_day_select` reads a conditional table without being declared in
`Picking_Data.CONDITIONAL_READS`, and `test_the_dead_site_is_still_dead` no longer holds.

**Consequence for this effort:** adding files under `Tests/` will trip the files-catalog gate, since it
already trips on a file added yesterday. The regeneration chain (`extract.py --write`,
`--catalog-merge`, fill every new `purpose: TODO`, `--write-nodes`, `render_html.py --build`) has to
run before this effort's own additions can be called clean — and it must not be confused with clearing
the ten reds above, which are someone else's to clear.
