# 02 - the aisle ledger has no module

Type: refactor
Status: claimed
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

## Design note -- the ten commit blocks, read (2026-09-16)

Read before building. They differ in FOUR ways, not just in which dicts they touch, and a
single uniform `add()` would silently flatten two of them.

**1. Per-BIN state vs per-SKU state, and the guard is the line between them.**
Six blocks (`:345`, `:410`, `:689`, `:1208`, `:1563`, `:1821`) put the idx-set add INSIDE
`if sku not in aisle_sku_sets[aid]`. Four (`:833`, `:1031`, `:2158`, `:2693`) put the idx add
AND `aisle_member_pos[aid][idx].append(x_phys)` OUTSIDE it. That is not drift -- it is correct:
`member_pos` tracks live bin COLUMN POSITIONS, so it must record every bin, while the priced
sums are SKU-once. It mirrors `_drop_sku_from_aisle`, which drops a member_pos entry for every
bin but subtracts the sums only on the SKU's last one.

So the interface is **two methods, not one**:

    ledger.add_bin(aid, sku, idx, x_phys)     # every placement -- member_pos, idx set
    ledger.add_sku(aid, sku, ...)             # first bin of a SKU in that aisle -- the sums

Collapsing these into one `add()` is the trap. Keep the guard where each caller has it.

**2. Which sums, per family.** lift only (`:345`, the dead `load_*` family, deleted by ticket
03); demand only (`:410`, `:689`, `:833`, `:1031`, `:1208`, `:2158`, `:2693`); demand +
pick_load + vol (`:1563`, `:1821` -- the two `rank_cartlabor` halves). This is exactly the
`ledger_terms` field ticket 03 wants; derive it, do not hand-list it a second time.

**3. `f_s * q_s` vs `fq` -- a float-order hazard, not a style difference.** Some blocks
accumulate `+= f_s * q_s` and others `+= fq` where `fq` was computed earlier. Same value only
if computed identically. `add_sku` must take the ALREADY-MULTIPLIED scalar from the caller
rather than recomputing it from `f_s` and `q_s`, or the running sum changes in the last bits
and the equivalence oracles move.

**4. The cart term is dual-written, in lockstep.** `:1563` and `:1821` both do
`vol_load[best_aid] += m_s` (the pool's LOCAL copy, read by `_score_of` within the drain) AND
`aisle_vol_sum[best_aid] += m_s` (the manager's). The comment calls it "SKU-once, in lockstep
with pick_load_sum". The ledger owns the manager's copy; the pool's local `vol_load` is a
drain-scoped view and should stay with the pool, or `_score_of` gains an indirection on the
hot path. Decide this explicitly rather than absorbing both.

**Consequence for the byte-identity claim.** The equivalence oracles will NOT catch a mistake
here on their own -- ticket 01 established that they pin pool-vs-wave agreement, and a ledger
change moves both halves together. `run_digest.py` DB-row neutrality against the toy-run
baseline is the instrument that can actually fail.

### Two more constraints, from the seed and the manager-side writer

**`init_lift_state` does TWO jobs and only one of them is the ledger's.** Besides seeding
`_aisle_sku_sets` / `_aisle_idx_sets` / `_aisle_sku_counts` / `_aisle_member_pos` /
`_aisle_lift_sum`, it also clears and rebuilds `_bin_sku`, `_current_quantities`,
`_sku_singleton_bins` and `_sku_pallet_bins` -- none of which are aisle state. So
`AisleLedger.seed()` is NOT `init_lift_state` renamed; the method has to be split, with the
non-aisle rebuild staying on the manager. Getting this wrong would move `_current_quantities`
(which every reorder decision reads) behind a ledger interface it has no business being in.

**The two writers split on a line nobody drew deliberately.** `_execute_placement`
(`Inventory_Management.py:869-872`) commits ONLY `_aisle_sku_counts` -- the per-bin count.
Every set and every sum is committed by the POLICY, in its own copy of the block. So the
manager maintains the count half and the policy maintains the membership-and-price half, and
neither knows the other exists. That is the actual mechanism behind both drifts: a policy that
forgets a sum leaves the count right and the price wrong, which reads as a working warehouse.

Consequence for the interface: `add_bin` is the manager's call (it already has the hook), and
`add_sku` is the policy's. The guard `if sku not in ledger.holds(aid)` stays at the call site
because four of the ten blocks legitimately need work on both sides of it.

## Progress -- STAGE A landed, stage B outstanding (2026-09-16)

**This ticket is NOT resolved.** The drop half is done; the add half is not.

### What landed

`Warehouse/inventory/aisle_ledger.py` -- `AisleLedger` owns all eleven dicts.
`Inventory_Manager` holds `self.ledger`. The eight AISLE dicts are bound as **direct aliases**
(never rebound, read on the hot path, so an indirection there would be a per-candidate cost);
the three per-SKU PRODUCT dicts are **properties with setters**, because `init_demand_state`
rebinds them and a plain alias would silently detach at that point -- leaving `reconcile()`
comparing a level against products nobody wrote.

**Both drop twins now share one body.** `_drop_sku_from_aisle` and the loop inside
`_reclaim_empty_bins` call `ledger.drop_bin` + `ledger.drop_sku`. The KEEP THE TWO IN SYNC
instruction that produced ticket 01's defect is deleted, not restated.

`reconcile()` returns every level that no longer equals the sum of its members' products;
`assert_sound()` is the assertion form. `Tests/unit/test_aisle_ledger.py`, 10 tests.

### Three exactness hazards this ticket did not anticipate

1. **The lift delta is computed AFTER the index discard.** The original ran
   `2.0 * delta_lift_idxs(sku, idx_sets[aid])` once the idx was already removed, so a
   precomputed scalar would be taken against a different set and change the number.
   `drop_sku` therefore takes a CALLABLE, invoked with the post-discard set -- which also
   keeps the module free of any affinity knowledge. Pinned by a test that captures the set the
   callable sees.
2. **The two copies really did differ, and the difference was only in prose.** The cold path's
   `elif n == 1` ignores a defensive `n == 0`; the hot twin's bare `else` treats it like
   `n == 1`. That is now the explicit `last_when_zero` flag, with a test for each side.
3. **The lift assignment was unconditional and that matters.** `lift_sum[aid] = max(0.0, ...)`
   also MATERIALISES the defaultdict entry. A `if delta:` guard would stop creating keys that
   `aisle_metrics` later reads, so the assignment stays unconditional.

### Verification

| check | result |
|---|---|
| toy run vs baseline, `run_digest.py` | **IDENTICAL** on the comparable surface, 136 arms |
| `Tests/unit/test_aisle_ledger.py` | 10 passed |
| `Tests/unit -k "not gpu"` | 2648 passed, 1 skipped -- same count as before the change |
| the three placement equivalence files | 95 passed |
| gates 1-5, 7-10 | green |
| gate 6 | RED, unchanged message -- pre-existing, `architecture-drift/issues/04` |

### Stage B -- what remains before this ticket resolves

- The **ten add-side commit blocks** in `Assignment_Functions.py` still maintain the sets and
  sums by hand, each a different subset. `add_bin` / `add_sku` exist in the design note above
  but are NOT yet built or called. This is the larger half and the one that needs
  `ledger_terms` from ticket 03 to land cleanly.
- `_execute_placement` still commits `sku_counts` directly rather than through `add_bin`.
- `AISLE_COPIERS` / `AISLE_VIEWS` in `Inbound/gain.py` and `_gain_bundle_for`'s per-family
  `aisle_state=` lists are untouched; they collapse once the add half moves.
- The open design question stands: whether the priced levels should be running sums at all or
  derived on read from `members(aid)`. Stage A deliberately did not decide it -- it preserves
  today's accumulation order exactly, which is what made the digest identical.

### One thing worth a second look

The reclaim loop's hoisted locals were justified by a docstring reading "across ~7k
iterations". The SAME docstring says the loop is now "typically a handful per batch" -- the 7k
is stale prose from the O(total_bins) full-scan era it replaced. I removed the hoists for the
ledger calls on that basis and the digest is identical, but identical is not *fast*: if the
per-call cost matters it is a calltree measurement, not an argument.
