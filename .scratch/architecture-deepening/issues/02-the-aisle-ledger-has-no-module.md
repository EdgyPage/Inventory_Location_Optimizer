# 02 - the aisle ledger has no module

Type: refactor
Status: resolved
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

## Progress -- STAGE A landed (2026-09-16); see the stage B section at the foot

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
`assert_sound()` is the assertion form. `Tests/unit/test_aisle_ledger.py`, 9 tests.

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
| `Tests/unit/test_aisle_ledger.py` | 9 passed |
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

---

## STAGE B landed (2026-09-17) -- the add half is one body

### What landed

`add_sku` / `add_bin` / `count_bin` on `AisleLedger`, and **every** commit block in
`Assignment_Functions.py` now calls them. There were ELEVEN, not the ten this ticket counted:
`_cluster_map_commit` was a twelfth writer hiding as a helper, and `_commit_aisle` was another.
Both are deleted; nothing in the placement module writes an aisle book directly any more.

The three historical shapes collapse into one pair of calls:

| shape | families | what it maintains |
|---|---|---|
| A | travel / cohesion, ranked wave + pool | `demand_sum`, idx inside the guard |
| B | co-demand x2, minlabor x2, cluster_map | `demand_sum` + `member_pos` outside the guard |
| C | `rank_labor`, `rank_cartlabor` | `demand_sum`, `pick_load_sum`, and `vol_sum` when the cart is on |

**`None` is not 0.0, and that is the whole design of `add_sku`.** A level a family does not
maintain is omitted, not passed as zero: passing zero would `+=` into a `defaultdict` and
MATERIALISE a key the family never had, adding rows to `aisle_metrics`. A level the family
does maintain is passed even when its value is 0.0, exactly as `+= splp.get(sku, 0.0)` did.
This is stage A's third hazard running in the opposite direction.

### The decision this ticket left open

**Running sums stay; derive-on-read is refused.** The levels are read inside the scoring
expression on every candidate (`aisle_demand_sum[aid] + f_s * q_s` in the travel tie-break,
`load`/`vol_load` in `_score_of`), so deriving on read turns an O(1) dict read into a sum over
the aisle's members -- inside a loop whose width is the live aisle count and grows with the
catalogue (`k = 1.963` against the ladder knob, measured on `_RankedAssignPool`). What makes
the drift unrepresentable is not derive-on-read; it is that the add and the drop now live in
one module with `reconcile()` between them.

### Why no signature moved

Deliberate, and it is the reason the oracles still mean something. The three placement
equivalence suites carry FROZEN HAND-COPIES of these impl bodies and call
`_ranked_assign_impl` / `_TravelBalancedPool(...)` directly with the same positional dicts.
Changing a signature forces the oracle to be rewritten, which re-freezes it against the very
change it exists to check. So each function binds an `AisleLedger.over(...)` to the dicts it
was ALREADY HANDED, and writes through that. The copies inside `Tests/` are not duplicates to
delete -- they are the reference.

`over()` also carries the seam the gain evaluator needs: it BINDS rather than copies, so a
pool opened over `Inbound/gain.py`'s copy-on-write wrappers writes into those and not into the
real warehouse, unchanged from before.

### `bound` -> `maintained_levels()` -- ticket 03's `ledger_terms`, derived

A ledger records which books it was handed. A policy's ledger therefore KNOWS which levels its
family maintains, without anyone listing them a second time. `reconcile(levels=...)` takes that
list, and the per-arm integration assertion reads it off the pool rather than off a table.
This is what ticket 03 wants for `PlacementPolicy.ledger_terms`; it should consume this, not
restate it.

### A finding, filed as ticket 20

Pointing `reconcile()` at a real manager per arm immediately reported 65 of 65 aisles drifted
on `pick_load_sum` and `vol_sum` for `cluster_map`. Measured on stage A and stage B: identical,
and present BEFORE any drain. `init_demand_state` prices all three levels; the arm then
maintains only the ones it scores on, so two of them are stale for the whole run on 15 of the
17 arms. Dormant today (no reader outside the writer/loader pair for the persisted one), which
is exactly what `vol_sum` was before `rank_cartlabor` started reading it. Ticket 20 holds the
measurement and the three ways out.

### Verification

| check | result |
|---|---|
| toy run vs baseline, `run_digest.py` | **IDENTICAL** on the comparable surface, 136 arms |
| the three placement equivalence files + the pool/index suites | 148 passed |
| `Tests/unit -k "not gpu"` | 2,671 passed / 1 skipped -- unchanged count |
| `Tests/unit/test_aisle_ledger.py` | 19 passed (9 -> 19) |
| the source ratchet, on planted damage | fails and names the line |

The digest baseline was `comparison_whatif_20260916_223315` (ticket 12's commit), so IDENTICAL
also clears the six commits in between -- tickets 09, 10, 19 and their arch re-syncs.

### `over()` was a cost class, and it was measured rather than assumed

Binding a ledger per pool looked like an allocation nobody would notice. It is not: the gain
evaluator rebuilds the arm's policy for EVERY VIRTUAL PLACEMENT, at 12.59 pool opens per
placement, so anything on that path is paid O(yard^2) times per drain.

| form | per call | as a share of one pool open |
|---|---|---|
| `**books`, `cls()`, ten `setattr`s | 2.00 us | **23.4%** |
| spelled-out keywords, `__new__`, a shared unbound sentinel | 0.29 us | **4.2%** |

Where the 2.00 us went: 0.48 us allocating ten dicts of which seven were thrown away,
0.36 us building `bound` as a frozenset, 0.23 us validating with `set(books) - set(BOOKS)`,
the rest kwargs packing and ten `setattr`s. The fix is all three at once -- the signature is
spelled out (free validation, no kwargs dict), `bound` is derived on read, and a book the
caller leaves out binds ONE shared `_UnboundBook` instead of a fresh dict.

The sentinel also improved the failure. A private empty dict ABSORBS a write to a book the
family was never handed -- no error, and the number lands where no reader can follow it,
which is the shape of every defect this module exists for. `_UnboundBook` refuses the write
and says why; `.get()` still answers, so `reconcile()` works on a ledger holding three books.

This is the repo's own rule applied to itself: cost class before count, and "measured, and it
was nothing" is a different statement from "never measured". Here it was not nothing.

### The gain-path hazard, mostly closed

`_gain_bundle_for`'s warning was: *"A dict left off that list is not a refusal, it is a
virtual placement advancing the REAL warehouse."* Two things now stand between that and the
code:

1. **A pool can only write books it was handed.** Its ledger binds exactly what the factory
   passed out of `state`, and every other book refuses. So a family's write set is bounded by
   its wiring rather than by a comment.
2. **`AisleLedger.POLICY_BOOKS` vs `Inbound.gain.AISLE_VIEWS`, checked at import** in
   `strategy_runner` -- the only module that may see both vocabularies, since `Inbound/` must
   not import the placement engine. A writable book with no copy-on-write view now refuses to
   load, instead of being discovered during a campaign.

### Resolved, with one piece split off

**Ticket 21** carries the remainder: the pool builders still take the aisle books as separate
positional parameters, so `strategies.py`, `_gain_bundle_for`'s `aisle_state=` dicts and
`test_gain_bundle_labor_families.FAMILIES` each restate the same list. Narrowing those
signatures means re-freezing the placement oracles, so it rides ticket 04's pass, which has to
re-freeze them anyway. What is left there is duplication; the danger is closed (see above).
