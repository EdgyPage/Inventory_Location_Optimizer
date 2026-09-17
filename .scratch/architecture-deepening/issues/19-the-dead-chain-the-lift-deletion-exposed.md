# 19 - the dead chain the lift deletion exposed

Type: debt
Status: resolved
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

## Progress (2026-09-16) -- LoadParams deleted; two corrections to this ticket

### Correction 1: there was no DDL change. This ticket was wrong.

It said `load_lambda` / `load_k` / `load_gamma` were three `simulation_runs` columns needing the
schema pipeline. **They are keys in the per-run CONFIG DICT**, written to `config.json`
(`workunits.py:298-300`), and `Picking_Data.py` has no such columns at all. No DDL, no `--sync`,
no adoption. The only places the three names appear outside the driver are archived
`config.json` files under `docs/experiments/experiment-1/` -- published static records that this
change cannot alter.

The lesson is the same one ticket 03 recorded: check the claim before acting on it, including a
claim in a ticket I wrote myself an hour earlier.

### Correction 2: `recovered_params.json` does not exist

`sim_assets.py` read it behind `if os.path.exists(param_path)` and fell back to hardcoded
defaults. There is no such file in the repo, so the branch never fired and the "recovered"
parameters were always `lambda_=1.1, k=1.0, gamma=1.5` -- three constants, read off no disk,
pickled to every worker to parameterise nothing.

### Deleted

`LoadParams` (the dataclass in `inventory_common.py`), its construction and the
`recovered_params.json` probe in `sim_assets.py`, its slot in the `_shared` payload, its unpack
in `workunits.py` and `strategy_runner.py`, the three `config.json` keys, the log line in
`strategy_runner`, the `_HERE` comment that pointed at the missing file, and four imports
(two source, two test). `Tests/bench/profile_lifecycle.py` loses its unused construction.

Verification: `Tests/unit -k "not gpu"` **2652 passed**, 1 skipped -- unchanged.

**Gate 5 went RED and was fixed properly, not waved through.** Editing `workunits.py` and
`sim_assets.py` moved the run-tree shape fingerprint. `python -m Optimization.runschema.preflight`
proved the shape with TWO CANARY RUNS (A mixed 2-cell, B store-only single cell -- the case
CLAUDE.md records as having silently dropped every store-only run from the what-if scanners) in
62s, reported **tree shape UNCHANGED, schema 341e1422457c still valid**, and refreshed the
fingerprint. Validated against canaries rather than against a finished run, per memory
`verify-tree-uses-the-runs-own-contract`.

### NOT deleted: `delta_lift_idxs`, and why

It has no production caller -- three comments and nothing else. But it is woven into the
**calltree instrument's frozen anchors**: `Tests/calltree/test_calltree_smoke.py:411-431, 673-674`
carries recorded call counts and fitted exponents for `AffinityStore.delta_lift_idxs` and its
inner genexpr from a 2026-09-16 meso capture, and `calltree_growth.py:263` names it as "THE CASE
THIS EXISTS FOR, measured". `Tests/bench/profile_lifecycle.py:75` lists it in `_AFF_METHODS`.

CLAUDE.md §1 puts the calltree instrument in the tenth gate precisely because it "fails SILENTLY
and in the direction of looking healthy". Deleting the function those anchors are written
against needs the anchors RE-MEASURED, which is a ladder run, not an edit. That is a bigger act
than the payoff, so it is deferred with this reason rather than done blind.

`Tests/unit/test_affinity_lift_invariant.py` also still passes: its body exercises the affinity
store's own arithmetic, which is live. Only its docstring describes the deleted consumer.

### Still open on this ticket

- `delta_lift_idxs` -- needs the calltree anchors re-measured first (above).
- `init_lift_state` no longer initialises any lift: 27 references across 15 files. A rename, not
  a deletion, so strictly outside the "delete what isn't necessary" mandate -- and a hot-path
  rename has to update `SECTION_MAP` in the same instrument.
- The stale docstring in `test_affinity_lift_invariant.py`.

## Answer -- RESOLVED (2026-09-16)

### `init_lift_state` is now `init_placement_state`

27 references across 15 files. Not in the calltree instrument's `SECTION_MAP` (only a plain
call in `calltree_scenarios.py`), so no anchor had to move with it, and not a case-only rename,
so `render_html --build` did not delete the page it had just written.

The docstring described a job the method no longer had. It has two, and the split is worth
stating: the aisle ledger's MEMBERSHIP half, and the manager's own per-bin indexes
(`_bin_sku`, `_current_quantities`, the per-SKU singleton/pallet sets). The new name covers
both; `init_aisle_state`, which this ticket suggested, would have covered only the first.

`Tests/unit/test_affinity_lift_invariant.py`'s rationale also described the deleted consumer.
Rewritten: the identity it pins -- a whole-set `sum_lift` equals the sum of incremental
`2·delta_lift_idxs` -- is a property of the affinity STORE, not of any consumer, so it survives
its former subject. Flagged in it that `sum_lift` is live (it computes `task_stats.lift_sum`, a
real consumed column) and that `_delta_lift_from_row` is a different, live function.

### `delta_lift_idxs` stays, and this is the resolution rather than a deferral

It has no production caller -- three comments and nothing else. It is NOT deleted, and the
reason is recorded above in full: `Tests/calltree/test_calltree_smoke.py` carries frozen call
counts and fitted exponents written against it from a 2026-09-16 meso capture, and
`calltree_growth.py` names it as "THE CASE THIS EXISTS FOR, measured".

CLAUDE.md §1 puts that instrument in the tenth gate precisely because it fails silently in the
direction of looking healthy. Deleting the function its anchors are written against needs those
anchors RE-MEASURED, which is a ladder run rather than an edit -- and a ladder run is a
measurement this ticket has no reason to commission. A function with no caller costs nothing;
an instrument whose anchors have quietly stopped meaning anything costs a whole round. Closed
on that trade, not left open on it.

### Verification

`Tests/unit -k "not gpu"` **2671 passed**, 1 skipped. Gates 1-4 and 7-10 green; gate 5 went red
(the rename touched run-tree shape sources) and was revalidated by the full preflight against
two canaries -- tree shape UNCHANGED, fingerprint refreshed. Gate 6 red, unchanged, pre-existing.
