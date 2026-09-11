# Exempt repack rows and pin the unload constant

Type: task
Status: resolved

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

## Answer

**BUILT and live on `develop`** — `fe80ed92` (the two changes plus their tests), `3c54866b`
(the regenerated architecture layer). Both changes landed in the ticket's order, and the order
mattered for the reason the ticket gave: the constant-C check prices repack rows, so a check 5
that FAILed every one of them would have masked it.

### 1. The check-5 exemption

Exempted by **(role, event_type) PAIR**, not by type:

        (role = 'put'     AND event_type <> 'put')
     OR (role = 'receive' AND event_type NOT IN ('receive','repack'))
     OR (role NOT IN ('put','receive') AND event_type IN ('put','receive','repack'))

A PUT row typed `'repack'` therefore still FAILs, and `repack` joins `put`/`receive` in the
third clause as a word no foreign row may claim — so the exemption **widens the vocabulary
rather than weakening the clause**, which is the distinction between this and the
`(role='receive') != (event_type='receive')` form the module docstring rejects. The check-5
paragraph is rewritten to state the rule the code now enforces, with the reason for each of
the two exemptions beside it.

**A third spelling of the same wrong rule was found and fixed.** The ticket named two (the SQL
and the module docstring); `sim_semantics.py`'s `work_events.event_type` note also said *"for
put/receive it equals role"* flat out. It is now amended to carry the one declared exception.
Changing a `Col` note moves no `schema_id` (the id is derived from the DDL), and nothing
asserts on that string — checked before editing.

### 2. Constant C — and it HOLDS

Built as check 6 in `reconcile()`, through `_unload_price`, with all five of 15's decisions.
Four deviations and additions, each under force:

1. **`sku_scores` is read through `reconcile`'s own IMMUTABLE connection, not through
   `load_sku_scores`** — that loader opens a plain `mode=ro`, which mints `-wal`/`-shm` beside
   an archived DB and cannot remove them (`wal-sidecars-come-from-readers`), and archived runs
   are exactly this tool's subject. `load_receive_events` is used as the ticket directs; its
   own open is already immutable.
2. **The check is PER RUN, looped over `SELECT DISTINCT run_id`**, because `per_item` and
   `intercept` come from the run's pick config and two runs in one file may legitimately price
   differently. That is also what makes `reconcile(path, run_id=None)` honest.
3. **`sku_scores.run_id` is declared alongside the ticket's two reads.** The ticket named
   `sku` and `handle_var`; the filter touches `run_id` too, and both other tables in
   `SEMANTIC_USES` declare theirs. `work_events.sku` and `work_events.qty` are declared for
   the same reason — they reach this module through `receive_event_frame`.
4. **A row with a NULL `duration` or `qty` is counted and reported, not failed.** A NULL
   duration already shortens check 1's sum against `recv_seconds` and `recv_rows` refuses a
   non-positive qty at the writer, so failing here as well would give one defect two red
   checks. It also stops `reconcile` — which promises never to raise on content — throwing a
   TypeError on a vintage that stored one.

**The verdict: it holds.** Measured over the runs on disk, not argued:

| run | receive rows | C | spread |
|---|---|---|---|
| `comparison_20260910_173151`, fulfillment arms | 505,177 | 5.100000 s | 4.4e-15 |
| `comparison_20260910_173151`, store arms | 70,253 | 7.600000 s | 2.8e-13 |
| `comparison_20260906_114653` .. `comparison_20260909_204522` | — | — | all PASS |

and C is **independently derived**, not merely self-consistent:
`UnloadCost.from_putaway(PutawayCost.from_pick(_build_pick_cfg(REGRESSION_CONFIGS[0])))` gives
`per_item + intercept` = 7.6 exactly, which is what the store arms record. That derivation is
an assertion in the test, because a check that only proved internal consistency would pass a
dock built entirely from class defaults — which is the next finding.

### The finding: check 6 partitions the archive by ERA, sharply

Every run **before the per-item charge break** (`fc7a46a5`, 2026-09-05) FAILS by **~4,600 s**.
On a 2026-09-01 arm a unit costs **1.03 s** to unload while its `handle_var` alone is
**1.79 s/unit**: that era built the dock from `UnloadCost`'s CLASS DEFAULTS rather than from
the run's pick config. That is `per-item-charge-hard-break` observed from the receiving side
for the first time, and the "second set of magic numbers" `UnloadCost`'s own docstring was
written against, realized.

**It stays red, and that is the decision this ticket did not have to escalate.** The module's
own ethic condemns a check that cannot tell a healthy state from the defect it is for — the
retired contiguity check is a monument to it. This one separates them by eighteen orders of
magnitude, 1e-15 against 4.6e+03, so there is no ambiguity to protect a reader from, only a
cause to name. The CLI therefore prints the explanation **once in the summary**, not per arm
(sixteen copies read as a broken tool in their own right), and the module docstring records
the boundary with the measurement.

### Three more findings

- **The e2e fixture made check 6 vacuous, and the sabotage is what proved it.** The 6-batch
  arm the rest of the file uses unloads **7 packs, all one SKU at qty 1** — and the residual
  cancels `qty * handle_var`, so on a single (sku, qty) it is constant *however the price was
  computed*. Zeroing that SKU's handle term left the check **green**. The three constant-C
  tests run on a 25-batch `varied_arm` (420 packs, 16 distinct handle terms, 2 quantities) and
  the **non-vacuity is asserted first**, so the fixture cannot drift back.
- **`C_store != C_ful` today, by construction.** The two channels run different pick configs
  (store intercept 15, fulfillment 10), so the per-leaf docks price at 7.6 s and 5.1 s. 15's
  cross-leaf clause is therefore a claim about a SITE dock having ONE price list — not
  something a coupled run satisfies for free, and not a test that can be written before that
  is settled. Recorded in the module docstring and graduated to the map's fog.
- **Check 1 fails four archived arms for a reason that is not a defect.** `_TOL` is an
  ABSOLUTE 1e-6 s, and on a 505,177-row arm the two sums accumulate `1.3e-6 s` apart over
  `4,177,040.9 s` — a relative error of 3e-13, pure float re-association. Pre-existing (checks
  1–4 are untouched by this commit) and left alone: absolute-versus-relative tolerance is a
  decision, not a build step. Graduated to the map's fog.

## What proved it

- **Check-5 exemption, all four rows asserted together**: a planted `('receive','repack')`
  PASSES; `('put','pick')`, `('receive','put')` and `('put','repack')` all still FAIL. The
  fourth is the pair-not-type distinction and is not in the ticket's list.
- **Constant C, three sabotages, each on its own copy** so none can mask another: one row's
  duration moved by `2 x _TOL` → FAIL; one SKU's `handle_var` zeroed → FAIL, with every check
  that does not read `sku_scores` still green; one SKU deleted from `sku_scores` → the
  DISTINCT `every_received_sku_is_scored` FAIL while `unload_price_is_constant` stays TRUE.
- **Inactive is not green**: an emptied `sku_scores` leaves BOTH new keys absent from `checks`
  (not `True`), `verdict` PASS, `active` still True, and `unload_note` set.
- **Gates**: 2037 unit, 786 integration, 35 e2e (the whole tier, 1 skipped), all **nine
  verifiers** green after the arch chain. `Tests/architecture` stands at its known four reds
  plus the `.claude/worktrees/` fifth — baselined against a `git archive HEAD` copy, which
  shows the same four and **nothing attributable to this change**.
- **No preflight canary owed and none paid**: nothing here moves a run-tree path or a declared
  shape, and `preflight --check` / `contract --check` were green throughout.

**One durable fact is worth a memory and is not written**: `per-item-charge-hard-break` now
has a DETECTOR — which side of `fc7a46a5` a run's receiving sits on is answerable in one
command, `python Diagnostics/receiving_report.py <run>`. It belongs as an amendment to that
memory rather than as a new one.
