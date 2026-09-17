# 20 - two priced levels are seeded once and then abandoned on 15 of 17 arms

Type: bug
Status: resolved
Blocked by: -

Found by ticket 02 stage B, the first time `AisleLedger.reconcile()` was pointed at a real
manager per arm. **Not introduced by it** -- measured identical on stage A and stage B.

## What was measured

`Tests/calltree/calltree_scenarios.build_assets` on `uni_cluster_map_norsl`, 120 SKUs,
65 aisles, straight after setup and again after a pool drain:

```
before drain : {'pick_load_sum': 65, 'vol_sum': 65}
after  drain : {'pick_load_sum': 65, 'vol_sum': 65}
```

65 of 65 aisles, on both revisions, before the drain. So this is not a drain effect and not
an add-half effect -- it is the initial stocking pass.

## The mechanism

`init_demand_state(inventory, wp)` prices **all three** levels off whatever is placed at that
moment: `demand_sum`, `pick_load_sum`, `vol_sum`, each summed from its per-SKU product over
`sku_sets[aid]`. At that point the ledger reconciles exactly.

Then `enqueue_all` runs the initial uniform stock through the ARM'S OWN placement policy, and
a placement family maintains only the levels it scores on:

| family | maintains |
|---|---|
| `rank_labor` | `demand_sum`, `pick_load_sum` |
| `rank_cartlabor` | `demand_sum`, `pick_load_sum`, `vol_sum` |
| the other 15 | `demand_sum` |

So from the first placed unit onward, `pick_load_sum` and `vol_sum` are stale on every other
arm -- and they keep moving the WRONG way, because `drop_sku` decrements both unconditionally
from the per-SKU products. Seeded high, never incremented, decremented on every reclaim: the
`lift_sum` shape exactly (ticket 19), and the `vol_sum` shape before ticket 01 fixed its drop
side.

## Why nothing has broken

Checked, not assumed:

- `aisle_metrics` persists `demand_sum` and `pick_load_sum`. **`vol_sum` is not persisted at
  all.** So the wrong number reaches the database for one of the two.
- `aisle_metrics.pick_load_sum` has **no reader** outside the writer
  (`Simulation_Analytics.py:538`), the loader (`Picking_Data.py:2742`) and its
  `sim_semantics` column declaration. No Quantity, figure, view or experiment reads it --
  the same audit that condemned `lift_sum`.
- In memory, `pick_load_sum` is read only by `_TravelBalancedPool` / `_travel_balanced_impl`,
  i.e. by `rank_labor` and `rank_cartlabor`, which are exactly the arms that maintain it.
  `vol_sum` is read only by the cart scorer, i.e. `rank_cartlabor` alone.

So it is dormant today. It is worth a ticket because that is precisely what `vol_sum` was
before `rank_cartlabor` started reading it -- at which point a stale level began moving real
placements, and did so for the life of the feature.

## The three ways out, and what each costs

1. **Seed only what the arm maintains.** `init_demand_state` would take the arm's terms and
   price nothing else. Cheapest and most honest, but it changes what `aisle_metrics.
   pick_load_sum` records on 15 arms from a stale seed to 0.0 -- a comparability break in a
   persisted column, for a column nothing reads.
2. **Maintain every level on every arm.** Symmetric and makes `reconcile()` unconditional,
   but it makes every family pay for terms it does not score on, and it MOVES THE NUMBERS on
   the two arms that do read them only if done wrong -- so it needs the toy-run digest.
3. **Delete `pick_load_sum` from `aisle_metrics`** and keep it in memory only, where its two
   readers are also its two writers. This is what ticket 19 did to `lift_sum`, and the
   evidence is the same: no reader outside the writer/loader pair.

Option 3 is the one that passes the deletion test; options 1 and 2 keep a column alive to
record a number nobody asks for. Decide it against `sim_semantics` and the schema pipeline --
a DDL change here moves two tables (`aisle_metrics` and `simulation_runs`, which carries
`sim_schema_id`), and the vintage fixtures put a removed column back by DROP/CREATE, never by
ALTER ADD COLUMN, because the observed shape records column order and the indexes.

## What already guards it

`Tests/integration/test_placement_pool_audit.py::test_every_pool_leaves_the_aisle_ledger_reconciled`
reconciles each arm on the levels its family maintains, read off the pool's own ledger
(`AisleLedger.maintained_levels()`), and separately pins the two labour families' term sets.
So a family that silently stopped maintaining a level fails there; this ticket is about the
levels no family maintains.


---

## Progress -- the column is gone (2026-09-17)

**Option 3 taken**, on the evidence the ticket already had: `aisle_metrics.pick_load_sum`
recorded a number that is wrong on 15 of 17 arms and that no Quantity, figure, view or
experiment reads. That is the same audit, with the same conclusion, as `lift_sum` in ticket 19.

### What went

The `AisleMetricRecord` field, the DDL column, the `REQUIRES` entry, the INSERT column string
and its value tuple, the loader arm, and the `sim_semantics` declaration. The in-memory
`_aisle_pick_load_sum` STAYS -- its two readers (`_TravelBalancedPool` and
`_travel_balanced_impl`) are its two writers, and they are exactly the two arms that maintain
it.

`Visualization/RECONSTRUCTION.md` advertised these columns for state reconstruction and said
"Only written by strategies that maintain aisle state -- empty for most arms". **That was
wrong in both directions**: they are written for every arm, and the value is stale on most of
them. Corrected rather than deleted, because the row is still true of `demand_sum`.

### One read kept deliberately

`snapshot_aisle_metrics` still reads `_aisle_pick_load_sum` -- for the ROW SET only. It is one
of the three dicts whose union decides which aisles get a row, and dropping it could change
that union, which would make the column removal indistinguishable from a row-set change in the
digest. Commented at the site.

### Schema pipeline

`--sync` before the DDL edit, `--accept` after. `sim_db` moved
**c37b50bf2b84 -> d9854632d1b0**; the outgoing id is adopted into `known_ids` and the
commit-window comment is written by hand. Ticket 03's rule applies: a DDL change moves TWO
tables in the digest, `aisle_metrics` and `simulation_runs` (which carries `sim_schema_id`).

### The in-memory half went with it -- ticket 23, same commit

Deleting the column would only have stopped the wrong number being RECORDED. Ticket 23 was
split out and then done in the same pass, because ticket 03 had just made it cheap:
`init_demand_state(inventory, wp, terms=...)` now prices only the levels the arm declares in
`PlacementPolicy.ledger_terms`, so there is no level that is priced and then abandoned.

Measured before and after, on `build_assets` fixtures:

| arm | maintains | findings, all levels priced | after |
|---|---|---|---|
| `rank_cartlabor` | 3 of 3 | 0 | 0 |
| `rank_labor` | 2 of 3 | 65 | 0 |
| `cluster_map`, `comp` | 1 of 3 | 130 | 0 |

`AisleLedger.reconcile()` is therefore UNCONDITIONAL again, and its `levels=` argument was
deleted with the condition that needed it.
`test_every_pool_leaves_the_aisle_ledger_reconciled` asks the whole question of every arm.


### One more found the same way

`test_visualization_data.py::test_aisle_metrics_roundtrip` failed, exactly as it did for
`lift_sum` in ticket 19 -- **the unit tier covers neither deletion**. That roundtrip is the
only place a record, its INSERT column list and its loader meet a real file together, and it
has now caught two column removals in two days. Said so in its docstring rather than fixing it
quietly.
