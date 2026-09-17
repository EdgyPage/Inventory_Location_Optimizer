# 03 - a placement policy is declared in four packages, and three of its registries are dead

Type: refactor
Status: needs-triage
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
