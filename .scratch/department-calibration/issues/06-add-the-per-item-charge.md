# Add the per-item charge and break the cost model

Type: task
Status: resolved

## Question

Land the cost-model change decided by
[Define the calibrated era](01-define-the-calibrated-era.md) — a HARD BREAK, by decision
(ADR-0001 in `docs/adr/`): old fixtures re-baseline, no archived result stays comparable.

**The model.** Picking: `M(y) · (intercept + qty · per_item + qty · var)`, per-item charge
default **0.5 s** in the `PickConfig` dataclass itself (the non-zero default is the decision),
both channels. Put-away: picking's shape, intercept × `put_intercept_scale` (default 0.5),
per-item charge × `put_item_ratio` (default 0.2). Receiving: ONE intercept per pack
× `recv_intercept_scale` (default 1.0) plus ONE per-item charge per PACK, not per item — five
pallets and a bag of singletons is six charges. `PutawayCost` and `UnloadCost` keep their
BY-REFERENCE link to picking's coefficients; the scales are the only place a crew's numbers may
differ (both files forbid a second literal set: "two default sets that drifted 55x apart").

**Where it lives.** `per_pick` (`Warehouse/kernel/cost_model.py`) is the shared primitive all
three crews call — the charge is a parameter there, default 0.0, so nothing forks. `_pick_time`
(`Warehouse/picking/Pick.py`), `put_cost` (`Warehouse/operations/putaway.py`) and
`unload_cost` (`Inbound/unload.py`) pass their crew's value. The scales are settings knobs with
the full five-seam contract (memory `config-knob-has-five-seams`: declare, CONFIG + accessor,
CLI, run-spec record + BOTH restore sites, `workunits._shared`). Check `docs/macros.py`, which
rebuilds `PickConfig` from an archived `config.json` by filtering `dataclasses.fields`, and
`run_map_precompute`, for the same reason.

**Two docstrings are wrong today and must be fixed with the change:** the `PickConfig` comment
and `_pick_time` read as if the intercept were per item — it is charged once per pick LINE (one
bin visit for one SKU); and `cost_model.py`'s bracket comment says the height multiplier
"scales ONLY the per-unit handling term (not the intercept)" while `per_pick` scales the whole.
`_pick_time`'s docstring has it right. Say which is the model, once.

**Tests.** (1) The gain evaluator (`Inbound/gain.py`) and the demand-mass yardsticks
(`workunits._yardsticks`) carry the charge — a sabotage test: zero the charge in one and the
priced hours must move. (2) Receiving charges per pack: a split delivery costs
`n_packs × charge`, not `n_items × charge`. (3) The three scales flow from CLI to worker
(`_shared`), the spawn trap. (4) Golden fixtures re-baselined, the commit message naming the
break; the byte-identical proofs for THIS change do not apply and must not be faked with a
0.0 default.

Resolves when the change is on `develop` with the tests green and the two docstrings fixed.

## Answer

**Landed on `develop` (2026-09-05), tests green (Tests/unit + test_receiving_phase: 1587
passed), the two docstrings fixed, the nine gates clean.** ADR-0001 stands as written.

**The model, stated once.** `cost_model.per_pick` is now
`mult · (intercept + qty · per_item + qty · var)`; `per_item` defaults to 0.0 in the PRIMITIVE
(so it does not fork, and a zero charge is bit-identical to the old expression) and is
non-zero at the model level: `PickConfig.pick_per_item = 0.5`. The pick-model defaults moved
INTO the kernel (`DEFAULT_PICK_*`, `DEFAULT_PUT_INTERCEPT_SCALE`, `DEFAULT_PUT_ITEM_RATIO`,
`DEFAULT_RECV_INTERCEPT_SCALE`) because `wh_operations` may not import `wh_picking`, and
`PickConfig`, `WorkloadParams` and `PutawayCost` all reference them - the three copied
literal sets are gone.

**Where the charge flows - wider than the three crews.** It rides `WorkloadParams.pick_per_item`
into EVERY `per_pick` caller: `_pick_time`, `put_cost`, `unload_cost`, Workload's P-term,
`Order.labor_cost` (the `pick_per_item` kwarg is keyword-only and REQUIRED), `optimal_work`
and `build_optimal_map`'s per-pick floor, the gain evaluator's at-bin price, and all six
placement scorers. Not a widening for its own sake: the existing lockstep tests (P == sum of
pick time; labor_cost == pick time at qty 1) require it, and pricing placement on a model the
sim no longer bills is the drift `cost_model` exists to prevent.

**Two things that were not true before this ticket.** (1) The put crew was NEVER built from the
run's pick config - `Inventory_Management` defaulted to `PutawayCost()`, so every put was
priced at the kernel's 1.0 while the store arms pick at intercept 15. `PutawayCost.from_pick`
and `UnloadCost.from_putaway` now run in `strategy_runner`, from THIS channel's `pick_cfg`,
scaled by the payload record. (2) Receiving's per-item charge is put-away's (0.2 x picking's)
charged ONCE PER PACK, added outside `per_pick`'s quantity term: five pallets and a bag is six
charges (test pins it). The existing `--inbound-unload-*` overrides overlay the chain and were
kept.

**The three scales, five seams each.** `PUT_INTERCEPT_SCALE` / `PUT_ITEM_RATIO` /
`RECV_INTERCEPT_SCALE` in settings (importing the kernel defaults, one literal), CONFIG keys,
`crew_cost_spec()` read at call time, `--put-intercept-scale` / `--put-item-ratio` /
`--recv-intercept-scale`, run-spec record + `_apply_run_spec` + `_apply_run_shape`, and
`workunits._shared['crew_cost']`. The `test_run_shaping_params` prefix scan covers the restore
half automatically. `RECV_INTERCEPT_SCALE` was added to `test_receiving_params`' allowlist as a
PRICE, not physics.

**The archive.** The leaf `config.json` records `pick_per_item`. A pre-charge archive is
reconstructed WITHOUT the term by both `run_map_precompute` (`kw.setdefault(..., 0.0)`) and
`docs/macros.pick_time_formula` (renders no $t_1$) - the page and the map describe the run,
never this checkout's default. Experiment sites 1-4 are untouched; their formula pages are
correct for their runs.

**Re-baselined tests.** `test_putaway_timing` (longhand expression; defaults now scaled),
`test_assignment_and_labor` (height scaling, task-labor split), `test_receiving_phase` (unload
vs put-at-origin now differ by (qty-1) charges - the claim is the granularity), the
`_TravelBalanced` oracle, and the pool `__slots__` gained `_per_item`. Plus stub params in
five files. New: `Tests/unit/test_per_item_charge.py` (22 tests: the model once, the
non-zero default, one literal set, per-line intercept / per-item charge, put scaled, receiving
per pack, the two SABOTAGE tests on the gain evaluator and the W* yardstick, the five seams,
the archive vintage rule).

**Left as found.** `put_crew_spec` still reads `_s.` directly (ticket 08's trap fix). The
architecture catalog merge also absorbed pre-existing drift (gain_plan tests, whatif
PHASE2 constants, two removed restock_selection tests) - in the same commit, since a stale
graph fails the gate.

## Comments

**2026-09-05 — claimed; code and tests written, NOTHING RUN YET (session paused by request).**
Every production edit and every test-side re-baseline is in the working tree, uncommitted.
The one targeted run before the pause (10 test files) failed only on callers/stubs that were
then fixed; the fixes have not been re-run. Resume with, in order:

    python -m pytest Tests/unit/test_per_item_charge.py -q
    python -m pytest Tests/unit/test_putaway_timing.py Tests/unit/test_assignment_and_labor.py Tests/unit/test_standing_yard.py Tests/integration/test_receiving_phase.py Tests/unit/test_gain_plan.py Tests/unit/test_settings_module.py Tests/unit/test_run_shaping_params.py Tests/unit/test_inventory_optimal_solver.py Tests/unit/test_assignment_functions.py Tests/unit/test_warehouse_sizing.py Tests/unit/test_fulfillment_channels.py -q
    python -m pytest Tests/unit -q          # the re-baseline sweep: any exact-number test of a pick/put/labor cost is EXPECTED to move
    python context/guards/path_guard.py --scan ; python context/guards/docref_guard.py --scan

Interpretations made while building (record in the Answer when resolving):
- The charge flows through `WorkloadParams.pick_per_item` into EVERY `per_pick` caller — the
  six placement scorers, `Order.labor_cost`, `optimal_work`, the gain evaluator, Workload's
  P-term — not only the three crews, because the existing lockstep tests (P == sum of pick
  time; labor_cost == pick time at qty 1) require it and `cost_model`'s charter is lockstep.
- Receiving's per-item charge is PUT-AWAY's (0.2 x picking's), once per pack, per ticket 01's
  by-reference chain picking -> put-away -> receiving; `unload_cost` adds it outside the
  quantity term.
- The pick-model DEFAULTS moved to the kernel (`cost_model.DEFAULT_PICK_*`) because
  wh_operations may not import wh_picking; PickConfig/WorkloadParams/PutawayCost reference them.
- `PutawayCost` was never built from the run's PickConfig before (put-away priced at a literal
  1.0 while store arms pick at 15); `PutawayCost.from_pick` / `UnloadCost.from_putaway` now
  run in `strategy_runner`, scaled by the `crew_cost` payload record. The existing
  `--inbound-unload-*` overrides overlay that chain (kept, not removed).
- `run_map_precompute` and `docs/macros.py` reconstruct a pre-charge archive WITHOUT the term
  (vintage rule), never at this checkout's default.
Still to do after green: the commit (message names the hard break), the Answer, close, map
Decisions-so-far line, and ticket 08 unblocks.
