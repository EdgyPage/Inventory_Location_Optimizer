# Add the per-item charge and break the cost model

Type: task
Status: open

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
