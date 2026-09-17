# 03 - a placement policy is declared in four packages, and three of its registries are dead

Type: refactor
Status: claimed
Blocked by: 02

## Context

Competing placement policies are what this repo exists to measure, and adding one costs 5-8
declarations across four packages:

1. a pool class + builder in `Warehouse/placement/Assignment_Functions.py`
2. an import, a `_build_*` helper and a `_RESTOCKS` row in `Optimization/config/strategies.py`
   (`:20-41`, `:83-282`, `:320-341`)
3. a branch in `strategy_runner._gain_bundle_for` (`:206-350`), naming the exact ledger dicts its
   `take` commits to
4. a name in `Inbound.gain.FAITHFUL_GAIN_FAMILIES` (`:156`)
5. possibly rows in `AISLE_COPIERS` **and** `AISLE_VIEWS` (`gain.py:400-423`)
6. rows in `ASSIGNMENT_BUILDERS` / `RANKED_BUILDERS` / `SCORER_NEEDS` (`:2837-2860`)
7. a `test_*_pool_equivalence.py`
8. an edge in `context/arch/resolver_hints.yml`

**Three of those are already rotten.** `ASSIGNMENT_BUILDERS`, `RANKED_BUILDERS` and `SCORER_NEEDS`
have no production consumer -- the only reader in the repo is
`Tests/unit/test_assignment_functions.py:102-132`, which asserts their key sets. Their banner calls
them "programmatic name -> builder registries (robust downstream lookup)"; there is no downstream
lookup. They name `load_min`/`load_max`, which no arm runs, and omit all ten
`rank_*`/`map*`/`comp`/`expn` families. The real registry is `_RESTOCKS`, 17 rows, which they do
not mirror.

**And one arch hint is stale.** `resolver_hints.yml` (20 lines) declares only
`_stock_per_unit -> place_one` and `_stock_ranked -> place_wave`. The `open_pool -> _Pool.take`
edge -- the path 14 of 17 arms take -- is undeclared, so the generated code map shows the live
placement path as the legacy branch.

The dispatch seam itself is GOOD and stays: `Placement` (`inventory_common.py:125-166`) and `_Pool`
(`Assignment_Functions.py:560-599`) are small interfaces with six real adapters.

## What to build

One `PlacementPolicy` record per family in `Warehouse/placement/`, carrying `key`,
`needs_affinity`, `needs_demand`, `uses_aisle_index`, `order_score`, `open_pool`, `place_one`, and
`ledger_terms` (which priced quantities its `take` commits to -- available once 02 lands).

- `_RESTOCKS` becomes a list of these instead of a 6-tuple plus a hand-written `_build_*`.
- `_gain_bundle_for` becomes `policy.open_pool(cands, ledger.view())` with no per-family branch.
- `FAITHFUL_GAIN_FAMILIES` becomes a derived property.
- Delete `ASSIGNMENT_BUILDERS`, `RANKED_BUILDERS`, `SCORER_NEEDS` and the test that reads them.
- Declare the `open_pool -> take` edge in `resolver_hints.yml`.

**Also delete `_aisle_lift_sum` entirely, here.** It is write-only end to end (see map decisions):
the dict, the seeding in `init_lift_state` (`Inventory_Management.py:489-532`), the decrements at
`inventory_reorder.py:241` and in the reclaim twin, the `AisleMetricRecord.lift_sum` field
(`Optimization/metrics/Simulation_Analytics.py:521,541`), and the
`aisle_metrics.lift_sum` column (`Picking_Data.py:249-260`). The `load_min`/`load_max` family --
its only in-memory reader, `_build_load_assignment_fn` (`:288-360`) -- goes with it. Note the
decrement and per-arm reseed run today for **every** `needs_affinity` arm
(`strategy_runner.py:1461`), so this removes real work from the hot path.

**Templates already in this repo:** `Warehouse/picking/Workload_Builder.py:340-342` (`_SAMPLERS`,
3 adapters, loud refusal at `:378-382`) and `Warehouse/inventory/put_policy.py` (5 adapters, a
one-line interface, a `key_for` resolver, 130 lines). `_travel_or_cohesion` (`:510-530`) already
does it for 4 of 17 families.

## Verification

- The DDL drop rides the schema pipeline: `--sync` before, `--accept` after, commit-window comment
  written by hand.
- One parametrized test over the record list that **exercises** each policy rather than naming it
  -- the fix shape from memory `symbol-table-relationship-not-verified-by-symbols`.
- Equivalence suite; gates 2, 3, 4, 10. Gate 3 twice if any symbol rename is case-only
  (memory `case-only-rename-deletes-its-own-page`).

## Progress -- the dead registries and the stale hint are gone (2026-09-16)

**NOT resolved.** Two of the five pieces landed; the `PlacementPolicy` record and the
`lift_sum` / `load_*` deletion did not.

### Landed

**The three dead registries are deleted** -- `ASSIGNMENT_BUILDERS`, `RANKED_BUILDERS`,
`SCORER_NEEDS`, plus the three key-set tests that were their only readers.

Verified by grep before cutting rather than taken from the review: the only occurrences outside
`Assignment_Functions.py` were `Tests/unit/test_assignment_functions.py` and `context/files.yml`.
`Optimization/config/strategies.py` imports `build_trip_minimizing_assignment_fn` and calls it
directly at `:222`; it never touches the registry.

**Two of the deleted tests asserted things that were no longer true**, which is worth recording
because the docstrings read as authoritative:

- *"`strategies.py` looks builders up by these keys; a rename here is a KeyError there."*
  It does not, and a rename would have been a no-op.
- *"`SCORER_NEEDS` is how the runner decides whether to load the affinity DB and the demand
  maps."* The runner reads `needs_affinity` / `needs_demand` off `strategies._RESTOCKS`.
  `SCORER_NEEDS` was a second copy nobody consulted.

`test_built_scorers_carry_their_programmatic_name` was a real behavioural test that merely
routed through the registry; it now calls the builders directly, the way `strategies.py` does.
The module docstring records what was removed and why, so the next reader does not re-add them.

**`resolver_hints.yml` now declares the live placement path.** It had only
`_stock_per_unit -> place_one` and `_stock_ranked -> place_wave`. `_stock_ranked` branches on
`placement.is_pooled`, and 14 of 17 shipped rules take the POOLED branch -- so the generated
code map showed the legacy wave as the placement path and the pool as unreachable. Added
`_stock_ranked -> open_pool` and `_stock_ranked -> take`; the graph gained 7 edges, one per
`_Pool` subclass's `take`.

### Verification

`Tests/unit -k "not gpu"`: **2655 passed**, 1 skipped -- 2648 before, +10 new ledger tests,
-3 deleted registry tests. Gates 1, 2, 3, 7, 8, 9, 10 green; gate 6 red and pre-existing.

**No toy run for this commit, deliberately.** Nothing on a runtime path changed: three
module-level dicts with no reader were removed, a test was rewired to call what it already
called indirectly, and two resolver hints affect only the generated graph. Gate 2 validates the
import graph and the unit tier is green. The REMAINING work below does touch the write path and
a DDL, and that one needs a digest.

### What remains before this ticket resolves

1. **The `load_*` family and `lift_sum`.** The review said the family had no callers; it has
   two -- `Tests/unit/test_placement_lifecycle.py` (Stage 5, "load-aware reorder -- lift state
   maintained through a B/C-strategy restock") and `Tests/bench/perf_simulation.py`. So
   deleting it means deleting a passing test and editing a bench harness. That is a judgement
   call, not a mechanical removal, and it is **held for the user**: the family is production-
   dead and `lift_sum` is write-only, so deleting both is coherent, but it removes coverage
   that currently exists.
2. **The DDL drop** of `aisle_metrics.lift_sum` rides the schema pipeline (`--sync` before,
   `--accept` after). It will change the digest surface -- expect exactly one table's digests to
   move, and prove that it IS exactly that table rather than asserting it.
3. **The `PlacementPolicy` record itself**, which wants ticket 02 stage B (`ledger_terms`) first.

## Progress 2 -- lift_sum and the load_* family are deleted (2026-09-16)

Still CLAIMED: the `PlacementPolicy` record remains, and it wants ticket 02 stage B first.

### What went

`aisle_metrics.lift_sum` end to end -- the ledger dict and its `__slots__` entry, the manager
alias, the seeding in `init_lift_state`, the `lift_delta_fn` on `drop_sku` and both callers, the
`AisleMetricRecord` field, the DDL column, the `REQUIRES` entry, the INSERT, the loader, and the
`sim_semantics` entry. With it the `load_min`/`load_max` family (`_build_load_assignment_fn` and
its two wrappers, 128 lines), which was its only in-memory reader.

Schema pipeline followed as documented: `--sync` BEFORE the DDL edit, `--accept` after. `sim_db`
moved **c6bacfdb5c77 -> c37b50bf2b84**; the outgoing id is adopted into `known_ids` and I wrote
the commit-window comment by hand.

### The digest: TWO tables move on any DDL change, not one

This ticket predicted "expect exactly one table's digests to move". **Wrong, and the reason
generalises:** the run digests DIFFER on exactly two tables per arm, 136 arms --

| table | why |
|---|---|
| `aisle_metrics` | the removed column. Expected. |
| `simulation_runs` | carries `sim_schema_id`, which moved with the DDL. |

Verified column by column rather than assumed: `simulation_runs` differs only in `created` (the
wall-clock column, which `run_digest` already EXCLUDES from the comparable surface, so it is not
the cause) and `sim_schema_id` (`c6bacfdb5c77` -> `c37b50bf2b84`). Every other table on every
arm is byte-identical, so **no number moved** -- this is a pure deletion.

**Carry this forward:** in this repo a DDL change always moves two tables, because the schema id
is stamped into `simulation_runs`. "Exactly one table" is never the right prediction.

### Three things that cost real care

1. **A same-named decoy.** `task_stats.lift_sum` is a LIVE column -- read by
   `Performance_Evaluations/common/frames.py:211` and `core/context.py:87`, written via
   `affinity.sum_lift`, surfaced by `Visualization/readers/base.py:180,910`. A repo-wide grep
   returns both. Every hit was classified before anything was cut.
2. **A grep by symbol missed two importers.** `Tests/unit/test_index_equivalence.py` and
   `test_velocity_zoning.py` import `Tests/bench/perf_simulation.py`, which imported the deleted
   builder. Transitive importers do not appear in a grep for the symbol; only the full unit tier
   found them. The bench's B/C arms were `load_min`/`load_max` and are now `travel_min`/
   `travel_max` -- shipped policies, so the three-arm comparison keeps its point.
3. **Two vintage-fabrication tests broke instructively.** `test_era_wiring` and
   `test_free_index_by_bucket` build a fake old DB from the CURRENT DDL and drop everything added
   since; their own comment says that holds "only while nothing else has been added". Removing a
   column is the mirror case they did not anticipate. Restoring it needed DROP + CREATE, not
   `ALTER ADD COLUMN`, because the observed shape records column ORDER and `ADD COLUMN` can only
   append -- and `DROP TABLE` also took the two indexes, which the shape records too.

### Verification

`Tests/unit -k "not gpu"`: **2652 passed**, 1 skipped. Gates 1-5 and 7-10 green; gate 6 red,
unchanged, pre-existing. Digest: two tables, both explained above.

### Follow-on

Deleting this chain orphaned three more things -- `LoadParams` (a whole dead knob chain including
three `simulation_runs` columns), `AffinityStore.delta_lift_idxs` (now referenced only in
comments), and the name `init_lift_state`, which no longer initialises any lift. Written up as
ticket 19 rather than folded in here: this commit already carries one DDL move, and a second in
the same commit would make the two-table attribution above impossible to state.
