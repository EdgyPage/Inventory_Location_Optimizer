# Exempt repack rows and pin the unload constant

Type: task
Status: open

AFK. The execution override (map Notes) graduating two changes out of
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md) — its finding,
and the within-leaf half of its section 3. No decision is open here; both are settled in that
ticket and this one adds none.

**Both are takeable today and need no second leaf.** One is byte-identical outside a feature whose
coefficient is `assumed` 0.0; the other adds a check and changes no simulation byte at all. Same
shape as [Harden the three positional seams](11-harden-the-positional-seams.md),
[Seat the put-pool injection seams](12-seat-the-put-pool-seams.md),
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md) and
[Close the torn-finalize window](16-close-the-torn-finalize-window.md), all of which went in ahead
of the thing they serve.

They are paired deliberately. The check-5 fix alone is a three-line clause that no run exercises;
the constant-C check is testable against every inbound-on run already on disk, which is the only
way to learn whether the invariant holds before coupling comes to depend on it.

## Question

Two changes in `Diagnostics/receiving_report.py`, in this order.

### 1. Check 5 must exempt `event_type='repack'`

`repack_rows` (`Optimization/metrics/work_events.py:186-207`) states its design outright: a repack
is receiving work by the receiving crew at the dock's own per-pack price, so **`role` is
`'receive'` and only `event_type` differs** — deliberately, so that "what did receiving cost" sums
`role='receive'` and gets unloads AND rework, while "how much rework was there" filters
`event_type='repack'`.

Check 5's SQL says the opposite:

    (role IN ('put','receive') AND event_type <> role)

A repack row trips it. Every rework row reads as a mismatch and the arm reads FAIL.

It has never fired: `f_repack` is `assumed` 0.0 (`Optimization/simconfig/staffing.py:846`) and
ADR-0003's own-bin rung and rescues only engage once the free index is dry, which a warehouse sized
to its declared levels never reaches. So this goes red on the **first honest run of the feature**,
by which point a reader has been trained that check 5 is sound.

Exempt it the way pick rows are already exempted, and put the reason in the comment beside them:
picking is exempt because it has a vocabulary of its own; `repack` is exempt because it is the one
`role='receive'` event type that is declared to differ. Do not weaken the clause to
`(role='receive') != (event_type='receive')` — the module docstring records why that form was
rejected (it sails past a put row typed `'pick'`).

Update the check-5 paragraph in the module docstring; it currently states the rule the code
enforces, and both are wrong in the same way.

### 2. The unload price is one constant, and the check pins it

15 section 3. Every receive row must satisfy

    duration - qty * sku_scores.handle_var(sku)  ==  C

for one constant C, because
`unload_cost = per_item + intercept + qty * v_s` (`Inbound/unload.py:119`,
`Warehouse/kernel/cost_model.py:120`) and `v_s` is already in the sim DB as
`sku_scores.handle_var` (`Optimization/persistence/Picking_Data.py:720`) — `UnloadCost.from_putaway`
takes its weight/volume coefficients from `PutawayCost.from_pick`, which carries the pick
coefficients through unchanged, and `Warehouse/catalog/Order.py:269` computes the stored value from
the same `handle_var` on the same inputs. **No catalogue, no config record, no fit.**

Build it as a sixth check in `reconcile()`, with 15's five decisions:

1. **Tolerance `_TOL`**, reused — the recompute re-associates the float and is not bit-exact
   (relative error around 1e-14 on a ~50 s duration).
2. **C is fixed by spread, not against a reference**: `max(c) - min(c) <= _TOL`, with C itself
   reported in `--verbose` beside the uid blocks.
3. **Repack rows are INCLUDED, not filtered.** `_charge_repack`
   (`Warehouse/inventory/Inventory_Management.py:1229`) prices a rescue with `dock.unload_seconds`
   — the same `unload_cost` — so repacks satisfy the same C. This makes the check the only thing in
   the repo that verifies that docstring's central claim, *"no new coefficient enters the model"*.
   It is also why change 1 lands first: a check that FAILs every repack row would mask this one.
4. **Empty `sku_scores` makes the check inactive**, the idiom the tool already uses for a run with
   no receiving crew — not a FAIL, and not a silent PASS on a vintage that cannot answer.
5. **A receive row whose SKU is absent from a non-empty `sku_scores` is a FAIL.** A run that
   unloaded a SKU it never scored is itself the defect. This is the one clause that can fail for a
   reason outside receiving; say so in its message.

`load_receive_events` (`Picking_Data.py:3048`) and the `receive_event_frame` query (`:1711`) are
this check's inputs and have had **zero callers** since they were written — their four columns
`(batch_id, sku, qty, duration)` are exactly what it needs. Use them rather than a fresh inline
query; that is what they were written for, and a second query would be a second thing to keep in
step with the DDL.

The cross-leaf clause (`abs(C_store - C_ful) <= _TOL`) is **out of scope here** — it needs two
leaves and lives in the map's fog with the rest of 15's site-scope work.

Declare the two new reads in `SEMANTIC_USES`: `sku_scores.sku` and `sku_scores.handle_var`, both
`'read'`. `handle_var` is tagged `SCORE` (`Optimization/persistence/sim_semantics.py:410`), so a
`'sum'` declaration would be refused by the gate — correctly; this check never sums it. The literal
is AST-read by `Tests/architecture/test_column_semantics.py`, so it must stay a pure literal.

## What proves it

- The check-5 exemption: a planted repack row (`role='receive'`, `event_type='repack'`) passes,
  while a put row typed `'pick'` and a receive row typed `'put'` both still FAIL. Assert all three,
  or the exemption is indistinguishable from deleting the check.
- Constant-C: run it over an inbound-on run already on disk and record whether it holds. **If it
  does not, that is the finding and the build stops** — the invariant is derived from the cost
  chain, so a spread means the chain is not what the code says it is.
- Mutation sabotage: perturb one row's duration by 2 x `_TOL` and confirm FAIL; zero one SKU's
  `handle_var` and confirm FAIL; drop a SKU from `sku_scores` and confirm the distinct
  missing-score FAIL rather than a spread FAIL.
- `Tests/e2e/test_receiving_e2e.py` already imports `reconcile`, so the new check is in a gate from
  the moment it exists. Keep it that way — memory `hand-run-test-tiers-rot-silently`.
- Nine verifiers green. No preflight canary is owed: nothing here moves a run-tree path or a
  declared shape.
