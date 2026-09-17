# 01 - the cart volume sum never comes back down

Type: bug
Status: claimed

**PRE-RUN. This is the only ticket that touches `develop` before the phase-2 campaign launches.**

## Context

`Inventory_Manager._aisle_vol_sum` is seeded once per arm in `init_demand_state`
(`Warehouse/inventory/Inventory_Management.py:564`, seed at `:603-607`) and incremented on commit
by `rank_cartlabor` -- `Warehouse/placement/Assignment_Functions.py:1572` (function form) and
`:1830-1831` (`_TravelBalancedPool`, the shipped path).

**It is decremented nowhere.** `inventory_reorder._drop_sku_from_aisle` (def at
`Warehouse/inventory/inventory_reorder.py:202`) decrements `_aisle_lift_sum` at `:241`,
`_aisle_demand_sum` at `:244` and `_aisle_pick_load_sum` at `:247` -- and not `_aisle_vol_sum`.
Neither does the hoisted-locals twin in `_reclaim_empty_bins` (`:354-441`, the decrement block at
`:380-433`). The only full rebuild runs once per arm at setup, so the drift accumulates over the
whole run.

## Why this is a live defect and not a reporting one

`_aisle_vol_sum` is READ in the scoring expression. `_TravelBalancedPool`:

- seed `Assignment_Functions.py:1706`, `_cart_cost` `:1724-1726`
- `_score_of` `:1745-1756`: `score += self._cart_cost(self._vol_load[aid] + add)`
- argmin over `score` picks `best_aid` AND `best_choice` (the bin), commit at `:1830-1831`

So a monotonically-growing per-aisle penalty can flip the argmin and change where a unit lands.

**And `rank_cartlabor` is in the staged campaign twice.** `PHASE2_PAIRS`
(`Optimization/config/whatif_config.py:106-111`) carries `('rank_cartlabor','rank_minlabor')` and
`('tmin','rank_cartlabor')`. Launching without this fix bakes the drift into two of six pairs.

**Two bounds, stated conservatively.** Only `cart_on` arms are affected -- `cart is not None` is
False for `rank_labor` and every other rule. And `_cart_cost` is
`cart_coef * max(0.0, v_raw / cap_raw - 1.0)`, so the term is identically zero for any aisle still
under cart capacity: the drift is inert until an aisle crosses `cap_raw`, which
`Optimization/config/strategies.py:130-132` describes as "inert for the big store cart, bites for
the small fulfillment cart".

## What to build

The symmetric decrement, matching what the other three sums already do:

1. `_drop_sku_from_aisle` -- subtract the SKU's `_sku_vol_product` contribution beside the three
   existing decrements.
2. The inline twin in `_reclaim_empty_bins` -- the same, in the hoisted-locals form. The
   docstring at `inventory_reorder.py:210-214` says KEEP THE TWO IN SYNC; honour it.

**This is knowingly the hand-maintained-twin failure mode that ticket 02 exists to kill.** Two
lines in two places is the accepted price for a pre-run patch, on the condition that 02 is the
first thing that lands after the run. Do not attempt the derive-on-read form here -- it changes
the hot path in the scoring loop and is both a perf and a float-accumulation-order question.

## Verification

- A test that asserts the **magnitude** of the decrement on both paths, not merely that the value
  moved. A cap/floor that can be satisfied by zero is how this class of guard goes vacuous
  (memory `reloader-cap-floors-to-zero`).
- **The three placement equivalence files WILL fail, and should.**
  `test_co_demand_pool_equivalence.py`, `test_ranked_assign_pool_equivalence.py`,
  `test_travel_balanced_equivalence.py` pin float-exact agreement between the pool and wave halves;
  both halves share the bug today, so correcting one breaks the pin. Re-baseline them and say so
  in the commit message -- this is a named break, not a regression.
- Gate 10 (~20 s), `path_guard --scan`, `docref_guard --scan`.
- The commit message states which published results it invalidates: `rank_cartlabor` fulfillment
  arms, bounded to aisles that crossed cart capacity.

## Comments

Found by the 2026-09-16 architecture review. The guard that was supposed to catch this class --
`Tests/unit/test_placement_lifecycle.py:329-367` -- cannot: it asserts
`sum(sum(d.values()) for d in mgr._aisle_sku_counts.values()) > 0`, and `_aisle_sku_counts` is
written by `_execute_placement` (`Inventory_Management.py:869-872`) regardless of what the policy
commits. It is also written against `load_min`/`load_max`, a production-dead family.
