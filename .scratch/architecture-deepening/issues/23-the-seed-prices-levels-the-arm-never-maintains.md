# 23 - the seed prices levels the arm never maintains

Type: refactor
Status: resolved
Blocked by: -

Split from ticket 20, which deleted the column that RECORDED the wrong number. This is the
half that makes the drift unrepresentable rather than merely unrecorded.

## The mechanism, measured

`init_demand_state(inventory, wp)` prices all three levels off whatever is placed at that
moment -- `demand_sum`, `pick_load_sum`, `vol_sum`, each summed from its per-SKU product over
`sku_sets[aid]`. At that instant the ledger reconciles exactly.

Then `enqueue_all` runs the initial stock through the ARM'S OWN placement policy, which
maintains only the levels it scores on (`PlacementPolicy.ledger_terms`): `demand_sum` always,
`pick_load_sum` on the two labour balancers, `vol_sum` on `rank_cartlabor` alone. From the
first placed unit the other two are stale -- and they keep moving the WRONG way, because
`drop_sku` decrements both unconditionally from the per-SKU products.

Measured on `uni_cluster_map_norsl`, 120 SKUs, 65 aisles: 65 of 65 aisles drifted on both
levels, identically on both sides of ticket 02's ledger refactor, and present BEFORE any
drain.

## What to build

`init_demand_state(inventory, wp=None, terms=None)`:

- `terms is None` keeps today's behaviour (price everything) -- tests, `perf_simulation` and
  `calltree_scenarios` all call it without an arm;
- otherwise price ONLY the declared terms, **both the per-SKU product and the level**. Pricing
  the product without the level leaves `reconcile()` comparing an empty level against seeded
  products, which is a finding about the seed rather than about the warehouse.

The worker passes `POLICY_BY_KEY[strat.restock].ledger_terms`.

Then `AisleLedger.reconcile()` needs no `levels=` argument, and
`Tests/integration/test_placement_pool_audit.py` can stop deriving the level list per arm:
the invariant becomes unconditional, which is the strongest form it has had.

## Already checked

The two per-SKU product dicts are read ONLY by the labour builders in `strategies.py` and by
`_pool_travel_balanced` in `strategy_runner` -- both labour-only. So skipping them on the
other fifteen arms reaches nothing.

## Verification

- `run_digest.py` should be **IDENTICAL**: ticket 20 removed the only column that recorded
  either level, so this change is invisible to the database. If it is not identical, the
  finding is that something reads a level its arm never maintained.
- `reconcile()` with no arguments passes on every arm in
  `test_placement_pool_audit.py::test_every_pool_leaves_the_aisle_ledger_reconciled`.
- Gates 1, 2, 10.


---

## RESOLVED 2026-09-17, in ticket 20's commit

Split out and then done immediately: with ticket 20 removing the only column that recorded
either level, this became digest-invisible, and ticket 03 had just landed the declaration it
needs.

`init_demand_state(inventory, wp=None, terms=None)`. `None` prices everything (every caller
without an arm -- tests, the benches, the diagnostics). The worker and
`calltree_scenarios.build_assets` both pass `POLICY_BY_KEY[strat.restock].ledger_terms`; the
fixture matters as much as the worker, because a fixture that seeds differently from production
can only tell you about itself.

The per-SKU PRODUCT goes with its level, as the ticket required: seeding the product without
the level leaves `reconcile()` comparing an empty level against products nobody summed.

### Measured, before and after

| arm | maintains | before | after |
|---|---|---|---|
| `rank_cartlabor` | 3 of 3 | 0 findings | 0 |
| `rank_labor` | 2 of 3 | 65 | 0 |
| `cluster_map`, `comp` | 1 of 3 | 130 | 0 |

### What that bought

`AisleLedger.reconcile()` is unconditional, and its `levels=` argument is deleted -- the
condition that needed it is gone, so the affordance goes with it rather than waiting for a
caller that will never come. `test_every_pool_leaves_the_aisle_ledger_reconciled` asks the
whole question of every pooled arm, which is the strongest form the invariant has had.
