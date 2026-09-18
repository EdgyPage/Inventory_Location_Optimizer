# Architecture-tier drift — the seven that are not round 3's

Label: wayfinder:map
Opened: 2026-09-16

## Destination

Every failure in `Tests/architecture` on `develop` is either fixed, or has an owner and a written
reason. Reached when no one else has to re-derive which failures are theirs.

## Why this map exists

The round-3 closeout ran the full routine suite (2026-09-16, 34 min 51 s):

```
8 failed, 3543 passed, 3 skipped, 16 deselected
```

All eight are in `Tests/architecture`. **One was round 3's** — `test_merge_is_idempotent`, from
instrument constants added to `Tests/calltree/calltree_growth.py`; fixed by a derived-layer resync
in `45e7728f`. **The other seven are pre-existing source drift** and had been recorded nowhere but
a conversation, which is how they survive.

The attribution is structural rather than a judgement call: since `5330f1ad` that effort touched
only `Tests/`, `docs/`, `CLAUDE.md` and `.scratch/`, and all seven name files under `Optimization/`,
`Schema/`, `Warehouse/` or `Inbound/`. The memory *architecture tier is red on HEAD* also warns that
the failure COUNT moves with tree state and arch-layer staleness (16 vs 13 vs 5, all measured), so
a total is never the evidence — the individual assertion is.

## The seven

| # | test | what it says |
|---|---|---|
| [01](issues/01-backbone-edge-missing.md) | `test_archgraph_extract::test_key_backbone_edges_present` | `build_shared_assets → plan_warehouse` calls-edge absent |
| [02](issues/02-hotpath-sentinel-never-executed.md) | `test_architecture_coverage::test_hotpaths_execute_under_e2e_driver` | `_run_strategy_worker` sentinel never executed |
| [03](issues/03-add-from-bin-has-a-caller.md) | `test_bin_mutation_sites::test_the_dead_site_is_still_dead` | `add_from_bin` acquired a caller in `Inbound/trailer.py` |
| [04](issues/04-profiletree-store-stale.md) | `test_profiletree_consumption::test_the_committed_store_is_clean_and_current_now` | profiles-tree shape source changed since the store was synced |
| [05](issues/05-profiletree-fingerprint-disagrees.md) | `test_profiletree_consumption::test_source_fingerprint_matches_the_runtree_implementation` | two fingerprint implementations disagree on identical input |
| [06](issues/06-handwritten-contract-paths.md) | `test_runtree_consumption::test_no_new_handwritten_contract_paths` | 11 hand-written run-tree path sites above baseline |
| [07](issues/07-conditional-table-reader-undeclared.md) | `test_schema_compatibility::test_every_loader_that_reads_a_conditional_table_is_declared` | `_shift_day_select` reads a conditional table undeclared |

## Notes

- **Re-confirm before acting.** The evidence below is quoted from the 2026-09-16 full-suite run,
  which ran BEFORE the derived-layer resync in `45e7728f`. That resync regenerated `graph.json`,
  `files.yml`, `nodes.json` and the HTML site, and the three verifiers pass after it — so 01 in
  particular should be re-checked, since a backbone edge is read off the regenerated graph.
- **04 and 05 are probably one fix**, not two: a stale profiles-tree store and a fingerprint
  disagreement between `Schema.profile_tree` and `runschema.contract` are the two symptoms the
  `schema-maintainer` pattern expects when a shape source moves without the pipeline being run.
- **06 is a ratchet, not a break.** The test compares against a recorded baseline, so it fails on
  any INCREASE. Eleven sites across seven files is too many for one accidental commit; this looks
  like the baseline was recorded and then a feature landed without migrating onto the resolver
  accessors.
- **03 has a decision in it, not just a fix.** `add_from_bin` mutates a bin and emits no event.
  The test's own message says what it needs: "it needs one". Whether `Inbound/trailer.py` should
  stop calling it or the site should start emitting is a modelling call, not a mechanical one.

## Decisions so far

- 2026-09-18 — **03, 06 and 07 were the detectors, not the code.** Each was read at the line it
  matched: 03's caller is two docstrings, 07's loader is a docstring that says "select list",
  and 06's fifteen-hit file is an attribute named `_site`. All three scans now read code
  (AST references; a `.`-bounded token with `__slots__` lines skipped; string statements
  dropped from the SELECT blob), each with a non-vacuity test, and 06's remaining twelve rows
  were re-baselined as prose naming an artifact under the counter's own comments-count policy.
  See [03](issues/03-add-from-bin-has-a-caller.md), [06](issues/06-handwritten-contract-paths.md),
  [07](issues/07-conditional-table-reader-undeclared.md). The lesson is in
  `arch-tier-is-red-on-head`: read the matched LINE before writing a ticket that tells someone
  to change code.

## Fog

- Is 02 a real coverage gap or an instrument problem? A sentinel that "never executed" under the
  e2e driver is the same shape as the calltree blind spots round 3 spent its time on — the hot path
  may be running under a different name, or the driver may not reach it any more.
- How long have these been red? Nobody has bisected. `Tests/architecture` is not in any gate and is
  not run per-change (it builds the HTML site twice), so the window could be wide.
