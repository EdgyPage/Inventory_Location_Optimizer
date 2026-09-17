# 16 - the inbound objective restates the cost model the simulation bills

Type: refactor
Status: needs-triage
Blocked by: 02

## Context

The objective the inbound gain arms optimize is a **second implementation** of the cost model the
simulation charges. It shares the kernel primitives (`per_pick`, `height_multiplier`, imported at
`Inbound/gain.py:129`) but restates the formula, and nothing joins the two.

What the simulation bills, `Warehouse/operations/putaway.py:98-119` (`put_cost`, called from
`Inventory_Management.py:1342`):

```
travel + M(y) * (intercept + qty*per_item + qty*var)
```

What the arms optimize, `Inbound/gain.py:836-878` (`_cost_at`, the put term at `:869`):

```
put = ps.x_pace * x + ps.y_pace * y
```

No intercept, no per-item charge, and **no height multiplier** -- which is bin-dependent and
therefore does not cancel out of a gain difference between two candidate placements.

**This is not a bug claim.** `gain.py:77` records the simplification as a decision ("put travel is
paid once at the put crew's speeds"). The claim is that **whether it is a decision or drift is
undecidable from the tests.** All 57 `def test_` in `Tests/unit/test_gain_plan.py` validate the
evaluator against a naive rebuild of itself (`:706`) or a closed form of its own formula (`:746`).
Not one references `put_cost`, `Pick._pick_time`, or `Warehouse.operations.putaway`.

The formula has moved three times in two months -- ADR-0001 (the per-item charge, "a HARD BREAK for
every pick/put/labor number"), the 2026-08-24 one-clock refactor, ADR-0003 (put-away fills empty
bins first). Each changed what a put costs. Nothing would have told you whether the gain arms moved
with it.

## What to build

**(a) Give the objective a seam onto the billed cost.** Extract from `Warehouse/kernel/cost_model.py`
(or `putaway.py`) a pure `put_seconds_at(x, y, unit, wp, speed, cost)` -- and its pick counterpart
-- that BOTH `Inventory_Manager._place` and `_Evaluator._cost_at` call. The evaluator keeps
everything it owns (the visit expectation, the window-rate substitution, the moments, the
exhaustion penalty) and stops owning the at-bin expression. **Where the objective deliberately
drops a term, it drops it visibly, as a named argument, not by not writing it.**
`Inbound -> wh_kernel` is already a legal edge (`gain.py:129-131`).

**(b) Split the file, separately.** `Inbound/gain.py` is 1,398 lines and is four modules:
`:227-398` the copy-on-write views (~170 lines, two dedicated equivalence test files), `:400-730`
the owner resolution (~330), `:731-1236` `_Evaluator` + `plan_order` (~500), `:1303-1398` the
entries. Deletion test on the first two: deleting the CoW module brings back a measured 37.8x copy
blow-up at every `_make_pool`; deleting the bundle trio brings `if coupled:` back into the pricing
path, which `gain.py:57-60` says explicitly it exists to prevent. Both earn their keep -- they are
just not the same module as the evaluator.

**The template is in this repo already.** `Inbound/unload.py:60-71` (`UnloadCost.from_putaway`)
makes exactly this argument: a reference makes the drift "structurally impossible rather than merely
a diff someone might notice". The gain evaluator is the one costing path that did not get it.

## Verification

- The missing test becomes writable: hand the same unit and bin to `put_cost` and to the
  evaluator's put term, assert the documented relationship -- equal, or equal minus the named
  dropped term. Today no test can fail when the two diverge.
- Byte-identity: if the unification changes any number, that is a finding, not a refactor. Prove
  DB-row neutrality with `run_digest.py` or stop and record what moved.
- Gates 1, 2, 10.
