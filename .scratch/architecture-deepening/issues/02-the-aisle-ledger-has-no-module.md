# 02 - the aisle ledger has no module

Type: refactor
Status: needs-triage
Blocked by: 01

**POST-RUN. This is the first thing that lands after the campaign, per ticket 01's condition.**

## Context

Eleven parallel dicts live as loose attributes on `Inventory_Manager` -- `_aisle_sku_sets`,
`_aisle_idx_sets`, `_aisle_sku_counts`, `_aisle_lift_sum`, `_aisle_demand_sum`,
`_aisle_pick_load_sum`, `_aisle_vol_sum`, `_aisle_member_pos`, plus the per-SKU products
`_sku_demand_product`, `_sku_pick_load_product`, `_sku_vol_product`
(`Warehouse/inventory/Inventory_Management.py:394-428`, seeded at `:489-532` and `:564-608`).

They are ONE concept -- what each aisle currently holds, priced seven ways -- and they have two
writers who do not know about each other:

- **The add half lives inside placement.** `_execute_placement` updates only `_aisle_sku_counts`
  (`:869-872`); every other dict is incremented by the policy itself, in whichever of ten copied
  commit blocks that policy happens to run: `Assignment_Functions.py:345-352`, `410-417`,
  `689-694`, `833-843`, `1031-1037`, `1208-1214`, `1563-1572`, `1821-1832`, `2158-2164`,
  `2693-2699`. **Each copy maintains a different subset.**
- **The drop half lives in reorder, in two copies.** `_drop_sku_from_aisle`
  (`inventory_reorder.py:202-247`) and a hoisted-locals inline twin in `_reclaim_empty_bins`
  (`:354-441`), whose docstring says KEEP THE TWO IN SYNC.

Two drifts already shipped from this: ticket 01 (`_aisle_vol_sum`, live in scoring) and ticket 03
(`_aisle_lift_sum`, write-only).

The "who mutates what" fact is additionally restated in three places that are not the code that
establishes it: the pool's own `take`, `_gain_bundle_for`'s per-family `aisle_state=` lists
(`Optimization/simdriver/strategy_runner.py:206-350`), and a hand-written `FAMILIES` dict in
`Tests/unit/test_gain_bundle_labor_families.py:70-77`. `strategy_runner.py:227-230` states the
stakes: "A dict left off that list is not a refusal, it is a virtual placement advancing the REAL
warehouse."

## What to build

An `AisleLedger` owning all eleven dicts -- `Warehouse/inventory/aisle_ledger.py`, or beside
`BinKey` in `inventory_common.py`. Interface:

- `seed(warehouse, affinity, orders, wp)` -- today's two `init_*` methods
- `add(aid, sku, x_phys)` / `drop(aid, sku, x_phys)` -- ONE adjacent pair
- a read view: `demand(aid)`, `pick_load(aid)`, `members(aid)`, `holds(aid, sku)`
- `copy()` / `view()` -- absorbs `Inbound/gain.py`'s `AISLE_COPIERS` and `AISLE_VIEWS`
  (`:400-431`) and the import-time cross-check at `:425-430`
- `reconcile(warehouse)` -- asserts `demand_sum[aid] == sum(product[s] for s in members(aid))`
  and the six siblings. **The invariant becomes assertable for the first time.**

The manager holds one `self.ledger`; `_execute_placement` calls `ledger.add(...)`; **policies read
and stop writing.**

**Byte-identity is available and required.** `_aisle_demand_sum` is a running float sum whose
value depends on accumulation order, so `add`'s body must be today's block verbatim with the names
bound once. Every copy already commits under the same `if sku not in aisle_sku_sets[aid]` guard at
the same point in `take`, so the order does not move.

**Decide here, do not assume:** whether the seven priced quantities should be running sums at all,
or derived on read from `members(aid)` and the per-SKU products. Derive-on-read makes the drift
unrepresentable but changes the hot path. This is the fog item the map names.

## Verification

- The three placement equivalence files, exact-float, ~4 s. Byte-identical is the bar.
- `run_digest.py --self-test` then a baseline-vs-candidate digest on DB rows. Never on PNGs
  (memory `figures-are-not-byte-reproducible`).
- A new `reconcile()` assertion at the end of a drain in an existing integration test.
- Gates 1, 2, 7, 10.

## Comments

Unblocks ticket 03 (`ledger_terms` on the policy record) and makes ticket 18 re-judgeable -- it
removes roughly 12 of `ReorderMixin`'s ~50 manager-attribute reaches in one move.
