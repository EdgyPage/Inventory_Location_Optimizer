# 19 - the dead chain the lift deletion exposed

Type: debt
Status: needs-triage
Blocked by: 03

Deleting `aisle_metrics.lift_sum` and the `load_min`/`load_max` family (ticket 03) orphaned
three more things. Each was load-bearing only for the family that is now gone. Held out of that
commit deliberately: it already carried a DDL move, and a second schema change in the same
commit makes the digest attribution ("exactly one table moved") impossible to state.

## 1. `LoadParams` is a whole dead knob chain, including three DB columns

`LoadParams` (`Warehouse/inventory/inventory_common.py:169`) is constructed, pickled to every
spawned worker, logged, and written to the database -- and its only consumer was
`_build_load_assignment_fn`, which no longer exists.

| site | what it does now |
|---|---|
| `Optimization/simdriver/sim_assets.py:268-275` | reads `recovered_params.json` if present, else defaults; builds the object |
| `Optimization/simdriver/workunits.py:250, 487` | reads it from `shared`, puts it in the payload |
| `Optimization/simdriver/workunits.py:298-300` | writes `load_lambda` / `load_k` / `load_gamma` |
| `Optimization/simdriver/strategy_runner.py:1318` | `args['load_params']` |
| `Optimization/simdriver/strategy_runner.py:1339` | logs lambda/k/gamma -- **the only remaining use** |

So a value is read off disk, pickled to 120 processes, and logged, to parameterise nothing.

`recovered_params.json` beside `sim_assets.py` goes with it. The three `simulation_runs`
columns are a SECOND DDL change and ride the schema pipeline exactly as ticket 03's did:
`--sync`, edit, `--accept`, write the commit-window comment by hand.

Check before cutting: whether any analysis or published experiment reads `load_lambda` /
`load_k` / `load_gamma` off `simulation_runs`. Ticket 03's experience says the review's answer
is not enough -- grep it yourself, and watch for a same-named decoy (see below).

## 2. `AffinityStore.delta_lift_idxs` is orphaned

After ticket 03 it is referenced in three COMMENTS and nowhere else in
`Warehouse/`, `Optimization/`, `Inbound/`, `Diagnostics/` or `Visualization/`:

- `Warehouse/catalog/Affinity_Store.py:394` -- "Faster than delta_lift_idxs for large member sets"
- `Warehouse/inventory/Inventory_Management.py:411` -- a comment on `_aisle_idx_sets`
- `Warehouse/placement/Assignment_Functions.py:259` -- "Same CSR row-slice as delta_lift_idxs"

Its tests are in `Tests/unit/test_affinity_lift_invariant.py`. **Note what does NOT go with it**:
`_delta_lift_from_row` is a different function, live in the co-demand and cluster families, and
pinned by `Tests/calltree/test_rank_cache_equivalence.py`. `sum_lift` is also live -- it still
computes `task_stats.lift_sum`, which is a REAL consumed column (see below).

## 3. `init_lift_state` no longer initialises any lift

27 references across 15 files. It still seeds the ledger's membership dicts and rebuilds
`_bin_sku`, `_current_quantities` and the per-SKU bin indexes, so the method is needed -- only
its NAME is now a lie. `init_aisle_state` or similar.

Held out of ticket 03 because a 15-file rename inside a DDL commit is a review problem, and
because a hot-path rename has to update `SECTION_MAP` in the calltree instrument
(memory `calltree-framework-first-findings`) and can trip the case-only-rename trap
(`case-only-rename-deletes-its-own-page`) if the new name differs only in case, which it should
not here.

`Tests/unit/test_affinity_lift_invariant.py::test_seed_equals_incremental_rebuild` also has a
docstring describing behaviour that no longer exists ("init_lift_state seeds `_aisle_lift_sum`
... the reorder path maintains it"). The test body exercises the affinity store's arithmetic and
still passes; only its rationale is stale.

## THE TRAP, recorded because it nearly bit

**There are two `lift_sum` columns and only one of them is dead.**

- `aisle_metrics.lift_sum` -- write-only, DELETED in ticket 03.
- `task_stats.lift_sum` -- per-task `sum_lift`, **live**, read by
  `Optimization/Performance_Evaluations/common/frames.py:211` and `core/context.py:87`, written
  by `Simulation_Analytics.py:381` via `affinity.sum_lift`, and surfaced by
  `Visualization/readers/base.py:180, 910`.

A repo-wide `grep lift_sum` returns both. Anyone acting on this ticket must classify each hit
before touching it; ticket 03 did that classification and it is written up in its Answer.

## Verification any of this needs

- A toy run + `run_digest.py` against the current baseline. For the `simulation_runs` columns,
  expect exactly ONE table's digests to move and prove that it is that one.
- Ticket 03's other lesson: a grep by file COUNT missed two importers, because
  `Tests/unit/test_index_equivalence.py` and `test_velocity_zoning.py` import
  `Tests/bench/perf_simulation.py`, which imported the deleted symbol. **Transitive importers do
  not appear in a grep for the symbol.** Run the full unit tier before believing a deletion is
  contained.
